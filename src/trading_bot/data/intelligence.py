"""News and social intelligence plus read-only official social connectors.

External text is untrusted data: it is delimited by the sanitizer before it can be
used as evidence, never treated as instructions. The social connectors do not
scrape web pages or perform write actions. They normalize official API responses
into the ``SocialPost`` contract and fail closed on malformed, stale or oversized
responses.
"""

from __future__ import annotations

import base64
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from defusedxml import ElementTree
from pydantic import Field, field_validator

from trading_bot.core.clock import Clock, SystemClock
from trading_bot.data.sources import NEWS_ALLOWLIST
from trading_bot.schemas.assessments import (
    NewsAssessment,
    SocialAssessment,
)
from trading_bot.schemas.common import Evidence, Score, StrictSchema
from trading_bot.security.sanitizer import delimit_untrusted_content


class NewsItem(StrictSchema):
    item_id: str
    asset: str
    source: str
    title: str = Field(min_length=1)
    content: str = ""
    canonical_url: str | None = None
    published_at: datetime
    received_at: datetime
    credibility: Score = Decimal("50")

    @field_validator("published_at", "received_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("external timestamps must be timezone-aware")
        return value.astimezone(UTC)


class SocialPost(StrictSchema):
    post_id: str
    asset: str
    source: str
    author_id: str
    text: str = Field(min_length=1)
    published_at: datetime
    engagement: int = Field(ge=0)

    @field_validator("published_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("external timestamps must be timezone-aware")
        return value.astimezone(UTC)


class RSSNewsAdapter:
    """Read-only RSS/Atom collector; it never executes instructions in feed text."""

    def __init__(
        self,
        clock: Clock | None = None,
        *,
        max_payload_bytes: int = 2_000_000,
        max_redirects: int = 3,
    ) -> None:
        self._clock = clock or SystemClock()
        self._max_payload_bytes = max_payload_bytes
        self._max_redirects = max_redirects

    async def fetch(
        self,
        url: str,
        *,
        source: str,
        asset: str,
        allowed_hosts: frozenset[str] | None = None,
    ) -> tuple[NewsItem, ...]:
        parsed_url = httpx.URL(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.host:
            raise ValueError("feed URL must be an absolute HTTP(S) URL")
        async with httpx.AsyncClient(
            timeout=10,
            follow_redirects=True,
            max_redirects=self._max_redirects,
            headers={"Accept": "application/rss+xml, application/atom+xml, application/xml"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
        if len(response.content) > self._max_payload_bytes:
            raise ValueError("feed payload exceeds configured size limit")
        final_host = response.url.host
        if allowed_hosts is not None and final_host not in allowed_hosts:
            raise ValueError("feed redirect left the reviewed source host")
        content_type = response.headers.get("content-type", "").lower()
        if content_type and not any(
            value in content_type
            for value in ("xml", "rss", "atom", "text/plain", "application/octet-stream")
        ):
            raise ValueError("feed response has an unsupported content type")
        received = self._clock.now()
        root = ElementTree.fromstring(response.content)
        items: list[NewsItem] = []
        for element in root.iter():
            if _local_name(element.tag) not in {"item", "entry"}:
                continue
            values = {
                _local_name(child.tag): (child.text or "").strip()
                for child in element
                if child.text
            }
            title = values.get("title", "")
            canonical_url = _element_link(element)
            published_raw = values.get("pubDate") or values.get("published") or values.get(
                "updated", ""
            )
            if not title or not published_raw:
                continue
            try:
                published = _parse_external_datetime(published_raw)
            except ValueError:
                continue
            item_id = (
                values.get("guid")
                or values.get("id")
                or canonical_url
                or f"{source}:{title}"
            )
            items.append(
                NewsItem(
                    item_id=item_id,
                    asset=asset,
                    source=source,
                    title=title,
                    content=values.get("description") or values.get("summary", ""),
                    canonical_url=canonical_url,
                    published_at=published,
                    received_at=received,
                )
            )
        return tuple(items)


class AllowlistedRSSCollector:
    """Resolve only configured, reviewed source identifiers to RSS/Atom URLs.

    URLs are intentionally injected from configuration rather than guessed from
    a source name.  This makes ToS/robots review and provenance explicit and
    prevents a typo or an untrusted URL from becoming a collector.
    """

    def __init__(
        self,
        feeds: dict[str, str],
        *,
        adapter: RSSNewsAdapter | None = None,
    ) -> None:
        self._feeds = dict(feeds)
        self._adapter = adapter or RSSNewsAdapter()

    async def fetch(
        self,
        source_id: str,
        *,
        asset: str,
    ) -> tuple[NewsItem, ...]:
        if source_id not in NEWS_ALLOWLIST:
            raise ValueError(f"source is not allowlisted: {source_id}")
        url = self._feeds.get(source_id)
        if not url:
            raise ValueError(f"no reviewed feed configured for source: {source_id}")
        parsed = httpx.URL(url)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("feed URL must be an absolute HTTP(S) URL")
        return await self._adapter.fetch(
            url,
            source=source_id,
            asset=asset,
            allowed_hosts=frozenset({parsed.host}),
        )


def deduplicate_news(items: tuple[NewsItem, ...]) -> tuple[NewsItem, ...]:
    """Keep first-seen provenance while removing repeated feed records."""

    seen: set[str] = set()
    unique: list[NewsItem] = []
    for item in items:
        if item.item_id in seen:
            continue
        seen.add(item.item_id)
        unique.append(item)
    return tuple(unique)


class NewsIntelligence:
    agent_id = "news"
    version = "1.0.0"

    def assess(
        self,
        items: tuple[NewsItem, ...],
        *,
        asset: str,
        now: datetime,
        stale_after: timedelta = timedelta(hours=24),
    ) -> NewsAssessment:
        recent = [item for item in items if item.asset == asset]
        if not recent:
            return NewsAssessment(
                **_assessment_base(self.agent_id, self.version, asset, now),
                status="insufficient_data",
                sentiment="unknown",
                severity=Decimal("0"),
                stale_items=0,
                duplicate_items=0,
                limitations=("No news items with verified asset mapping.",),
            )
        duplicate_count = len(recent) - len({item.item_id for item in recent})
        stale_count = sum(1 for item in recent if now - item.published_at > stale_after)
        active = [item for item in recent if now - item.published_at <= stale_after]
        if not active:
            return NewsAssessment(
                **_assessment_base(self.agent_id, self.version, asset, now),
                status="insufficient_data",
                sentiment="unknown",
                severity=Decimal("0"),
                stale_items=stale_count,
                duplicate_items=duplicate_count,
                limitations=("All received items are stale.",),
            )
        scores = [_sentiment_score(item.title + " " + item.content) for item in active]
        sentiment = _aggregate_sentiment(scores)
        severity = min(Decimal("100"), max(item.credibility for item in active))
        evidence = tuple(
            Evidence(
                source=item.source,
                category="news",
                summary=_evidence_summary(item.title, item.source),
                observed_at=item.received_at,
                strength=item.credibility,
            )
            for item in active[:10]
        )
        return NewsAssessment(
            **_assessment_base(self.agent_id, self.version, asset, now),
            status="available",
            sentiment=sentiment,
            severity=severity,
            stale_items=stale_count,
            duplicate_items=duplicate_count,
            evidence=evidence,
            limitations=(
                "Keyword sentiment is supplementary evidence, not a trading instruction.",
            ),
        )


class SocialIntelligence:
    agent_id = "social"
    version = "1.0.0"

    def assess(
        self,
        posts: tuple[SocialPost, ...],
        *,
        asset: str,
        now: datetime,
        stale_after: timedelta = timedelta(hours=6),
    ) -> SocialAssessment:
        active = [
            post
            for post in posts
            if post.asset == asset and now - post.published_at <= stale_after
        ]
        if not active:
            return SocialAssessment(
                **_assessment_base(self.agent_id, self.version, asset, now),
                status="insufficient_data",
                sentiment="unknown",
                manipulation_risk=Decimal("0"),
                unusual_activity=False,
                limitations=("No fresh social records with verified asset mapping.",),
            )
        author_counts = Counter(post.author_id for post in active)
        repeated_author_ratio = Decimal(max(author_counts.values())) / Decimal(len(active))
        spam_count = sum(1 for post in active if _looks_like_spam(post.text))
        manipulation_risk = min(
            Decimal("100"),
            Decimal("100") * max(repeated_author_ratio, Decimal(spam_count) / Decimal(len(active))),
        )
        scores = [_sentiment_score(post.text) for post in active]
        return SocialAssessment(
            **_assessment_base(self.agent_id, self.version, asset, now),
            status="available",
            sentiment=_aggregate_sentiment(scores),
            manipulation_risk=manipulation_risk,
            unusual_activity=len(active) >= 10 or max(post.engagement for post in active) >= 1000,
            evidence=tuple(
                Evidence(
                    source=post.source,
                    category="social",
                    summary=_evidence_summary(post.text, post.source),
                    observed_at=post.published_at,
                    strength=min(Decimal("100"), Decimal(post.engagement) / Decimal("10")),
                )
                for post in active[:10]
            ),
            limitations=(
                "Public sentiment is noisy and can be manipulated; it is never decisive.",
            ),
        )


def _assessment_base(agent_id: str, version: str, asset: str, now: datetime) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "agent_version": version,
        "asset": asset,
        "assessed_at": now.astimezone(UTC),
        "confidence": Decimal("50"),
    }


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


def _element_link(element: Any) -> str | None:
    for child in element:
        if _local_name(child.tag) != "link":
            continue
        href = (child.attrib.get("href") or child.text or "").strip()
        if href.startswith(("http://", "https://")):
            return href
    return None


def _parse_external_datetime(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("external timestamp must include timezone")
    return parsed.astimezone(UTC)


BULLISH_WORDS = ("surge", "bullish", "beat", "raises guidance", "upgrade", "growth")
BEARISH_WORDS = ("crash", "bearish", "miss", "cuts guidance", "downgrade", "probe")


def _sentiment_score(text: str) -> int:
    lowered = text.lower()
    bullish = sum(word in lowered for word in BULLISH_WORDS)
    bearish = sum(word in lowered for word in BEARISH_WORDS)
    return bullish - bearish


def _aggregate_sentiment(scores: list[int]) -> str:
    total = sum(scores)
    if total > 0:
        return "bullish"
    if total < 0:
        return "bearish"
    return "neutral"


def _looks_like_spam(text: str) -> bool:
    lowered = text.lower()
    return "guaranteed" in lowered or "pump" in lowered or lowered.count("!") >= 4


def _evidence_summary(content: str, source: str) -> str:
    return delimit_untrusted_content(content, source)[:1000]


# --- Read-only official social connectors -----------------------------------


class SocialConnectorError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.retryable = retryable


class XOfficialConnector:
    """Official X API v2 recent-search connector using an app-only bearer token."""

    def __init__(
        self,
        bearer_token: str,
        *,
        base_url: str = "https://api.x.com",
        max_payload_bytes: int = 2_000_000,
    ) -> None:
        if not bearer_token.strip():
            raise ValueError("X bearer token is required")
        parsed = httpx.URL(base_url)
        if parsed.scheme != "https" or not parsed.host:
            raise ValueError("X API base URL must use HTTPS")
        self._token = bearer_token
        self._base_url = base_url.rstrip("/")
        self._max_payload_bytes = max_payload_bytes

    async def fetch_recent(
        self,
        *,
        query: str,
        asset: str,
        max_results: int = 10,
    ) -> tuple[SocialPost, ...]:
        if not query.strip():
            raise ValueError("X search query is required")
        if not 10 <= max_results <= 100:
            raise ValueError("X recent search max_results must be between 10 and 100")
        params = {
            "query": query.strip(),
            "max_results": str(max_results),
            "tweet.fields": "created_at,public_metrics,author_id",
            "expansions": "author_id",
            "user.fields": "username",
        }
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.get(
                    f"{self._base_url}/2/tweets/search/recent",
                    params=params,
                    headers={"Authorization": f"Bearer {self._token}"},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise SocialConnectorError(
                "x_http_error",
                retryable=status == 429 or status >= 500,
                detail=str(status),
            ) from exc
        except httpx.RequestError as exc:
            raise SocialConnectorError("x_network_error", retryable=True) from exc
        return _parse_x_response(response, asset=asset, max_payload_bytes=self._max_payload_bytes)


class RedditOfficialConnector:
    """Read-only Reddit OAuth client-credentials connector."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        oauth_url: str = "https://www.reddit.com/api/v1/access_token",
        api_url: str = "https://oauth.reddit.com",
        user_agent: str = "hyverion-quant-ai/0.1 (read-only market research)",
        max_payload_bytes: int = 2_000_000,
    ) -> None:
        if not client_id.strip() or not client_secret.strip():
            raise ValueError("Reddit client credentials are required")
        for value, label in ((oauth_url, "Reddit OAuth URL"), (api_url, "Reddit API URL")):
            parsed = httpx.URL(value)
            if parsed.scheme != "https" or not parsed.host:
                raise ValueError(f"{label} must use HTTPS")
        self._client_id = client_id
        self._client_secret = client_secret
        self._oauth_url = oauth_url
        self._api_url = api_url.rstrip("/")
        self._user_agent = user_agent
        self._max_payload_bytes = max_payload_bytes
        self._access_token: str | None = None
        self._token_expires_at = 0.0

    async def fetch_search(
        self,
        *,
        query: str,
        asset: str,
        limit: int = 25,
    ) -> tuple[SocialPost, ...]:
        if not query.strip():
            raise ValueError("Reddit search query is required")
        if not 1 <= limit <= 100:
            raise ValueError("Reddit search limit must be between 1 and 100")
        token = await self._ensure_access_token()
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.get(
                    f"{self._api_url}/search",
                    params={
                        "q": query.strip(),
                        "sort": "new",
                        "limit": str(limit),
                        "raw_json": "1",
                    },
                    headers={
                        "Authorization": f"Bearer {token}",
                        "User-Agent": self._user_agent,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise SocialConnectorError(
                "reddit_http_error",
                retryable=status == 429 or status >= 500,
                detail=str(status),
            ) from exc
        except httpx.RequestError as exc:
            raise SocialConnectorError("reddit_network_error", retryable=True) from exc
        return _parse_reddit_response(
            response, asset=asset, max_payload_bytes=self._max_payload_bytes
        )

    async def _ensure_access_token(self) -> str:
        if self._access_token and time.monotonic() < self._token_expires_at:
            return self._access_token
        basic = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode("ascii")
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.post(
                    self._oauth_url,
                    data={"grant_type": "client_credentials"},
                    headers={
                        "Authorization": f"Basic {basic}",
                        "User-Agent": self._user_agent,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise SocialConnectorError(
                "reddit_auth_error", retryable=status == 429 or status >= 500, detail=str(status)
            ) from exc
        except httpx.RequestError as exc:
            raise SocialConnectorError("reddit_auth_network_error", retryable=True) from exc
        if len(response.content) > self._max_payload_bytes:
            raise SocialConnectorError("reddit_auth_payload_too_large", retryable=False)
        payload = _json_object(response, "reddit_auth_invalid_json")
        token = payload.get("access_token")
        expires_in = payload.get("expires_in")
        if not isinstance(token, str) or not token.strip():
            raise SocialConnectorError("reddit_auth_missing_token", retryable=False)
        self._access_token = token
        self._token_expires_at = time.monotonic() + max(60, int(expires_in or 3600) - 60)
        return token


def _parse_x_response(
    response: httpx.Response, *, asset: str, max_payload_bytes: int
) -> tuple[SocialPost, ...]:
    if len(response.content) > max_payload_bytes:
        raise SocialConnectorError("x_payload_too_large", retryable=False)
    payload = _json_object(response, "x_invalid_json")
    users = {
        str(user.get("id")): str(user.get("username") or user.get("id") or "unknown")
        for user in payload.get("includes", {}).get("users", [])
        if isinstance(user, dict)
    }
    posts: list[SocialPost] = []
    for item in payload.get("data", []):
        if not isinstance(item, dict):
            continue
        created_at = item.get("created_at")
        if not isinstance(created_at, str):
            continue
        try:
            published = datetime.fromisoformat(created_at.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            continue
        metrics = item.get("public_metrics")
        metrics = metrics if isinstance(metrics, dict) else {}
        engagement = sum(
            int(metrics.get(key) or 0)
            for key in ("like_count", "reply_count", "retweet_count", "quote_count")
        )
        post_id = item.get("id")
        text = item.get("text")
        author_id = str(item.get("author_id") or "unknown")
        if not isinstance(post_id, str) or not isinstance(text, str) or not text.strip():
            continue
        posts.append(
            SocialPost(
                post_id=post_id,
                asset=asset,
                source="x-official",
                author_id=users.get(author_id, author_id),
                text=text,
                published_at=published,
                engagement=max(0, engagement),
            )
        )
    return tuple(posts)


def _parse_reddit_response(
    response: httpx.Response, *, asset: str, max_payload_bytes: int
) -> tuple[SocialPost, ...]:
    if len(response.content) > max_payload_bytes:
        raise SocialConnectorError("reddit_payload_too_large", retryable=False)
    payload = _json_object(response, "reddit_invalid_json")
    listing = payload.get("data")
    children = listing.get("children") if isinstance(listing, dict) else None
    if not isinstance(children, list):
        return ()
    posts: list[SocialPost] = []
    for child in children:
        data = child.get("data") if isinstance(child, dict) else None
        if not isinstance(data, dict):
            continue
        post_id = str(data.get("id") or data.get("name") or "")
        title = str(data.get("title") or "").strip()
        body = str(data.get("selftext") or "").strip()
        created = data.get("created_utc")
        if not post_id or not title or not isinstance(created, (int, float)):
            continue
        published = datetime.fromtimestamp(created, tz=UTC)
        engagement = max(0, int(data.get("score") or 0) + int(data.get("num_comments") or 0))
        posts.append(
            SocialPost(
                post_id=post_id,
                asset=asset,
                source="reddit-official",
                author_id=str(data.get("author_fullname") or data.get("author") or "unknown"),
                text=f"{title}\n{body}".strip(),
                published_at=published,
                engagement=engagement,
            )
        )
    return tuple(posts)


def _json_object(response: httpx.Response, error_code: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise SocialConnectorError(error_code, retryable=False) from exc
    if not isinstance(payload, dict):
        raise SocialConnectorError(error_code, retryable=False)
    return payload
