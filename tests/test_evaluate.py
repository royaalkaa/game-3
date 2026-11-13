import io
from pathlib import Path

from PIL import Image

from photo_hunt.evaluate import (
    Row,
    Sample,
    burst,
    collect,
    latency_stats,
    prepare,
    run,
    summarize,
    write_csv,
)
from photo_hunt.vision import CheckResult, detect_image_type


def make_photo(path: Path, size=(3000, 2000)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 30, 30)).save(path)


def row(expected: bool, match: bool | None, conf: float = 0.9, seconds: float = 1.0) -> Row:
    sample = Sample(Path("x.jpg"), "кружка", expected)
    result = None if match is None else CheckResult(match=match, confidence=conf, reason="r")
    return Row(sample, result, seconds)


class ConstChecker:
    def __init__(self, result):
        self.result = result
        self.items = []

    async def check(self, photo, item):
        self.items.append(item)
        return self.result


def test_collect_reads_item_and_label(tmp_path: Path):
    make_photo(tmp_path / "кружка" / "yes" / "a.jpg")
    make_photo(tmp_path / "кружка" / "no" / "b.png")
    (tmp_path / "кружка" / "yes" / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "очки" / "maybe").mkdir(parents=True)
    samples = collect(tmp_path)
    assert [(s.item, s.path.name, s.expected) for s in samples] == [
        ("кружка", "a.jpg", True),
        ("кружка", "b.png", False),
    ]


def test_prepare_resizes_to_jpeg(tmp_path: Path):
    path = tmp_path / "big.png"
    make_photo(path)
    data = prepare(path)
    assert detect_image_type(data) == "image/jpeg"
    with Image.open(io.BytesIO(data)) as img:
        assert max(img.size) == 1024


def test_summarize_counts_each_outcome():
    rows = [
        row(True, True),  # верно
        row(False, False),  # верно
        row(False, True),  # чужое засчитано
        row(True, False),  # своё отклонено
        row(True, True, conf=0.6),  # ведущему при пороге 0.8
        row(True, None),  # недоступно
    ]
    s = summarize(rows, 0.8)
    assert (s.correct, s.false_accept, s.false_reject, s.review, s.unavailable) == (2, 1, 1, 1, 1)
    assert s.decided_accuracy == 0.5
    assert summarize(rows, 0.5).correct == 3


def test_summarize_empty():
    assert summarize([], 0.8).decided_accuracy == 0.0


def test_latency_stats():
    rows = [row(True, True, seconds=s) for s in (1, 2, 3, 4, 10)]
    assert latency_stats(rows) == (3, 10, 10)
    assert latency_stats([]) == (0.0, 0.0, 0.0)


async def test_run_and_burst(tmp_path: Path):
    make_photo(tmp_path / "кружка" / "yes" / "a.jpg")
    samples = collect(tmp_path)
    checker = ConstChecker(CheckResult(match=True, confidence=0.9, reason="ok"))
    rows = await run(checker, samples)
    assert rows[0].result.match and checker.items == ["кружка"]
    failed, _ = await burst(ConstChecker(None), samples[0], 5)
    assert failed == 5


def test_write_csv(tmp_path: Path):
    out = tmp_path / "r.csv"
    write_csv([row(True, True), row(False, None)], out)
    text = out.read_text(encoding="utf-8-sig")
    assert "недоступно" in text and text.count("\n") == 3
