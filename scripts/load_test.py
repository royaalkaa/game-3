"""Нагрузочный прогон: N игроков с WebSocket одновременно отправляют фото.

Сервер должен быть запущен с PH_VISION_MODE=fake.
Запуск: python scripts/load_test.py --url http://127.0.0.1:8000 --players 200
Пароль ведущего берётся из переменной PH_HOST_TOKEN.
"""

import argparse
import asyncio
import json
import logging
import math
import os
import secrets
import statistics
import time

import httpx
import websockets

log = logging.getLogger("load_test")
PHOTO_BYTES = 200_000


def fake_jpeg() -> bytes:
    return bytes.fromhex("ffd8ff") + secrets.token_bytes(PHOTO_BYTES)


async def listen(ws_url: str, token: str, counter: dict[str, int], stop: asyncio.Event) -> None:
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"token": token}))
        while not stop.is_set():
            try:
                await asyncio.wait_for(ws.recv(), timeout=0.5)
                counter["messages"] += 1
            except TimeoutError:
                continue


async def main(url: str, players: int, host_token: str) -> None:
    host = {"X-Host-Token": host_token}
    ws_url = url.replace("http", "ws", 1) + "/ws/player"
    limits = httpx.Limits(max_connections=players + 10)
    async with httpx.AsyncClient(base_url=url, timeout=60, limits=limits) as client:
        (await client.post("/api/host/reset", headers=host)).raise_for_status()

        t0 = time.perf_counter()
        joins = await asyncio.gather(
            *(client.post("/api/join", json={"name": f"bot{i}"}) for i in range(players))
        )
        tokens = [r.json()["token"] for r in joins]
        log.info("joined players=%d in %.2fs", players, time.perf_counter() - t0)

        counter = {"messages": 0}
        stop = asyncio.Event()
        listeners = [asyncio.create_task(listen(ws_url, t, counter, stop)) for t in tokens]
        await asyncio.sleep(1)
        log.info("websockets open=%d", sum(1 for t in listeners if not t.done()))

        advance = math.ceil(players * 0.6)
        body = {"item": "кружка", "lyric": "", "advance": advance}
        (await client.post("/api/host/music", json=body, headers=host)).raise_for_status()
        hunt = await client.post("/api/host/hunt", json={"duration_s": 120}, headers=host)
        hunt.raise_for_status()

        photo = fake_jpeg()

        async def upload(token: str) -> float:
            start = time.perf_counter()
            r = await client.post(
                "/api/photo",
                files={"photo": ("p.jpg", photo, "image/jpeg")},
                headers={"X-Player-Token": token},
            )
            r.raise_for_status()
            return time.perf_counter() - start

        t0 = time.perf_counter()
        latencies = await asyncio.gather(*(upload(t) for t in tokens))
        uploaded = time.perf_counter() - t0
        log.info(
            "uploads=%d total=%.2fs median=%.3fs max=%.3fs",
            len(latencies),
            uploaded,
            statistics.median(latencies),
            max(latencies),
        )

        while True:
            state = (await client.get("/api/host/state", headers=host)).json()
            if state["counts"]["checking"] == 0:
                break
            await asyncio.sleep(0.2)
        log.info("all checked in %.2fs, counts=%s", time.perf_counter() - t0, state["counts"])

        result = (await client.post("/api/host/finish", headers=host)).json()
        await asyncio.sleep(1)
        log.info("survivors=%d expected=%d", result["survivors"], advance)
        log.info("websocket messages received=%d", counter["messages"])

        stop.set()
        await asyncio.gather(*listeners, return_exceptions=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--players", type=int, default=200)
    args = parser.parse_args()
    token = os.environ.get("PH_HOST_TOKEN")
    if not token:
        raise SystemExit("Set PH_HOST_TOKEN")
    asyncio.run(main(args.url, args.players, token))
