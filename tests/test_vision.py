import asyncio
from types import SimpleNamespace

import openai

from photo_hunt.game import Verdict
from photo_hunt.vision import (
    CheckResult,
    FakeChecker,
    OpenAIChecker,
    decide,
    detect_image_type,
    parse_answer,
)

JPEG = bytes.fromhex("ffd8ff") + b"0" * 10


class FakeCompletions:
    def __init__(self, content=None, exc=None, delay=0.0):
        self.content = content
        self.exc = exc
        self.delay = delay
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        msg = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def make_checker(completions, timeout_s=5.0):
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return OpenAIChecker(client, model="m", concurrency=2, timeout_s=timeout_s)


def test_decide_thresholds():
    ok = CheckResult(match=True, confidence=0.9, reason="кружка")
    no = CheckResult(match=False, confidence=0.9, reason="нет кружки")
    unsure = CheckResult(match=True, confidence=0.5, reason="?")
    assert decide(ok, 0.8).verdict == Verdict.ACCEPTED
    assert decide(no, 0.8).verdict == Verdict.REJECTED
    assert decide(no, 0.8).reason == "нет кружки"
    assert decide(unsure, 0.8).verdict == Verdict.REVIEW
    assert decide(None, 0.8).verdict == Verdict.REVIEW


def test_parse_answer_rejects_broken_json():
    assert parse_answer('{"match": true, "confidence": 0.9, "reason": "да"}').match is True
    assert parse_answer('{"match": true}') is None
    assert parse_answer('{"match": true, "confidence": 7, "reason": ""}') is None
    assert parse_answer("not json") is None
    assert parse_answer("") is None
    assert parse_answer(None) is None


def test_detect_image_type():
    assert detect_image_type(JPEG) == "image/jpeg"
    assert detect_image_type(bytes.fromhex("89504e470d0a1a0a") + b"x") == "image/png"
    assert detect_image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert detect_image_type(b"<svg>") is None


async def test_openai_checker_sends_image_and_parses():
    comp = FakeCompletions('{"match": true, "confidence": 0.95, "reason": "вижу кружку"}')
    result = await make_checker(comp).check(JPEG, "кружка")
    assert result is not None and result.match
    call = comp.calls[0]
    assert call["model"] == "m"
    assert call["response_format"]["json_schema"]["strict"] is True
    content = call["messages"][1]["content"]
    assert "кружка" in content[0]["text"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_openai_checker_api_error_means_unavailable():
    exc = openai.APIConnectionError(request=None)  # type: ignore[arg-type]
    assert await make_checker(FakeCompletions(exc=exc)).check(JPEG, "x") is None


async def test_openai_checker_timeout_means_unavailable():
    comp = FakeCompletions('{"match": true, "confidence": 1, "reason": ""}', delay=1.0)
    assert await make_checker(comp, timeout_s=0.05).check(JPEG, "x") is None


async def test_openai_checker_bad_json_means_unavailable():
    assert await make_checker(FakeCompletions("{oops")).check(JPEG, "x") is None


async def test_fake_checker_accepts():
    result = await FakeChecker(0).check(JPEG, "кружка")
    assert result is not None and result.match and "кружка" in result.reason
