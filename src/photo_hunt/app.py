import asyncio
import contextlib
import io
import json
import logging
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import openai
import segno
from fastapi import FastAPI, Header, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from starlette.websockets import WebSocketDisconnect

from photo_hunt.config import Settings, VisionMode
from photo_hunt.game import Game, GameError, Phase, Player, Submission
from photo_hunt.vision import FakeChecker, OpenAIChecker, PhotoChecker, decide, detect_image_type

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
HOST_PUSH_INTERVAL_S = 0.25
WS_AUTH_TIMEOUT_S = 10.0


class Song(BaseModel):
    title: str = Field(max_length=120)
    item: str = Field(max_length=60)
    lyric: str = Field(default="", max_length=200)
    file: str | None = Field(default=None, max_length=200)
    stop_at: float | None = Field(default=None, ge=0)


class JoinIn(BaseModel):
    name: str = Field(max_length=100)


class MusicIn(BaseModel):
    item: str = Field(max_length=200)
    lyric: str = Field(default="", max_length=500)
    advance: int


class HuntIn(BaseModel):
    duration_s: float = Field(default=45, gt=0, le=600)


class DecideIn(BaseModel):
    accept: bool


def load_songs(path: Path) -> list[Song]:
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8")
    try:
        return [Song.model_validate(s) for s in _json_list(raw)]
    except (ValidationError, ValueError) as exc:
        raise ValueError(f"songs file is invalid: {path}") from exc


def _json_list(raw: str) -> list[Any]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("songs file must contain a list")
    return data


class Hub:
    """Рассылка состояния по WebSocket. Ведущему шлём не чаще раза в 250 мс."""

    def __init__(self) -> None:
        self.players: dict[str, set[WebSocket]] = {}
        self.hosts: set[WebSocket] = set()
        self._host_pending = False

    async def _send(self, ws: WebSocket, data: dict[str, Any]) -> bool:
        try:
            await ws.send_json(data)
        except (WebSocketDisconnect, RuntimeError, OSError):
            return False
        return True


def _seconds_left(game: Game, now: float) -> float | None:
    if game.phase != Phase.HUNT or game.round is None or game.round.deadline is None:
        return None
    return max(0.0, round(game.round.deadline - now, 1))


def player_view(game: Game, player: Player, now: float) -> dict[str, Any]:
    rnd = game.round
    show_item = game.phase in (Phase.HUNT, Phase.RESULTS, Phase.FINISHED)
    sub = rnd.submissions.get(player.id) if rnd else None
    winner = None
    if game.phase == Phase.FINISHED and game.last_survivors:
        winner = game.players[game.last_survivors[0]].name
    return {
        "type": "state",
        "phase": game.phase,
        "round_no": game.round_no,
        "alive_count": len(game.alive_players()),
        "advance": rnd.advance if rnd else None,
        "item": rnd.item if rnd and show_item else None,
        "lyric": rnd.lyric if rnd and show_item else None,
        "seconds_left": _seconds_left(game, now),
        "winner": winner,
        "me": {
            "name": player.name,
            "alive": player.alive,
            "verdict": sub.verdict if sub else None,
            "reason": sub.reason if sub else "",
            "place": game.place_of(player.id),
        },
    }


def host_view(game: Game, now: float, join_url: str) -> dict[str, Any]:
    rnd = game.round
    subs: list[Submission] = sorted(
        rnd.submissions.values() if rnd else [], key=lambda s: s.received_at
    )
    started = rnd.started_at if rnd and rnd.started_at is not None else now
    return {
        "type": "state",
        "phase": game.phase,
        "round_no": game.round_no,
        "players_total": len(game.players),
        "alive_count": len(game.alive_players()),
        "item": rnd.item if rnd else None,
        "lyric": rnd.lyric if rnd else None,
        "advance": rnd.advance if rnd else None,
        "seconds_left": _seconds_left(game, now),
        "counts": game.counts(),
        "join_url": join_url,
        "submissions": [
            {
                "id": s.id,
                "name": game.players[s.player_id].name,
                "verdict": s.verdict,
                "reason": s.reason,
                "confidence": s.confidence,
                "t": round(s.received_at - started, 1),
            }
            for s in subs
        ],
        "survivors": [
            {
                "name": game.players[pid].name,
                "submission_id": rnd.submissions[pid].id
                if rnd and pid in rnd.submissions
                else None,
            }
            for pid in game.last_survivors
        ],
    }


