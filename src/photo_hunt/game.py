"""Состояние игры. Чистая логика без сети: время передаётся снаружи, чтобы тесты были точными."""

import secrets
from dataclasses import dataclass, field
from enum import StrEnum

NAME_MAX_LEN = 24
ITEM_MAX_LEN = 60
LYRIC_MAX_LEN = 200


class Phase(StrEnum):
    LOBBY = "lobby"
    MUSIC = "music"
    HUNT = "hunt"
    RESULTS = "results"
    FINISHED = "finished"


class Verdict(StrEnum):
    CHECKING = "checking"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REVIEW = "review"


class GameError(Exception):
    """Ошибка с текстом для человека: уходит в ответ API как есть."""


@dataclass
class Player:
    id: str
    token: str
    name: str
    alive: bool = True


@dataclass
class Submission:
    id: str
    player_id: str
    round_no: int
    received_at: float
    photo: bytes
    verdict: Verdict = Verdict.CHECKING
    reason: str = ""
    confidence: float | None = None


@dataclass
class Round:
    no: int
    item: str
    lyric: str
    advance: int
    started_at: float | None = None
    deadline: float | None = None
    # Последняя попытка каждого игрока: после отказа можно переснять.
    submissions: dict[str, Submission] = field(default_factory=dict)


@dataclass
class Game:
    phase: Phase = Phase.LOBBY
    players: dict[str, Player] = field(default_factory=dict)
    round: Round | None = None
    round_no: int = 0
    last_survivors: list[str] = field(default_factory=list)

    # --- игроки ---

    def join(self, name: str) -> Player:
        clean = " ".join(name.split())[:NAME_MAX_LEN]
        if not clean:
            raise GameError("Введи имя")
        # После старта новые игроки смотрят игру, но не участвуют.
        alive = self.phase == Phase.LOBBY
        player = Player(
            id=secrets.token_hex(8), token=secrets.token_urlsafe(24), name=clean, alive=alive
        )
        self.players[player.id] = player
        return player

    def player_by_token(self, token: str) -> Player | None:
        for player in self.players.values():
            if secrets.compare_digest(player.token, token):
                return player
        return None

    def alive_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.alive]

    # --- раунд ---

    def start_music(self, item: str, lyric: str, advance: int) -> Round:
        if self.phase not in (Phase.LOBBY, Phase.RESULTS):
            raise GameError("Раунд уже идёт")
        item = item.strip()[:ITEM_MAX_LEN]
        if not item:
            raise GameError("Укажи предмет")
        alive = len(self.alive_players())
        if alive < 2:
            raise GameError("Нужно хотя бы два игрока")
        if not 1 <= advance < alive:
            raise GameError(f"Дальше проходят от 1 до {alive - 1} игроков")
        self.round_no += 1
        self.round = Round(
            no=self.round_no, item=item, lyric=lyric.strip()[:LYRIC_MAX_LEN], advance=advance
        )
        self.phase = Phase.MUSIC
        return self.round

    def start_hunt(self, now: float, duration_s: float) -> None:
        if self.phase != Phase.MUSIC or self.round is None:
            raise GameError("Сначала запусти музыку")
        if duration_s <= 0:
            raise GameError("Время раунда должно быть больше нуля")
        self.round.started_at = now
        self.round.deadline = now + duration_s
        self.phase = Phase.HUNT

    def submit(self, player: Player, photo: bytes, now: float) -> Submission:
        rnd = self.round
        if self.phase != Phase.HUNT or rnd is None:
            raise GameError("Сейчас фото не принимаются")
        if not player.alive:
            raise GameError("Ты уже выбыл, смотри игру")
        if rnd.deadline is not None and now > rnd.deadline:
            raise GameError("Время вышло")
        previous = rnd.submissions.get(player.id)
        if previous is not None and previous.verdict != Verdict.REJECTED:
            raise GameError("Фото уже отправлено")
        sub = Submission(
            id=secrets.token_hex(8),
            player_id=player.id,
            round_no=rnd.no,
            received_at=now,
            photo=photo,
        )
        rnd.submissions[player.id] = sub
        return sub

    def find_submission(self, submission_id: str) -> Submission | None:
        if self.round is None:
            return None
        for sub in self.round.submissions.values():
            if sub.id == submission_id:
                return sub
        return None

    def apply_check(
        self, submission_id: str, verdict: Verdict, reason: str, confidence: float | None
    ) -> Submission | None:
        """Результат автоматической проверки. Решение ведущего не перезаписывается."""
        sub = self.find_submission(submission_id)
        if sub is None or sub.verdict != Verdict.CHECKING:
            return None
        sub.verdict = verdict
        sub.reason = reason
        sub.confidence = confidence
        return sub

    def host_decide(self, submission_id: str, accept: bool) -> Submission:
        sub = self.find_submission(submission_id)
        if sub is None:
            raise GameError("Фото не найдено")
        if self.phase != Phase.HUNT:
            raise GameError("Раунд уже завершён")
        sub.verdict = Verdict.ACCEPTED if accept else Verdict.REJECTED
        sub.reason = "" if accept else "Ведущий не засчитал"
        return sub

    def accept_all_review(self) -> int:
        """Запасной режим, когда проверка недоступна: спорные засчитываются по времени."""
        if self.round is None or self.phase != Phase.HUNT:
            raise GameError("Раунд не идёт")
        count = 0
        for sub in self.round.submissions.values():
            if sub.verdict == Verdict.REVIEW:
                sub.verdict = Verdict.ACCEPTED
                count += 1
        return count

    def ranking(self) -> list[Submission]:
        if self.round is None:
            return []
        accepted = [s for s in self.round.submissions.values() if s.verdict == Verdict.ACCEPTED]
        return sorted(accepted, key=lambda s: s.received_at)

    def place_of(self, player_id: str) -> int | None:
        for idx, sub in enumerate(self.ranking(), start=1):
            if sub.player_id == player_id:
                return idx
        return None

    def counts(self) -> dict[str, int]:
        result = {v.value: 0 for v in Verdict}
        if self.round is not None:
            for sub in self.round.submissions.values():
                result[sub.verdict.value] += 1
        return result

    def finish_round(self) -> list[Player]:
        rnd = self.round
        if self.phase != Phase.HUNT or rnd is None:
            raise GameError("Раунд не идёт")
        if self.counts()[Verdict.CHECKING] > 0:
            raise GameError("Ещё идёт проверка фото, подожди пару секунд")
        winners = self.ranking()[: rnd.advance]
        if not winners:
            # Никто не справился: раунд не считается, все остаются в игре.
            self.last_survivors = []
            self.phase = Phase.RESULTS
            return []
        survivor_ids = {s.player_id for s in winners}
        for player in self.alive_players():
            if player.id not in survivor_ids:
                player.alive = False
        # Фото выбывших больше не нужны: в памяти остаются только фото прошедших.
        rnd.submissions = {pid: s for pid, s in rnd.submissions.items() if pid in survivor_ids}
        self.last_survivors = [s.player_id for s in winners]
        self.phase = Phase.FINISHED if len(winners) == 1 else Phase.RESULTS
        return [self.players[pid] for pid in self.last_survivors]

    def reset(self) -> None:
        self.phase = Phase.LOBBY
        self.players.clear()
        self.round = None
        self.round_no = 0
        self.last_survivors = []
