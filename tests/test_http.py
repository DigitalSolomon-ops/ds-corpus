from __future__ import annotations

import httpx
import pytest

from ds_corpus.http import MAX_RETRIES, PoliteSession, RobotsDisallowed, TokenBucket


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_token_bucket_spaces_requests():
    clock = FakeClock()
    bucket = TokenBucket(0.5, clock=clock, sleep=clock.sleep)  # 1 req / 2 s
    bucket.acquire()                      # first request: immediate
    assert clock.sleeps == []
    bucket.acquire()                      # second: must wait the full interval
    assert clock.sleeps == [2.0]
    clock.now += 5                        # long pause -> no sleep needed
    bucket.acquire()
    assert clock.sleeps == [2.0]


def test_token_bucket_slow_to_only_widens():
    clock = FakeClock()
    bucket = TokenBucket(1.0, clock=clock, sleep=clock.sleep)
    bucket.slow_to(4.0)
    assert bucket.interval == 4.0
    bucket.slow_to(0.1)                   # never speeds up past configured rate
    assert bucket.interval == 4.0


def _session(handler, respect_robots=False):
    return PoliteSession(
        "ds-corpus/test (contact: test@example.com)",
        rate_per_second=1000,
        respect_robots=respect_robots,
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )


def test_retry_on_429_then_success():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, text="ok")

    with _session(handler) as s:
        assert s.get("https://example.com/x").text == "ok"
    assert calls["n"] == 3


def test_retries_exhausted_raises():
    def handler(request):
        return httpx.Response(503)

    with _session(handler) as s, pytest.raises(httpx.HTTPStatusError):
        s.get("https://example.com/x")


def test_404_raises_without_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404)

    with _session(handler) as s, pytest.raises(httpx.HTTPStatusError):
        s.get("https://example.com/x")
    assert calls["n"] == 1                # 404 is not retryable


def test_robots_disallow_blocks_fetch():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, text="should not be reachable")

    with _session(handler, respect_robots=True) as s:
        assert s.get("https://example.com/open/page").status_code == 200
        with pytest.raises(RobotsDisallowed):
            s.get("https://example.com/private/page")


def test_unreachable_robots_fails_polite():
    def handler(request):
        if request.url.path == "/robots.txt":
            raise httpx.ConnectError("nope")
        return httpx.Response(200, text="body")

    with _session(handler, respect_robots=True) as s:
        with pytest.raises(RobotsDisallowed):
            s.get("https://example.com/anything")


def test_user_agent_carries_contact():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200)

    with _session(handler) as s:
        s.get("https://example.com/")
    assert "@" in seen["ua"]