def build_checker(settings: Settings) -> PhotoChecker:
    if settings.vision_mode == VisionMode.FAKE:
        return FakeChecker(settings.fake_delay_s)
    assert settings.openai_api_key is not None  # noqa: S101 - гарантирует валидатор Settings
    client = openai.AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
    return OpenAIChecker(
        client,
        model=settings.openai_model,
        concurrency=settings.vision_concurrency,
        timeout_s=settings.vision_timeout_s,
        reasoning_effort=settings.openai_reasoning_effort,
    )


def create_app(
    settings: Settings | None = None,
    checker: PhotoChecker | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    checker = checker or build_checker(settings)
    songs = load_songs(settings.songs_file)
    game = Game()
    hub = Hub()
    background: set[asyncio.Task[None]] = set()
    host_token = settings.host_token.get_secret_value()

    app = FastAPI(title="Photo Hunt", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.game = game
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # --- рассылка ---

    async def push_player(player_id: str) -> None:
        player = game.players.get(player_id)
        sockets = hub.players.get(player_id)
        if player is None or not sockets:
            return
        data = player_view(game, player, clock())
        for ws in list(sockets):
            if not await hub._send(ws, data):
                sockets.discard(ws)

    async def push_all_players() -> None:
        await asyncio.gather(*(push_player(pid) for pid in list(hub.players)))

    async def push_hosts_now() -> None:
        data = host_view(game, clock(), settings.public_url)
        for ws in list(hub.hosts):
            if not await hub._send(ws, data):
                hub.hosts.discard(ws)

    async def _delayed_host_push() -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.Event().wait(), timeout=HOST_PUSH_INTERVAL_S)
        hub._host_pending = False
        await push_hosts_now()

    def schedule_hosts() -> None:
        if hub._host_pending:
            return
        hub._host_pending = True
        spawn(_delayed_host_push())

    def spawn(coro: Any) -> None:
        task = asyncio.create_task(coro)
        background.add(task)
        task.add_done_callback(background.discard)

    async def broadcast_all() -> None:
        await push_all_players()
        await push_hosts_now()

    async def verify(submission_id: str, photo: bytes, item: str, player_id: str) -> None:
        result = await checker.check(photo, item)
        decision = decide(result, settings.confidence_threshold)
        if game.apply_check(submission_id, decision.verdict, decision.reason, decision.confidence):
            log.info(
                "photo_checked",
                extra={"submission_id": submission_id, "verdict": decision.verdict.value},
            )
            await push_player(player_id)
            schedule_hosts()

    # --- доступ ---

    def require_host(token: str | None) -> None:
        if token is None or not secrets.compare_digest(token, host_token):
            raise HTTPException(status_code=401, detail="Нужен пароль ведущего")

    def require_player(token: str | None) -> Player:
        player = game.player_by_token(token) if token else None
        if player is None:
            raise HTTPException(status_code=401, detail="Зайди в игру заново")
        return player

    @app.exception_handler(GameError)
    async def _game_error(_: Request, exc: GameError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    # --- страницы ---

    @app.get("/", include_in_schema=False)
    async def play_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "play.html")

    @app.get("/host", include_in_schema=False)
    async def host_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "host.html")

    @app.get("/api/qr.svg")
    async def qr() -> Response:
        buf = io.BytesIO()
        segno.make(settings.public_url, error="m").save(buf, kind="svg", scale=10, border=2)
        return Response(buf.getvalue(), media_type="image/svg+xml")

    # --- игрок ---

    @app.post("/api/join")
    async def join(body: JoinIn) -> dict[str, Any]:
        player = game.join(body.name)
        schedule_hosts()
        return {"token": player.token, "alive": player.alive}

    @app.post("/api/photo", status_code=202)
    async def photo(
        photo: UploadFile, x_player_token: str | None = Header(default=None)
    ) -> dict[str, str]:
        player = require_player(x_player_token)
        data = await photo.read(settings.max_photo_bytes + 1)
        if len(data) > settings.max_photo_bytes:
            raise HTTPException(status_code=413, detail="Фото слишком большое")
        if detect_image_type(data) is None:
            raise HTTPException(status_code=400, detail="Это не фото")
        sub = game.submit(player, data, clock())
        assert game.round is not None  # noqa: S101 - submit проверил, что раунд идёт
        spawn(verify(sub.id, data, game.round.item, player.id))
        await push_player(player.id)
        schedule_hosts()
        return {"submission_id": sub.id}

    @app.websocket("/ws/player")
    async def ws_player(ws: WebSocket) -> None:
        await ws.accept()
        player = await _auth_socket(ws, lambda t: game.player_by_token(t))
        if player is None:
            return
        hub.players.setdefault(player.id, set()).add(ws)
        await push_player(player.id)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            hub.players.get(player.id, set()).discard(ws)

    # --- ведущий ---

    @app.get("/api/host/songs")
    async def host_songs(x_host_token: str | None = Header(default=None)) -> list[dict[str, Any]]:
        require_host(x_host_token)
        return [
            {**s.model_dump(exclude={"file"}), "id": i, "has_music": s.file is not None}
            for i, s in enumerate(songs)
        ]

    @app.get("/api/host/music/{song_id}")
    async def host_music(song_id: int, x_host_token: str | None = Header(default=None)) -> Response:
        require_host(x_host_token)
        if not 0 <= song_id < len(songs) or songs[song_id].file is None:
            raise HTTPException(status_code=404, detail="Нет файла песни")
        # Имя файла берётся только из songs.json, а не из запроса.
        base = settings.music_dir.resolve()
        path = (base / str(songs[song_id].file)).resolve()
        if base not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="Нет файла песни")
        return FileResponse(path)

    @app.get("/api/host/state")
    async def host_state(x_host_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_host(x_host_token)
        return host_view(game, clock(), settings.public_url)

    @app.post("/api/host/music")
    async def host_start_music(
        body: MusicIn, x_host_token: str | None = Header(default=None)
    ) -> dict[str, int]:
        require_host(x_host_token)
        rnd = game.start_music(body.item, body.lyric, body.advance)
        await broadcast_all()
        return {"round_no": rnd.no}

    @app.post("/api/host/hunt")
    async def host_hunt(body: HuntIn, x_host_token: str | None = Header(default=None)) -> None:
        require_host(x_host_token)
        game.start_hunt(clock(), body.duration_s)
        await broadcast_all()

    @app.post("/api/host/decide/{submission_id}")
    async def host_decide(
        submission_id: str, body: DecideIn, x_host_token: str | None = Header(default=None)
    ) -> None:
        require_host(x_host_token)
        sub = game.host_decide(submission_id, body.accept)
        await push_player(sub.player_id)
        await push_hosts_now()

    @app.post("/api/host/accept-review")
    async def host_accept_review(x_host_token: str | None = Header(default=None)) -> dict[str, int]:
        require_host(x_host_token)
        count = game.accept_all_review()
        await broadcast_all()
        return {"accepted": count}

    @app.post("/api/host/finish")
    async def host_finish(x_host_token: str | None = Header(default=None)) -> dict[str, int]:
        require_host(x_host_token)
        survivors = game.finish_round()
        log.info("round_finished", extra={"round_no": game.round_no, "left": len(survivors)})
        await broadcast_all()
        return {"survivors": len(survivors)}

    @app.post("/api/host/reset")
    async def host_reset(x_host_token: str | None = Header(default=None)) -> None:
        require_host(x_host_token)
        game.reset()
        sockets = [ws for group in hub.players.values() for ws in group]
        hub.players.clear()
        for ws in sockets:
            await hub._send(ws, {"type": "reset"})
        await push_hosts_now()

    @app.get("/api/host/photo/{submission_id}")
    async def host_photo(
        submission_id: str, x_host_token: str | None = Header(default=None)
    ) -> Response:
        require_host(x_host_token)
        sub = game.find_submission(submission_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="Фото не найдено")
        media_type = detect_image_type(sub.photo) or "application/octet-stream"
        return Response(sub.photo, media_type=media_type, headers={"Cache-Control": "no-store"})

    @app.websocket("/ws/host")
    async def ws_host(ws: WebSocket) -> None:
        await ws.accept()

        def check(token: str) -> bool | None:
            return True if secrets.compare_digest(token, host_token) else None

        if await _auth_socket(ws, check) is None:
            return
        hub.hosts.add(ws)
        await push_hosts_now()
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            hub.hosts.discard(ws)

    return app


async def _auth_socket(ws: WebSocket, resolve: Callable[[str], Any]) -> Any:
    """Токен приходит первым сообщением, а не в адресе: адреса попадают в логи прокси."""
    try:
        msg = await asyncio.wait_for(ws.receive_json(), timeout=WS_AUTH_TIMEOUT_S)
    except (TimeoutError, WebSocketDisconnect, ValueError):
        with contextlib.suppress(RuntimeError):
            await ws.close(code=4401)
        return None
    token = msg.get("token") if isinstance(msg, dict) else None
    result = resolve(token) if isinstance(token, str) else None
    if result is None:
        with contextlib.suppress(RuntimeError):
            await ws.send_json({"type": "auth_failed"})
            await ws.close(code=4401)
    return result
