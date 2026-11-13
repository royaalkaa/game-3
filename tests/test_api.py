import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from photo_hunt.app import create_app
from photo_hunt.config import Settings, VisionMode
from photo_hunt.vision import CheckResult

HOST = "host-secret-123"
JPEG = bytes.fromhex("ffd8ff") + b"0" * 10


class ScriptedChecker:
    """Отвечает по предмету: удобно проверять разные исходы."""

    def __init__(self, result: CheckResult | None) -> None:
        self.result = result

    async def check(self, photo: bytes, item: str) -> CheckResult | None:
        return self.result


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def setup(tmp_path: Path):
    music = tmp_path / "music"
    music.mkdir()
    (music / "song.mp3").write_bytes(b"ID3fake")
    songs = tmp_path / "songs.json"
    songs.write_text(
        json.dumps(
            [
                {
                    "id": "a",
                    "title": "A",
                    "item": "кружка",
                    "lyric": "л",
                    "file": "song.mp3",
                    "stop_at": 3,
                },
                {"id": "b", "title": "B", "item": "очки", "file": "../secret.txt"},
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    settings = Settings(
        host_token=HOST,
        vision_mode=VisionMode.FAKE,
        songs_file=songs,
        music_dir=music,
        public_url="https://game.test",
    )
    checker = ScriptedChecker(CheckResult(match=True, confidence=0.95, reason="ок"))
    clock = Clock()
    app = create_app(settings, checker=checker, clock=clock)
    with TestClient(app) as client:
        yield client, checker, clock, app


H = {"X-Host-Token": HOST}


def join(client, name):
    return client.post("/api/join", json={"name": name}).json()["token"]


def upload(client, token, data=JPEG):
    return client.post(
        "/api/photo",
        files={"photo": ("p.jpg", data, "image/jpeg")},
        headers={"X-Player-Token": token},
    )


def test_pages_and_qr(setup):
    client, *_ = setup
    assert "Фотоохота" in client.get("/").text
    assert client.get("/host").status_code == 200
    qr = client.get("/api/qr.svg")
    assert qr.headers["content-type"] == "image/svg+xml"


def test_host_endpoints_require_token(setup):
    client, *_ = setup
    assert client.get("/api/host/state").status_code == 401
    assert client.get("/api/host/state", headers={"X-Host-Token": "wrong"}).status_code == 401
    assert client.post("/api/host/finish").status_code == 401


def test_songs_hide_file_and_block_path_escape(setup):
    client, *_ = setup
    songs = client.get("/api/host/songs", headers=H).json()
    assert songs[0]["has_music"] is True and "file" not in songs[0]
    assert client.get("/api/host/music/a", headers=H).content == b"ID3fake"
    assert client.get("/api/host/music/b", headers=H).status_code == 404
    assert client.get("/api/host/music/zzz", headers=H).status_code == 404


def test_song_editor_over_http(setup, tmp_path: Path):
    client, *_ = setup
    assert client.post("/api/host/songs", json={"title": "N", "item": "x"}).status_code == 401
    created = client.post(
        "/api/host/songs", json={"title": "Новая", "item": "очки", "stop_at": 10}, headers=H
    )
    assert created.status_code == 201
    sid = created.json()["id"]
    assert (
        client.post("/api/host/songs", json={"title": "", "item": "x"}, headers=H).status_code
        == 422
    )

    updated = client.put(
        f"/api/host/songs/{sid}",
        json={"title": "Новая", "item": "шапка", "stop_at": 12.3},
        headers=H,
    ).json()
    assert updated["item"] == "шапка" and updated["has_music"] is False

    music = client.put(
        f"/api/host/songs/{sid}/music",
        files={"music": ("x.mp3", b"ID3data", "audio/mpeg")},
        headers=H,
    )
    assert music.json()["has_music"] is True
    assert client.get(f"/api/host/music/{sid}", headers=H).content == b"ID3data"
    bad = client.put(
        f"/api/host/songs/{sid}/music",
        files={"music": ("x.mp3", b"<html>", "audio/mpeg")},
        headers=H,
    )
    assert bad.status_code == 400
    assert (
        client.put(
            "/api/host/songs/zzz/music", files={"music": ("x", b"ID3", "audio/mpeg")}, headers=H
        )
    ).status_code == 404

    saved = json.loads((tmp_path / "songs.json").read_text(encoding="utf-8"))
    assert [s["title"] for s in saved] == ["A", "B", "Новая"]

    assert client.delete(f"/api/host/songs/{sid}", headers=H).status_code == 204
    assert client.delete(f"/api/host/songs/{sid}", headers=H).status_code == 404
    assert [s["id"] for s in client.get("/api/host/songs", headers=H).json()] == ["a", "b"]


def test_music_size_limit(tmp_path: Path):
    settings = Settings(
        host_token=HOST,
        vision_mode=VisionMode.FAKE,
        songs_file=tmp_path / "songs.json",
        music_dir=tmp_path / "music",
        max_music_bytes=10,
    )
    with TestClient(create_app(settings, checker=ScriptedChecker(None))) as client:
        sid = client.post("/api/host/songs", json={"title": "A", "item": "x"}, headers=H).json()[
            "id"
        ]
        r = client.put(
            f"/api/host/songs/{sid}/music", files={"music": ("x", b"ID3" + b"0" * 20)}, headers=H
        )
        assert r.status_code == 413


def test_healthz_and_songs_page(setup):
    client, *_ = setup
    assert client.get("/healthz").json() == {"ok": True, "phase": "lobby", "players": 0}
    assert "Песни" in client.get("/songs").text


def test_full_round_over_http(setup):
    client, _, clock, _ = setup
    tokens = [join(client, f"p{i}") for i in range(3)]
    assert client.post(
        "/api/host/music", json={"item": "кружка", "lyric": "", "advance": 2}, headers=H
    ).json() == {"round_no": 1}
    assert upload(client, tokens[0]).status_code == 409  # музыка ещё играет
    assert client.post("/api/host/hunt", json={"duration_s": 30}, headers=H).status_code == 200
    for i, t in enumerate(tokens):
        clock.now = float(i)
        assert upload(client, t).status_code == 202
    state = client.get("/api/host/state", headers=H).json()
    assert state["counts"]["accepted"] == 3
    assert [s["name"] for s in state["submissions"]] == ["p0", "p1", "p2"]
    assert client.post("/api/host/finish", headers=H).json() == {"survivors": 2}
    state = client.get("/api/host/state", headers=H).json()
    assert state["phase"] == "results"
    assert [s["name"] for s in state["survivors"]] == ["p0", "p1"]
    photo = client.get(f"/api/host/photo/{state['survivors'][0]['submission_id']}", headers=H)
    assert photo.content == JPEG


def test_photo_validation(setup):
    client, *_ = setup
    t = join(client, "a")
    join(client, "b")
    client.post("/api/host/music", json={"item": "x", "advance": 1}, headers=H)
    client.post("/api/host/hunt", json={}, headers=H)
    assert upload(client, "bad-token").status_code == 401
    assert upload(client, t, b"<svg/>").status_code == 400
    assert upload(client, t, JPEG + b"0" * 3_000_000).status_code == 413


def test_low_confidence_goes_to_host_and_host_decides(setup):
    client, checker, *_ = setup
    checker.result = CheckResult(match=True, confidence=0.3, reason="?")
    t = join(client, "a")
    join(client, "b")
    client.post("/api/host/music", json={"item": "x", "advance": 1}, headers=H)
    client.post("/api/host/hunt", json={}, headers=H)
    upload(client, t)
    sub = client.get("/api/host/state", headers=H).json()["submissions"][0]
    assert sub["verdict"] == "review"
    client.post(f"/api/host/decide/{sub['id']}", json={"accept": True}, headers=H)
    assert client.get("/api/host/state", headers=H).json()["counts"]["accepted"] == 1


def test_unavailable_check_then_accept_all(setup):
    client, checker, *_ = setup
    checker.result = None
    t = join(client, "a")
    join(client, "b")
    client.post("/api/host/music", json={"item": "x", "advance": 1}, headers=H)
    client.post("/api/host/hunt", json={}, headers=H)
    upload(client, t)
    assert client.post("/api/host/accept-review", headers=H).json() == {"accepted": 1}


def test_game_error_is_readable(setup):
    client, *_ = setup
    r = client.post("/api/host/music", json={"item": "x", "advance": 1}, headers=H)
    assert r.status_code == 409
    assert r.json()["detail"] == "Нужно хотя бы два игрока"


def test_player_websocket_gets_state_and_reset(setup):
    client, *_ = setup
    t = join(client, "Аня")
    with client.websocket_connect("/ws/player") as ws:
        ws.send_json({"token": t})
        state = ws.receive_json()
        assert state["me"]["name"] == "Аня" and state["phase"] == "lobby"
        client.post("/api/host/reset", headers=H)
        assert ws.receive_json() == {"type": "reset"}


def test_player_websocket_rejects_bad_token(setup):
    client, *_ = setup
    with client.websocket_connect("/ws/player") as ws:
        ws.send_json({"token": "nope"})
        assert ws.receive_json() == {"type": "auth_failed"}


def test_host_websocket(setup):
    client, *_ = setup
    with client.websocket_connect("/ws/host") as ws:
        ws.send_json({"token": HOST})
        state = ws.receive_json()
        assert state["join_url"] == "https://game.test"


def test_item_hidden_from_players_during_music(setup):
    client, *_ = setup
    t = join(client, "a")
    join(client, "b")
    client.post("/api/host/music", json={"item": "секрет", "advance": 1}, headers=H)
    with client.websocket_connect("/ws/player") as ws:
        ws.send_json({"token": t})
        assert ws.receive_json()["item"] is None


def test_settings_fail_fast():
    with pytest.raises(ValueError):
        Settings(host_token=HOST, vision_mode=VisionMode.OPENAI, openai_api_key=None)
    with pytest.raises(ValueError):
        Settings(host_token="short", vision_mode=VisionMode.FAKE)
