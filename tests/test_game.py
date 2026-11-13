import pytest

from photo_hunt.game import Game, GameError, Phase, Verdict

JPEG = bytes.fromhex("ffd8ff") + b"0" * 10


def make_game(n: int) -> tuple[Game, list]:
    game = Game()
    players = [game.join(f"p{i}") for i in range(n)]
    return game, players


def hunt(game: Game, advance: int, now: float = 0.0, duration: float = 30.0) -> None:
    game.start_music("кружка", "строчка", advance)
    game.start_hunt(now, duration)


def accept(game: Game, sub_id: str) -> None:
    game.apply_check(sub_id, Verdict.ACCEPTED, "ok", 0.9)


def test_join_cleans_name_and_rejects_empty():
    game = Game()
    assert game.join("  Иван   Петров  ").name == "Иван Петров"
    assert len(game.join("x" * 100).name) == 24
    with pytest.raises(GameError):
        game.join("   ")


def test_late_joiner_is_spectator():
    game, _ = make_game(3)
    game.start_music("кружка", "", 2)
    late = game.join("late")
    assert late.alive is False
    assert len(game.alive_players()) == 3


def test_advance_must_eliminate_someone():
    game, _ = make_game(3)
    with pytest.raises(GameError):
        game.start_music("кружка", "", 3)
    with pytest.raises(GameError):
        game.start_music("кружка", "", 0)
    with pytest.raises(GameError):
        game.start_music("  ", "", 1)


def test_need_two_players():
    game, _ = make_game(1)
    with pytest.raises(GameError):
        game.start_music("кружка", "", 1)


def test_hunt_requires_music():
    game, _ = make_game(3)
    with pytest.raises(GameError):
        game.start_hunt(0, 30)


def test_first_accepted_survive_last_eliminated():
    game, ps = make_game(4)
    hunt(game, advance=2)
    subs = [game.submit(p, JPEG, now=t) for t, p in zip([5, 1, 3, 2], ps, strict=True)]
    for s in subs:
        accept(game, s.id)
    survivors = game.finish_round()
    # Порядок прихода: p1 (1 с), p3 (2 с), p2 (3 с), p0 (5 с).
    assert [p.name for p in survivors] == ["p1", "p3"]
    assert {p.name for p in game.alive_players()} == {"p1", "p3"}
    assert game.phase == Phase.RESULTS


def test_rejected_and_missing_players_are_eliminated():
    game, ps = make_game(4)
    hunt(game, advance=3)
    s0 = game.submit(ps[0], JPEG, 1)
    s1 = game.submit(ps[1], JPEG, 2)
    accept(game, s0.id)
    game.apply_check(s1.id, Verdict.REJECTED, "нет кружки", 0.9)
    survivors = game.finish_round()
    assert [p.name for p in survivors] == ["p0"]
    assert game.phase == Phase.FINISHED


def test_nobody_accepted_round_is_void():
    game, ps = make_game(3)
    hunt(game, advance=1)
    game.submit(ps[0], JPEG, 1)
    sub = game.round.submissions[ps[0].id]
    game.apply_check(sub.id, Verdict.REJECTED, "нет", 0.9)
    assert game.finish_round() == []
    assert len(game.alive_players()) == 3
    assert game.phase == Phase.RESULTS


def test_resubmit_only_after_rejection():
    game, ps = make_game(3)
    hunt(game, advance=1)
    first = game.submit(ps[0], JPEG, 1)
    with pytest.raises(GameError):
        game.submit(ps[0], JPEG, 2)
    game.apply_check(first.id, Verdict.REJECTED, "нет", 0.9)
    second = game.submit(ps[0], JPEG, 3)
    assert second.received_at == 3
    assert game.round.submissions[ps[0].id] is second


def test_submit_after_deadline_or_when_eliminated():
    game, ps = make_game(3)
    hunt(game, advance=1, now=0, duration=10)
    with pytest.raises(GameError):
        game.submit(ps[0], JPEG, now=10.5)
    ps[1].alive = False
    with pytest.raises(GameError):
        game.submit(ps[1], JPEG, now=1)


def test_submit_outside_hunt():
    game, ps = make_game(3)
    with pytest.raises(GameError):
        game.submit(ps[0], JPEG, 0)


def test_finish_blocked_while_checking():
    game, ps = make_game(3)
    hunt(game, advance=1)
    game.submit(ps[0], JPEG, 1)
    with pytest.raises(GameError):
        game.finish_round()


def test_review_unresolved_counts_as_not_accepted():
    game, ps = make_game(3)
    hunt(game, advance=2)
    a = game.submit(ps[0], JPEG, 1)
    b = game.submit(ps[1], JPEG, 2)
    accept(game, b.id)
    game.apply_check(a.id, Verdict.REVIEW, "", None)
    assert [p.name for p in game.finish_round()] == ["p1"]


def test_host_decision_is_not_overwritten_by_late_check():
    game, ps = make_game(3)
    hunt(game, advance=1)
    sub = game.submit(ps[0], JPEG, 1)
    game.host_decide(sub.id, accept=True)
    assert game.apply_check(sub.id, Verdict.REJECTED, "нет", 0.9) is None
    assert sub.verdict == Verdict.ACCEPTED


def test_host_can_reject_accepted():
    game, ps = make_game(3)
    hunt(game, advance=1)
    sub = game.submit(ps[0], JPEG, 1)
    accept(game, sub.id)
    game.host_decide(sub.id, accept=False)
    assert sub.verdict == Verdict.REJECTED
    with pytest.raises(GameError):
        game.host_decide("missing", accept=True)


def test_accept_all_review_fallback():
    game, ps = make_game(4)
    hunt(game, advance=2)
    for t, p in enumerate(ps[:3]):
        s = game.submit(p, JPEG, t)
        game.apply_check(s.id, Verdict.REVIEW, "", None)
    assert game.accept_all_review() == 3
    assert [s.player_id for s in game.ranking()] == [p.id for p in ps[:3]]


def test_place_and_counts():
    game, ps = make_game(3)
    hunt(game, advance=2)
    a = game.submit(ps[0], JPEG, 2)
    b = game.submit(ps[1], JPEG, 1)
    accept(game, a.id)
    accept(game, b.id)
    assert game.place_of(ps[1].id) == 1
    assert game.place_of(ps[0].id) == 2
    assert game.place_of(ps[2].id) is None
    assert game.counts()["accepted"] == 2


def test_eliminated_photos_are_dropped_after_round():
    game, ps = make_game(3)
    hunt(game, advance=1)
    for t, p in enumerate(ps):
        accept(game, game.submit(p, JPEG, t).id)
    game.finish_round()
    assert set(game.round.submissions) == {ps[0].id}


def test_full_game_to_single_winner():
    game, _ = make_game(10)
    now = 0.0
    for advance in (5, 3, 2, 1):
        hunt(game, advance=advance, now=now)
        for i, p in enumerate(game.alive_players()):
            accept(game, game.submit(p, JPEG, now + i).id)
        game.finish_round()
        now += 100
    assert game.phase == Phase.FINISHED
    assert [p.name for p in game.alive_players()] == ["p0"]


def test_player_by_token_and_reset():
    game, ps = make_game(2)
    assert game.player_by_token(ps[0].token) is ps[0]
    assert game.player_by_token("nope") is None
    game.reset()
    assert game.players == {}
    assert game.phase == Phase.LOBBY
