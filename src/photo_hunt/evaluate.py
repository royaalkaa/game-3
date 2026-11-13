"""Прогон набора фото через модель: точность по порогам и время ответа.

Структура папки:
    data/eval/<предмет>/yes/*.jpg   на фото человек с предметом, должно засчитаться
    data/eval/<предмет>/no/*.jpg    предмета нет или он на экране, не должно засчитаться

Запуск (ПК): python -m photo_hunt.evaluate data/eval [--burst 50]
Ключ берётся из .env (OPENAI_API_KEY), как у сервера.
"""

import argparse
import asyncio
import csv
import io
import logging
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from photo_hunt.vision import CheckResult, PhotoChecker

log = logging.getLogger("evaluate")

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
LABELS = {"yes": True, "no": False}
MAX_SIDE = 1024
JPEG_QUALITY = 75
THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9)


@dataclass(frozen=True)
class Sample:
    path: Path
    item: str
    expected: bool


@dataclass(frozen=True)
class Row:
    sample: Sample
    result: CheckResult | None
    seconds: float


@dataclass(frozen=True)
class Summary:
    threshold: float
    total: int
    correct: int
    false_accept: int
    false_reject: int
    review: int
    unavailable: int

    @property
    def decided_accuracy(self) -> float:
        decided = self.correct + self.false_accept + self.false_reject
        return self.correct / decided if decided else 0.0


def collect(root: Path) -> list[Sample]:
    samples = []
    for item_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for label, expected in LABELS.items():
            folder = item_dir / label
            if not folder.is_dir():
                continue
            for path in sorted(folder.iterdir()):
                if path.suffix.lower() in IMAGE_SUFFIXES:
                    samples.append(Sample(path, item_dir.name, expected))
    return samples


def prepare(path: Path) -> bytes:
    """Как в браузере игрока: поворот по EXIF, сторона до 1024 px, JPEG."""
    from PIL import Image, ImageOps

    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except ImportError:
        log.warning("heic_unsupported")

    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        return buf.getvalue()


def summarize(rows: list[Row], threshold: float) -> Summary:
    correct = false_accept = false_reject = review = unavailable = 0
    for row in rows:
        res = row.result
        if res is None:
            unavailable += 1
        elif res.confidence < threshold:
            review += 1
        elif res.match == row.sample.expected:
            correct += 1
        elif res.match:
            false_accept += 1
        else:
            false_reject += 1
    return Summary(threshold, len(rows), correct, false_accept, false_reject, review, unavailable)


def latency_stats(rows: list[Row]) -> tuple[float, float, float]:
    times = sorted(r.seconds for r in rows)
    if not times:
        return 0.0, 0.0, 0.0
    p90 = times[min(len(times) - 1, int(len(times) * 0.9))]
    return statistics.median(times), p90, times[-1]


async def run(checker: PhotoChecker, samples: list[Sample]) -> list[Row]:
    async def one(sample: Sample) -> Row:
        data = await asyncio.to_thread(prepare, sample.path)
        start = time.perf_counter()
        result = await checker.check(data, sample.item)
        return Row(sample, result, time.perf_counter() - start)

    return list(await asyncio.gather(*(one(s) for s in samples)))


async def burst(checker: PhotoChecker, sample: Sample, count: int) -> tuple[int, float]:
    """Все фото разом, как в первом раунде: сколько ответов не пришло и за сколько."""
    data = await asyncio.to_thread(prepare, sample.path)
    start = time.perf_counter()
    results = await asyncio.gather(*(checker.check(data, sample.item) for _ in range(count)))
    return sum(1 for r in results if r is None), time.perf_counter() - start


def write_csv(rows: list[Row], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["file", "item", "expected", "match", "confidence", "seconds", "reason"])
        for r in rows:
            res = r.result
            writer.writerow(
                [
                    r.sample.path.name,
                    r.sample.item,
                    "yes" if r.sample.expected else "no",
                    "" if res is None else ("yes" if res.match else "no"),
                    "" if res is None else f"{res.confidence:.2f}",
                    f"{r.seconds:.2f}",
                    "недоступно" if res is None else res.reason,
                ]
            )


async def main(root: Path, burst_count: int) -> None:
    from photo_hunt.app import build_checker
    from photo_hunt.config import Settings, VisionMode

    settings = Settings()  # type: ignore[call-arg]
    if settings.vision_mode != VisionMode.OPENAI:
        raise SystemExit("Set PH_VISION_MODE=openai in .env to evaluate the real model")
    checker = build_checker(settings)

    samples = collect(root)
    if not samples:
        raise SystemExit(f"No photos found in {root}/<item>/yes|no/")
    log.info("model=%s photos=%d", settings.openai_model, len(samples))

    rows = await run(checker, samples)
    report = root / "report.csv"
    write_csv(rows, report)

    for r in rows:
        res = r.result
        verdict = "недоступно" if res is None else f"{res.match} {res.confidence:.2f} {res.reason}"
        mark = "" if res is None or res.match == r.sample.expected else "  <-- ошибка"
        log.info("%s/%s: %s%s", r.sample.item, r.sample.path.name, verdict, mark)

    log.info("порог | верно | чужие засчитаны | свои отклонены | ведущему | недоступно")
    for t in THRESHOLDS:
        s = summarize(rows, t)
        log.info(
            "%.1f   | %d/%d | %d | %d | %d | %d",
            t,
            s.correct,
            s.total,
            s.false_accept,
            s.false_reject,
            s.review,
            s.unavailable,
        )
    median, p90, worst = latency_stats(rows)
    log.info("время ответа: медиана %.2fs, p90 %.2fs, макс %.2fs", median, p90, worst)
    log.info("подробности: %s", report)

    if burst_count:
        first_yes = next((s for s in samples if s.expected), samples[0])
        failed, seconds = await burst(checker, first_yes, burst_count)
        log.info("залп %d запросов: не ответили %d, всего %.1fs", burst_count, failed, seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, nargs="?", default=Path("data") / "eval")
    parser.add_argument("--burst", type=int, default=0, help="сколько одновременных запросов")
    args = parser.parse_args()
    asyncio.run(main(args.root, args.burst))
