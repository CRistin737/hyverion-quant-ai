"""EvidenceGraph, ConfluenceEngine, DataQualityScore and MarketConsensusMatrix (§37-§39, §117).

Deterministic and auditable. For a **long QQQ** thesis each evidence family
scores from -100 (contradicts) to +100 (supports), with the reason written
down. Correlated families (breadth and components both describe the same
stocks) share one cap so they are not counted twice (§37). The confluence
score uses **versioned weights** and never trusts an LLM's self-reported
confidence (§38).

Nothing here decides a trade. The strategy and the critic read it; the
RiskEngine still has the final word.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from trading_bot.data.features import FeatureSet
from trading_bot.schemas.common import StrictSchema

CONFLUENCE_WEIGHTS_VERSION = "confluence-v1"
# Share of the score each family can move. Sum = 1. Reviewed via ChangeProposal only.
CONFLUENCE_WEIGHTS: dict[str, Decimal] = {
    "PRICE_STRUCTURE": Decimal("0.26"),
    "BREADTH": Decimal("0.16"),
    "COMPONENTS": Decimal("0.10"),
    "LIQUIDITY_VOLUME": Decimal("0.08"),
    "VOLATILITY": Decimal("0.10"),
    "MACRO": Decimal("0.12"),
    "RATES": Decimal("0.04"),
    "OPTIONS": Decimal("0.04"),
    "NEWS": Decimal("0.06"),
    "HISTORICAL_ANALOGS": Decimal("0.04"),
}
# Families that read the same underlying stocks: combined, never double counted.
CORRELATED = (("BREADTH", "COMPONENTS"),)
Stance = Literal["supports", "neutral", "contradicts", "unavailable"]


class EvidenceNode(StrictSchema):
    family: str
    score: Decimal  # -100..100 for a long QQQ thesis
    stance: Stance
    reasons: tuple[str, ...]


class EvidenceGraph(StrictSchema):
    thesis: str = "long_qqq"
    nodes: tuple[EvidenceNode, ...]

    def node(self, family: str) -> EvidenceNode | None:
        return next((node for node in self.nodes if node.family == family), None)

    @property
    def contradictions(self) -> tuple[str, ...]:
        return tuple(
            f"{node.family}:{reason}"
            for node in self.nodes
            if node.stance == "contradicts"
            for reason in node.reasons
        )


class Confluence(StrictSchema):
    score: Decimal  # 0-100; 50 = no edge either way
    weights_version: str
    available_weight: Decimal  # share of weights with data (0-1)
    by_family: dict[str, Decimal]


class DataQuality(StrictSchema):
    score: Decimal  # 0-100
    blocking: tuple[str, ...]
    degraded: tuple[str, ...]


class ConsensusMatrix(StrictSchema):
    bull: tuple[str, ...]
    bear: tuple[str, ...]
    neutral: tuple[str, ...]
    conflict: Decimal  # 0 (agreement) to 1 (evenly split)


def _stance(score: Decimal) -> Stance:
    if score >= 15:
        return "supports"
    if score <= -15:
        return "contradicts"
    return "neutral"


def _clamp(value: Decimal) -> Decimal:
    return max(Decimal("-100"), min(Decimal("100"), value))


def _d(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def _node(family: str, score: Decimal | None, reasons: list[str]) -> EvidenceNode:
    if score is None:
        return EvidenceNode(family=family, score=Decimal("0"), stance="unavailable",
                            reasons=tuple(reasons) or ("no_data",))
    clamped = _clamp(score)
    return EvidenceNode(
        family=family, score=clamped, stance=_stance(clamped), reasons=tuple(reasons)
    )


def _price(features: FeatureSet, price: Decimal) -> EvidenceNode:
    reasons: list[str] = []
    score = Decimal("0")
    if features.session_vwap is not None:
        above = price >= features.session_vwap
        score += 35 if above else -35
        reasons.append("above_vwap" if above else "below_vwap")
    if features.ema_fast is not None and features.ema_slow is not None:
        up = features.ema_fast >= features.ema_slow
        score += 35 if up else -35
        reasons.append("ema_trend_up" if up else "ema_trend_down")
    distance = features.vwap_distance_atr
    if distance is not None and distance > Decimal("2.5"):
        score -= 30
        reasons.append("extended_above_vwap")
    score += (features.trend_score - 50) / Decimal("2.5")
    return _node("PRICE_STRUCTURE", score, reasons)


def _breadth(breadth: Mapping[str, Any] | None) -> tuple[EvidenceNode, EvidenceNode]:
    if not breadth:
        return _node("BREADTH", None, []), _node("COMPONENTS", None, [])
    weighted = _d(breadth.get("weighted_breadth")) or Decimal("0.5")
    green = _d(breadth.get("pct_green")) or Decimal("0.5")
    reasons = [f"weighted_breadth={weighted:.2f}", f"pct_green={green:.2f}"]
    divergences = list(breadth.get("divergences") or [])
    score = (weighted - Decimal("0.5")) * 160 + (green - Decimal("0.5")) * 80
    for divergence in divergences:
        if divergence in {"qqq_up_breadth_weak", "megacap_only_rally"}:
            score -= 25
            reasons.append(divergence)
    top10 = _d(breadth.get("top10_breadth")) or Decimal("0.5")
    concentration = _d(breadth.get("top10_contribution_share")) or Decimal("0")
    comp_reasons = [f"top10_breadth={top10:.2f}"]
    comp_score = (top10 - Decimal("0.5")) * 160
    if concentration > Decimal("0.75"):
        comp_score -= 15
        comp_reasons.append("concentrated_leadership")
    return _node("BREADTH", score, reasons), _node("COMPONENTS", comp_score, comp_reasons)


def _liquidity(features: FeatureSet) -> EvidenceNode:
    if features.relative_volume is None:
        return _node("LIQUIDITY_VOLUME", None, [])
    rv = features.relative_volume
    score = min(Decimal("40"), (rv - 1) * 40) if rv >= 1 else (rv - 1) * 60
    return _node("LIQUIDITY_VOLUME", score, [f"relative_volume={rv:.2f}"])


def _volatility(vol: Mapping[str, Any] | None) -> tuple[EvidenceNode, EvidenceNode]:
    if not vol or vol.get("status") == "unavailable":
        return _node("VOLATILITY", None, []), _node("OPTIONS", None, [])
    reasons: list[str] = []
    score = Decimal("0")
    vix = _d(vol.get("vix_close"))
    if vix is not None:
        score += 20 if vix < 18 else (-40 if vix > 28 else 0)
        reasons.append(f"vix={vix:.1f}")
    spread = _d(vol.get("iv_minus_realized"))
    if spread is not None and spread > Decimal("0.08"):
        score -= 20
        reasons.append("implied_well_above_realized")
    option_reasons: list[str] = []
    option_score: Decimal | None = None
    skew = _d(vol.get("skew_25d"))
    slope = _d(vol.get("term_slope"))
    if skew is not None or slope is not None:
        option_score = Decimal("0")
        if skew is not None and skew > Decimal("0.08"):
            option_score -= 40
            option_reasons.append("steep_put_skew")
        if slope is not None and slope < 0:
            option_score -= 40
            option_reasons.append("inverted_term_structure")
        if not option_reasons:
            option_score += 10
            option_reasons.append("options_calm")
    return _node("VOLATILITY", score if reasons else None, reasons), _node(
        "OPTIONS", option_score, option_reasons
    )


def _macro(macro: Mapping[str, Any] | None, now: datetime) -> EvidenceNode:
    if not macro:
        return _node("MACRO", None, [])
    if macro.get("gate"):
        return _node("MACRO", Decimal("-100"), [str(macro["gate"])])
    score = Decimal("10")
    reasons: list[str] = []
    for event in macro.get("upcoming") or []:
        minutes = event.get("minutes_to_event")
        if minutes is None or event.get("importance") == "LOW":
            continue
        if 0 <= int(minutes) <= 120:
            penalty = 50 if event.get("importance") == "HIGH" else 20
            score -= penalty
            reasons.append(f"{event.get('event_type')}_in_{int(minutes)}m")
    return _node("MACRO", score, reasons or ["no_event_within_2h"])


def _rates(rates: Mapping[str, Any] | None) -> EvidenceNode:
    if not rates or rates.get("status") != "ok":
        return _node("RATES", None, [])
    ten = next((s for s in rates.get("series") or [] if s.get("series_id") == "DGS10"), None)
    change = _d(ten.get("change_1d")) if ten else None
    if change is None:
        return _node("RATES", None, [])
    # Only a *shock* counts; the sign of the relationship is measured by regime
    # in research, never assumed here (§23).
    if abs(change) >= Decimal("0.10"):
        return _node("RATES", Decimal("-40"), [f"rate_shock_10y={change:+.2f}"])
    return _node("RATES", Decimal("0"), ["rates_calm"])


def _news(news: list[Mapping[str, Any]] | None) -> EvidenceNode:
    if news is None:
        return _node("NEWS", None, [])
    score = Decimal("0")
    reasons: list[str] = []
    for item in news[:15]:
        relevance = _d(item.get("qqq_relevance")) or Decimal("0")
        weight = relevance * (_d(item.get("decay")) or Decimal("0"))
        materiality = {"HIGH": 3, "MEDIUM": 2}.get(str(item.get("materiality")), 1)
        direction = int(item.get("sentiment") or 0)
        contribution = weight * materiality * direction * 400
        if contribution:
            score += contribution
            symbols = ",".join(item.get("symbols") or [])
            reasons.append(f"{symbols}:{direction:+d}")
    return _node("NEWS", score, reasons[:5] or ["no_directional_news"])


def build_evidence(
    features: FeatureSet,
    price: Decimal,
    intelligence: Mapping[str, Any] | None,
    *,
    now: datetime,
    analogs: Mapping[str, Any] | None = None,
) -> EvidenceGraph:
    intel = intelligence or {}
    breadth, components = _breadth(intel.get("breadth"))
    volatility, options = _volatility(intel.get("volatility"))
    analog_node = _node("HISTORICAL_ANALOGS", None, [])
    analogs = analogs if analogs is not None else intel.get("analogs")
    if analogs and analogs.get("quality") == "ok":
        # Share of similar past sessions that closed higher from this minute on.
        edge = (_d(analogs.get("up_rate")) or Decimal("0.5")) - Decimal("0.5")
        analog_node = _node(
            "HISTORICAL_ANALOGS", edge * 200,
            [f"n={analogs.get('sample')}", f"up_rate={analogs.get('up_rate')}"],
        )
    nodes = (
        _price(features, price),
        breadth,
        components,
        _liquidity(features),
        volatility,
        _macro(intel.get("macro"), now),
        _rates(intel.get("rates")),
        options,
        _news(intel.get("news") if "news" in intel else None),
        analog_node,
    )
    return EvidenceGraph(nodes=nodes)


def confluence(graph: EvidenceGraph, weights: Mapping[str, Decimal] | None = None) -> Confluence:
    table = dict(weights or CONFLUENCE_WEIGHTS)
    scores = {node.family: node.score for node in graph.nodes if node.stance != "unavailable"}
    for group in CORRELATED:
        present = [family for family in group if family in scores]
        if len(present) > 1:
            # One combined vote for correlated families (§37): weighted mean,
            # carrying only the largest single weight.
            total = sum((table[f] for f in present), Decimal("0"))
            merged = sum((scores[f] * table[f] for f in present), Decimal("0")) / total
            keep = max(present, key=lambda family: table[family])
            for family in present:
                scores.pop(family)
            scores[keep] = merged
            for family in present:
                if family != keep:
                    table[family] = Decimal("0")
    available = sum((table[family] for family in scores), Decimal("0"))
    if available <= 0:
        return Confluence(score=Decimal("50"), weights_version=CONFLUENCE_WEIGHTS_VERSION,
                          available_weight=Decimal("0"), by_family={})
    weighted = sum((scores[family] * table[family] for family in scores), Decimal("0")) / available
    return Confluence(
        score=(Decimal("50") + weighted / 2).quantize(Decimal("0.1")),
        weights_version=CONFLUENCE_WEIGHTS_VERSION,
        available_weight=available.quantize(Decimal("0.01")),
        by_family={family: score.quantize(Decimal("0.1")) for family, score in scores.items()},
    )


def data_quality(
    intelligence: Mapping[str, Any] | None,
    *,
    now: datetime,
    market_data_age: timedelta,
    session_open: bool,
    broker_equity_fresh: bool = True,
) -> DataQuality:
    """§39: freshness and coverage before any signal. Blocking items stop entries."""

    intel = intelligence or {}
    blocking: list[str] = []
    degraded: list[str] = []
    if market_data_age > timedelta(seconds=30):
        blocking.append("market_data_stale")
    if not broker_equity_fresh:
        blocking.append("broker_equity_unavailable")
    macro = intel.get("macro") or {}
    if macro.get("gate") == "macro_calendar_unavailable":
        blocking.append("macro_calendar_unavailable")
    breadth = intel.get("breadth")
    if session_open:
        if not breadth:
            degraded.append("breadth_unavailable")
        else:
            try:
                as_of = datetime.fromisoformat(str(breadth.get("as_of")))
                if now - as_of > timedelta(minutes=5):
                    degraded.append("breadth_stale")
            except ValueError:
                degraded.append("breadth_unreadable")
            coverage = _d(breadth.get("coverage_weight")) or Decimal("0")
            if coverage < Decimal("0.8"):
                degraded.append("breadth_low_coverage")
    for key in ("news", "rates", "volatility"):
        if not intel.get(key):
            degraded.append(f"{key}_unavailable")
    for source, error in (intel.get("errors") or {}).items():
        degraded.append(f"{source}:{error}")
    score = Decimal("100") - 40 * len(blocking) - 6 * len(degraded)
    return DataQuality(score=max(Decimal("0"), score), blocking=tuple(blocking),
                       degraded=tuple(dict.fromkeys(degraded)))


def consensus_matrix(graph: EvidenceGraph) -> ConsensusMatrix:
    bull = tuple(n.family for n in graph.nodes if n.stance == "supports")
    bear = tuple(n.family for n in graph.nodes if n.stance == "contradicts")
    neutral = tuple(n.family for n in graph.nodes if n.stance == "neutral")
    voting = len(bull) + len(bear)
    conflict = Decimal(min(len(bull), len(bear)) * 2) / Decimal(voting) if voting else Decimal("0")
    return ConsensusMatrix(bull=bull, bear=bear, neutral=neutral, conflict=conflict)
