"""Bounded, read-only collection of reviewed external intelligence.

The collector is intentionally separate from the AI runtime.  It may fetch a
reviewed RSS feed or an official social API, normalize the result, persistable
records, and a provider-safe context envelope.  It never scrapes arbitrary
HTML, receives exchange credentials, or makes a trading decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import SecretStr

from trading_bot.config.models import ExternalDataConfig, SecretSettings
from trading_bot.core.clock import Clock
from trading_bot.data.intelligence import (
    AllowlistedRSSCollector,
    NewsIntelligence,
    NewsItem,
    RedditOfficialConnector,
    SocialConnectorError,
    SocialIntelligence,
    SocialPost,
    XOfficialConnector,
    deduplicate_news,
)
from trading_bot.schemas.observability import SourceRunRecord
from trading_bot.security.sanitizer import delimit_untrusted_content
from trading_bot.security.secrets import KeyringSecretStore

SourceRun = SourceRunRecord


@dataclass(frozen=True, slots=True)
class ExternalCollectionResult:
    """The complete, bounded result of one external collection pass."""

    news_items: tuple[NewsItem, ...] = ()
    social_posts: tuple[SocialPost, ...] = ()
    context: dict[str, Any] | None = None
    errors: tuple[str, ...] = ()
    source_runs: tuple[SourceRun, ...] = ()


class ExternalIntelligenceCollector:
    """Collect only configured sources and fail closed per source."""

    def __init__(
        self,
        config: ExternalDataConfig,
        secrets: SecretSettings,
        clock: Clock,
        *,
        secret_store: KeyringSecretStore | None = None,
        rss_collector: AllowlistedRSSCollector | None = None,
    ) -> None:
        self._config = config
        self._secrets = secrets
        self._clock = clock
        self._secret_store = secret_store or KeyringSecretStore()
        self._rss_collector = rss_collector or AllowlistedRSSCollector(config.news_feeds)

    async def collect(self, *, asset: str, now: datetime | None = None) -> ExternalCollectionResult:
        timestamp = now or self._clock.now()
        news_items: list[NewsItem] = []
        social_posts: list[SocialPost] = []
        errors: list[str] = []
        source_runs: list[SourceRun] = []

        if self._config.news_provider == "rss":
            reviewed = set(self._config.news_reviewed_sources)
            for source_id in sorted(self._config.news_feeds):
                started_at = self._clock.now()
                if source_id not in reviewed:
                    errors.append(f"news:{source_id}:not_reviewed")
                    source_runs.append(
                        SourceRun(
                            source_id=source_id,
                            status="SKIPPED",
                            records_count=0,
                            started_at=started_at,
                            finished_at=self._clock.now(),
                            error="not_reviewed",
                        )
                    )
                    continue
                try:
                    fetched = await self._rss_collector.fetch(source_id, asset=asset)
                    news_items.extend(fetched)
                    source_runs.append(
                        SourceRun(
                            source_id=source_id,
                            status="SUCCEEDED",
                            records_count=len(fetched),
                            started_at=started_at,
                            finished_at=self._clock.now(),
                        )
                    )
                except Exception as exc:  # connector failures never open a trade
                    errors.append(f"news:{source_id}:{type(exc).__name__}")
                    source_runs.append(
                        SourceRun(
                            source_id=source_id,
                            status="FAILED",
                            records_count=0,
                            started_at=started_at,
                            finished_at=self._clock.now(),
                            error=type(exc).__name__,
                        )
                    )
        elif self._config.news_provider not in {"disabled", ""}:
            errors.append("news:unsupported_provider")

        social_source = (
            "social-x-official"
            if self._config.social_provider == "x"
            else "social-reddit-official"
            if self._config.social_provider == "reddit"
            else None
        )
        social_started = self._clock.now()
        try:
            if self._config.social_provider == "x":
                token = self._secret("x_bearer_token")
                if token:
                    social_posts.extend(
                        await XOfficialConnector(token).fetch_recent(
                            query=f'({asset.split("/")[0]}) lang:en -is:retweet',
                            asset=asset,
                        )
                    )
                else:
                    errors.append("social:x:credential_missing")
            elif self._config.social_provider == "reddit":
                client_id = self._secret("reddit_client_id")
                client_secret = self._secret("reddit_client_secret")
                if client_id and client_secret:
                    social_posts.extend(
                        await RedditOfficialConnector(client_id, client_secret).fetch_search(
                            query=asset.split("/")[0], asset=asset
                        )
                    )
                else:
                    errors.append("social:reddit:credential_missing")
            elif self._config.social_provider not in {"disabled", ""}:
                errors.append("social:unsupported_provider")
        except SocialConnectorError as exc:
            errors.append(f"social:{exc.code}")
        except (ValueError, TypeError) as exc:
            errors.append(f"social:{type(exc).__name__}")
        if social_source is not None:
            social_error = next(
                (
                    item.removeprefix("social:")
                    for item in errors
                    if item.startswith("social:")
                ),
                None,
            )
            source_runs.append(
                SourceRun(
                    source_id=social_source,
                    status="FAILED" if social_error else "SUCCEEDED",
                    records_count=len(social_posts),
                    started_at=social_started,
                    finished_at=self._clock.now(),
                    error=social_error,
                )
            )

        unique_news = deduplicate_news(tuple(news_items))
        news_assessment = NewsIntelligence().assess(unique_news, asset=asset, now=timestamp)
        social_assessment = SocialIntelligence().assess(
            tuple(social_posts), asset=asset, now=timestamp
        )
        return ExternalCollectionResult(
            news_items=unique_news,
            social_posts=tuple(social_posts),
            context={
                "news": [self._news_payload(item) for item in unique_news[:40]],
                "social": [self._social_payload(item) for item in social_posts[:40]],
                "assessments": {
                    "news": news_assessment.model_dump(mode="json"),
                    "social": social_assessment.model_dump(mode="json"),
                },
                "errors": tuple(errors),
                "collected_at": timestamp.isoformat(),
            },
            errors=tuple(errors),
            source_runs=tuple(source_runs),
        )

    def _secret(self, name: str) -> str | None:
        configured = getattr(self._secrets, name, None)
        if isinstance(configured, SecretStr):
            value = configured.get_secret_value().strip()
            if value:
                return value
        stored = self._secret_store.get(f"source:{name}")
        return stored.strip() if stored else None

    @staticmethod
    def _news_payload(item: NewsItem) -> dict[str, Any]:
        return {
            "item_id": item.item_id,
            "asset": item.asset,
            "source": item.source,
            "title": delimit_untrusted_content(item.title, item.source),
            "content": delimit_untrusted_content(item.content, item.source),
            "canonical_url": item.canonical_url,
            "published_at": item.published_at.isoformat(),
            "received_at": item.received_at.isoformat(),
            "credibility": str(item.credibility),
        }

    @staticmethod
    def _social_payload(item: SocialPost) -> dict[str, Any]:
        return {
            "post_id": item.post_id,
            "asset": item.asset,
            "source": item.source,
            "author_id": item.author_id,
            "text": delimit_untrusted_content(item.text, item.source),
            "published_at": item.published_at.isoformat(),
            "engagement": item.engagement,
        }
