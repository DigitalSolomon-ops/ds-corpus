"""Polite HTTP layer.

One session per source run: descriptive User-Agent with contact address,
token-bucket rate limiting, robots.txt respect (including crawl-delay, which
may only slow us down further), and bounded retries with backoff. Politeness
is a governing constraint — nothing here may be bypassed by adapters.
"""

from __future__ import annotations

import time
import urllib.robotparser
from urllib.parse import urlsplit

import httpx

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 3


class TokenBucket:
    """Single-consumer rate limiter. Clock and sleep are injectable for tests."""

    def __init__(
        self,
        rate_per_second: float,
        clock=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate must be positive")
        self.interval = 1.0 / rate_per_second
        self._clock = clock
        self._sleep = sleep
        self._next_allowed: float | None = None

    def acquire(self) -> None:
        now = self._clock()
        if self._next_allowed is not None and now < self._next_allowed:
            self._sleep(self._next_allowed - now)
            now = self._next_allowed
        self._next_allowed = now + self.interval

    def slow_to(self, min_interval: float) -> None:
        """Widen the interval (e.g. robots crawl-delay). Never narrows it."""
        if min_interval > self.interval:
            self.interval = min_interval


class RobotsCache:
    """Per-host robots.txt, fetched once through the same polite client."""

    def __init__(self, client: httpx.Client, user_agent: str) -> None:
        self._client = client
        self._ua = user_agent
        self._parsers: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _parser(self, url: str):
        host = urlsplit(url).netloc
        if host not in self._parsers:
            rp = urllib.robotparser.RobotFileParser()
            robots_url = f"{urlsplit(url).scheme}://{host}/robots.txt"
            try:
                resp = self._client.get(robots_url)
                if resp.status_code >= 400:
                    # No robots.txt (or it errors): nothing is disallowed,
                    # but we still keep our own rate limit.
                    rp.parse([])
                else:
                    rp.parse(resp.text.splitlines())
            except httpx.HTTPError:
                # Unreachable robots.txt: fail polite — treat as disallow-all
                # rather than assume permission we couldn't check.
                self._parsers[host] = None
                return None
            self._parsers[host] = rp
        return self._parsers[host]

    def allowed(self, url: str) -> bool:
        rp = self._parser(url)
        if rp is None:
            return False
        return rp.can_fetch(self._ua, url)

    def crawl_delay(self, url: str) -> float | None:
        rp = self._parser(url)
        if rp is None:
            return None
        try:
            d = rp.crawl_delay(self._ua)
        except AttributeError:
            return None
        return float(d) if d else None


class RobotsDisallowed(Exception):
    pass


class PoliteSession:
    """The only way adapters talk to the network."""

    def __init__(
        self,
        user_agent: str,
        rate_per_second: float,
        respect_robots: bool = True,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ) -> None:
        self.user_agent = user_agent
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=60.0,
            follow_redirects=True,
            transport=transport,
        )
        self._bucket = TokenBucket(rate_per_second, sleep=sleep)
        self._robots = RobotsCache(self._client, user_agent) if respect_robots else None
        self._sleep = sleep

    def get(self, url: str, **kwargs) -> httpx.Response:
        if self._robots is not None:
            if not self._robots.allowed(url):
                raise RobotsDisallowed(url)
            delay = self._robots.crawl_delay(url)
            if delay:
                self._bucket.slow_to(delay)

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            self._bucket.acquire()
            try:
                resp = self._client.get(url, **kwargs)
            except httpx.TransportError as e:
                last_exc = e
                self._sleep(2**attempt)
                continue
            if resp.status_code in RETRYABLE_STATUS and attempt < MAX_RETRIES:
                retry_after = resp.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else 2**attempt
                except ValueError:
                    wait = 2**attempt
                self._sleep(min(wait, 120))
                continue
            resp.raise_for_status()
            return resp
        raise last_exc if last_exc else RuntimeError(f"retries exhausted: {url}")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteSession":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
