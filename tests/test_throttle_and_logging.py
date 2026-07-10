"""Tests for the adaptive 429 cooldown gate, verbose logging setup and
the xelatex export engine registration."""

import logging

import pytest

from pdftransl.llm.ratelimit import CooldownGate, RateLimiter


# ---- CooldownGate ---------------------------------------------------------

class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def make_gate(base=10.0, max_cooldown=60.0):
    clock = FakeClock()
    gate = CooldownGate(base=base, max_cooldown=max_cooldown,
                        clock=clock, sleep=clock.sleep)
    return gate, clock


def test_gate_open_by_default():
    gate, clock = make_gate()
    gate.wait()
    assert clock.t == 0.0          # no waiting when nothing tripped


def test_trip_blocks_then_releases():
    gate, clock = make_gate(base=10.0)
    gate.trip()
    gate.wait()
    assert clock.t >= 10.0         # waited out the cooldown


def test_retry_after_header_wins():
    gate, clock = make_gate(base=10.0)
    applied = gate.trip(retry_after=25.0)
    assert applied == 25.0
    gate.wait()
    assert clock.t >= 25.0


def test_penalty_grows_and_resets():
    gate, clock = make_gate(base=10.0, max_cooldown=60.0)
    assert gate.trip() == 10.0     # first 429 -> base
    assert gate.trip() == 20.0     # second -> doubled
    gate.reset()                   # a success relaxes it
    clock.t += 1000
    assert gate.trip() == 10.0     # back to base


def test_penalty_capped():
    gate, clock = make_gate(base=50.0, max_cooldown=60.0)
    gate.trip()
    assert gate.trip() == 60.0     # capped at max


def test_client_trips_gate_on_429(monkeypatch):
    """A 429 response trips the shared gate; the retry then succeeds."""
    from pdftransl.config import ProviderConfig
    from pdftransl.llm import openai_compat
    from pdftransl.llm.openai_compat import OpenAICompatClient

    class Resp:
        def __init__(self, status, body=None, headers=None):
            self.status_code = status
            self._body = body or {}
            self.headers = headers or {}
            self.text = "rate limited"

        def json(self):
            return self._body

    responses = [
        Resp(429, headers={"Retry-After": "7"}),
        Resp(200, body={"choices": [{"message": {"content": "ok"}}]}),
    ]
    # клиент ходит через requests.Session (keep-alive пул) — патчим метод
    # класса Session, а не модульный requests.post
    monkeypatch.setattr(openai_compat.requests.Session, "post",
                        lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(openai_compat.time, "sleep", lambda s: None)

    gate, clock = make_gate()
    client = OpenAICompatClient(
        ProviderConfig(name="test", base_url="http://x", model="m", is_local=True),
        cooldown_gate=gate,
    )
    result = client.chat([{"role": "user", "content": "hi"}])
    assert result == "ok"
    # Retry-After: 7 was honoured and the success reset the penalty
    assert clock.t >= 7.0


def test_rate_limiter_still_works():
    clock = FakeClock()
    limiter = RateLimiter(rpm=60, clock=clock, sleep=clock.sleep)
    limiter.wait()
    limiter.wait()
    assert clock.t >= 1.0


# ---- logging setup -----------------------------------------------------------

def test_setup_logging_reads_env(monkeypatch):
    from pdftransl import logging_setup

    monkeypatch.setenv("PDFTRANSL_LOG_LEVEL", "debug")
    assert logging_setup.setup_logging() == "DEBUG"
    assert logging.getLogger().level == logging.DEBUG
    # noisy libs stay at INFO even in DEBUG
    assert logging.getLogger("urllib3").level == logging.INFO
    # restore sane state for the rest of the suite
    logging_setup.set_level("WARNING")


def test_set_level_runtime():
    from pdftransl.logging_setup import set_level

    assert set_level("INFO") == "INFO"
    assert logging.getLogger().level == logging.INFO
    with pytest.raises(ValueError):
        set_level("LOUD")
    set_level("WARNING")


# ---- xelatex engine registration -------------------------------------------

def test_xelatex_listed_when_tex_present(monkeypatch):
    from pdftransl.export import exporter

    monkeypatch.setattr(exporter, "_tex_engine", lambda: "/usr/bin/xelatex")
    assert "xelatex" in exporter.available_engines()["pdf"]
    monkeypatch.setattr(exporter, "_tex_engine", lambda: None)
    assert "xelatex" not in exporter.available_engines()["pdf"]


def test_glossary_remove(tmp_path):
    from pdftransl.rag.glossary import Glossary

    gl = Glossary(tmp_path / "g.db")
    gl.add("embedding", "эмбеддинг", "en", "ru")
    assert gl.remove("embedding", "en", "ru") is True
    assert gl.remove("embedding", "en", "ru") is False
    assert gl.list_all() == []
