"""News intelligence for QQQ (§28-§34): dedup, entity resolution, materiality, decay.

Deterministic first: 30 copies of one story are **one** cluster (§33), a
story matters in proportion to the index weight of the companies it names
(§31), and its influence decays with age (§32). An LLM may later summarise a
cluster, but it never decides whether the story exists or how much it weighs.

All text is ``UNTRUSTED_EXTERNAL_DATA`` (§34): it is stored and scored, never
followed. Nothing here can reach the broker.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from trading_bot.market_data.base import NewsArticle
from trading_bot.schemas.common import StrictSchema

SIMILARITY_THRESHOLD = 0.55
CLUSTER_WINDOW = timedelta(hours=24)
# Half-life of a story's influence by materiality (a filing or guidance change
# outlives a routine headline). To be validated with replay (§32).
HALF_LIFE = {"HIGH": timedelta(hours=6), "MEDIUM": timedelta(hours=2), "LOW": timedelta(minutes=45)}

_STOP = frozenset(
    "a an the of to in on for and or with by at from as is are was be its it this that "
    "after before over under says said new amid vs than into".split()
)
# Deterministic materiality cues. Specific phrases first.
_MATERIAL: tuple[tuple[str, str], ...] = (
    ("guidance", "HIGH"),
    ("earnings", "HIGH"),
    ("results", "MEDIUM"),
    ("revenue", "MEDIUM"),
    ("acquisition", "HIGH"),
    ("acquire", "HIGH"),
    ("merger", "HIGH"),
    ("antitrust", "HIGH"),
    ("sec ", "MEDIUM"),
    ("lawsuit", "MEDIUM"),
    ("probe", "MEDIUM"),
    ("investigation", "MEDIUM"),
    ("export control", "HIGH"),
    ("tariff", "HIGH"),
    ("downgrade", "MEDIUM"),
    ("upgrade", "MEDIUM"),
    ("recall", "MEDIUM"),
    ("ceo", "MEDIUM"),
    ("buyback", "MEDIUM"),
    ("dividend", "LOW"),
)
_BULL = ("beat", "beats", "raises", "surge", "record", "upgrade", "strong", "tops", "jumps")
_BEAR = ("miss", "misses", "cuts", "plunge", "downgrade", "weak", "probe", "falls", "slump")


class NewsCluster(StrictSchema):
    cluster_id: str
    headline: str
    symbols: tuple[str, ...]
    sources: tuple[str, ...]
    article_ids: tuple[str, ...]
    first_published_at: datetime
    last_published_at: datetime
    materiality: str  # HIGH | MEDIUM | LOW
    sentiment: int  # -1, 0, 1 from deterministic cues
    qqq_relevance: Decimal  # sum of index weights of the named companies (0-1)
    copies: int

    def decay(self, now: datetime) -> Decimal:
        """Influence left now: 1 at publication, halves every half-life."""

        age = max(timedelta(0), now - self.first_published_at)
        half = HALF_LIFE.get(self.materiality, HALF_LIFE["LOW"])
        return Decimal(str(round(math.pow(0.5, age / half), 6)))


def _tokens(text: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return frozenset(word for word in words if word not in _STOP and len(word) > 1)


def similarity(a: str, b: str) -> float:
    left, right = _tokens(a), _tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def materiality(text: str) -> str:
    lowered = f" {text.lower()} "
    levels = [level for cue, level in _MATERIAL if cue in lowered]
    if "HIGH" in levels:
        return "HIGH"
    return "MEDIUM" if "MEDIUM" in levels else "LOW"


def sentiment(text: str) -> int:
    words = set(re.findall(r"[a-z]+", text.lower()))
    score = sum(1 for cue in _BULL if cue in words) - sum(1 for cue in _BEAR if cue in words)
    return (score > 0) - (score < 0)


def cluster_news(
    articles: Sequence[NewsArticle], weights: dict[str, Decimal]
) -> list[NewsCluster]:
    """Group near-duplicate stories and score each group once."""

    groups: list[list[NewsArticle]] = []
    for article in sorted(articles, key=lambda item: item.created_at):
        for group in groups:
            head = group[0]
            same_story = similarity(article.headline, head.headline) >= SIMILARITY_THRESHOLD
            shared = not article.symbols or not head.symbols or set(article.symbols) & set(
                head.symbols
            )
            if (
                same_story
                and shared
                and article.created_at - head.created_at <= CLUSTER_WINDOW
            ):
                group.append(article)
                break
        else:
            groups.append([article])
    clusters: list[NewsCluster] = []
    for group in groups:
        head = group[0]
        symbols = tuple(sorted({s for item in group for s in item.symbols}))
        text = " ".join(f"{item.headline} {item.summary}" for item in group)
        relevance = sum((weights.get(symbol, Decimal("0")) for symbol in symbols), Decimal("0"))
        if "QQQ" in symbols:
            relevance = Decimal("1")
        digest = hashlib.sha256(
            f"{head.headline.lower()}|{head.created_at.isoformat()}".encode()
        ).hexdigest()[:16]
        clusters.append(
            NewsCluster(
                cluster_id=f"news:{digest}",
                headline=head.headline,
                symbols=symbols,
                sources=tuple(sorted({item.source for item in group})),
                article_ids=tuple(item.article_id for item in group),
                first_published_at=head.created_at,
                last_published_at=group[-1].created_at,
                materiality=materiality(text),
                sentiment=sentiment(text),
                qqq_relevance=min(Decimal("1"), relevance),
                copies=len(group),
            )
        )
    return sorted(clusters, key=lambda item: item.first_published_at, reverse=True)
