"""Polite, allowlisted HTTP fetching for public data sources (§65, §137).

Everything external goes through :class:`SafeFetcher`:

* **https only, allowlisted hosts only** — a source cannot redirect us anywhere;
* **robots.txt is honoured** for pages we read without an API (a missing
  robots.txt means allowed, a 5xx or timeout means *not* allowed: fail closed);
* **one request per host per interval** — official sites ask for fair access
  (SEC: at most 10 requests per second), we stay far below;
* an identifying **User-Agent with a contact email**, as the SEC requires;
* conditional requests (ETag / Last-Modified) so unchanged files cost nothing.

The body is returned as bytes and is *untrusted data*: callers parse it with
strict parsers and never execute or follow instructions inside it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

DEFAULT_USER_AGENT = "HyverionQuantAI/0.2"
MAX_BODY_BYTES = 8 * 1024 * 1024


class FetchError(RuntimeError):
    """A fetch that must not be retried blindly. ``code`` is stable for the UI."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(slots=True)
class _Cached:
    etag: str | None
    last_modified: str | None
    body: bytes


@dataclass(slots=True)
class SafeFetcher:
    allowed_hosts: frozenset[str]
    contact_email: str | None = None
    min_interval_seconds: float = 1.0
    timeout_seconds: float = 20.0
    transport: httpx.AsyncBaseTransport | None = None
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], object] = asyncio.sleep
    _last_request: dict[str, float] = field(default_factory=dict)
    _robots: dict[str, RobotFileParser | None] = field(default_factory=dict)
    _cache: dict[str, _Cached] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    @property
    def user_agent(self) -> str:
        if self.contact_email:
            return f"{DEFAULT_USER_AGENT} {self.contact_email}"
        return DEFAULT_USER_AGENT

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        check_robots: bool = True,
    ) -> bytes:
        host = self._validate(url)
        if check_robots and not await self._robots_allow(host, url):
            raise FetchError("robots_disallowed", url)
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            await self._throttle(host)
            cache_key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
            cached = self._cache.get(cache_key)
            request_headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip"}
            if cached and cached.etag:
                request_headers["If-None-Match"] = cached.etag
            if cached and cached.last_modified:
                request_headers["If-Modified-Since"] = cached.last_modified
            request_headers.update(headers or {})
            response = await self._request(url, request_headers, params)
            if response.status_code == 304 and cached is not None:
                return cached.body
            if response.status_code in {401, 403}:
                raise FetchError("source_auth_failed", str(response.status_code))
            if response.status_code == 429:
                raise FetchError("source_rate_limited", url)
            if response.status_code >= 400:
                raise FetchError("source_http_error", str(response.status_code))
            body = response.content
            if len(body) > MAX_BODY_BYTES:
                raise FetchError("source_body_too_large", str(len(body)))
            self._cache[cache_key] = _Cached(
                response.headers.get("etag"), response.headers.get("last-modified"), body
            )
            return body

    def _validate(self, url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise FetchError("source_scheme_forbidden", parts.scheme)
        host = (parts.hostname or "").lower()
        if host not in self.allowed_hosts:
            raise FetchError("source_host_not_allowlisted", host)
        return host

    async def _throttle(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self.min_interval_seconds - (self.monotonic() - last)
            if wait > 0:
                await self.sleep(wait)  # type: ignore[misc]
        self._last_request[host] = self.monotonic()

    async def _request(
        self, url: str, headers: dict[str, str], params: dict[str, str] | None
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.get(url, headers=headers, params=params)
                target_header = response.headers.get("location")
                if response.status_code in {301, 302, 303, 307, 308} and target_header:
                    # Follow one hop only when it stays on an allowlisted https host.
                    target = target_header
                    self._validate(str(httpx.URL(url).join(target)))
                    response = await client.get(
                        str(httpx.URL(url).join(target)), headers=headers, params=params
                    )
                return response
        except httpx.TimeoutException as exc:
            raise FetchError("source_timeout", url) from exc
        except httpx.HTTPError as exc:
            raise FetchError("source_unreachable", type(exc).__name__) from exc

    async def _robots_allow(self, host: str, url: str) -> bool:
        if host not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            robots_url = f"https://{host}/robots.txt"
            try:
                response = await self._request(
                    robots_url, {"User-Agent": self.user_agent}, None
                )
            except FetchError:
                parser = None  # unreachable robots.txt: do not crawl (fail closed)
            else:
                status = response.status_code
                is_html = "html" in response.headers.get("content-type", "")
                if status in {404, 410} or (status < 400 and is_html):
                    # No robots.txt (or an HTML "not found" page): no rules.
                    parser.parse([])  # type: ignore[union-attr]
                elif status >= 400:
                    parser = None
                else:
                    parser.parse(response.text.splitlines())  # type: ignore[union-attr]
            self._robots[host] = parser
        rules = self._robots[host]
        return rules is not None and rules.can_fetch(DEFAULT_USER_AGENT, url)
