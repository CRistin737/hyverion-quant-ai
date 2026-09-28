"""Deterministic optimizer: look for a better strategy setting and propose it.

For each enabled strategy it replays a small, bounded set of candidates over
real public candles (``simulation.strategy_replay``, no lookahead):

1. candidates = blocked regimes x exit variants, all inside ``StrategyParams``;
2. the best candidate is chosen on the IN-SAMPLE part only;
3. it is judged on the OUT-OF-SAMPLE part against the active version with the
   project's champion/challenger rule, pooled over the approved symbols;
4. only a candidate that passes becomes a ``ChangeProposal`` ready for review.

It never applies anything, never touches risk limits and never reaches an
exchange client: the owner decides in the app (see ``learning.deployer``).
"""

from __future__ import annotations

import fcntl
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4

from trading_bot.config.models import StrategiesConfig
from trading_bot.core.clock import Clock
from trading_bot.db.database import Database
from trading_bot.db.repositories import AuditRepository
from trading_bot.learning.optimizer import ChampionChallenger, ExperimentMetrics
from trading_bot.learning.proposals import proposal_timeline
from trading_bot.learning.supervisor import LearningSupervisor
from trading_bot.learning.versions import StrategyParams, VersionRepository
from trading_bot.market.clock import regular_session_only, sessions_in
from trading_bot.schemas.trading import Candle
from trading_bot.simulation.strategy_replay import ReplayStats, replay_plugin
from trading_bot.strategies.registry import exit_defaults, plugin_factory, strategy_version

# 20 regular sessions fit in ~30 calendar days (weekends and holidays included).
HISTORY_SESSIONS = 20
HISTORY_MINUTES = 30 * 24 * 60
MIN_OOS_TRADES = 50
RUN_EVERY = timedelta(hours=24)
_OPEN_STATES = {"PROPOSED", "TESTING", "READY_FOR_REVIEW", "APPROVED"}
_BLOCK_OPTIONS: tuple[tuple[str, ...], ...] = (
    (),
    ("ranging",),
    ("trending_down",),
    ("ranging", "trending_down"),
)

CandleHistory = Callable[[str, int], Awaitable[tuple[Candle, ...]]]
Runner = Callable[[Callable[[], "Finding | None"]], Awaitable["Finding | None"]]


async def _inline(task: Callable[[], Finding | None]) -> Finding | None:
    return task()


@dataclass(frozen=True, slots=True)
class Pooled:
    in_sample: ReplayStats
    out_of_sample: ReplayStats


@dataclass(frozen=True, slots=True)
class Finding:
    strategy_id: str
    current: StrategyParams
    candidate: StrategyParams
    before: ReplayStats
    after: ReplayStats
    ready: bool
    reasons: tuple[str, ...]


def _exit_variants(current: StrategyParams) -> list[StrategyParams]:
    def bounded(**changes: Any) -> StrategyParams | None:
        try:
            return StrategyParams.model_validate({**current.model_dump(), **changes})
        except ValueError:
            return None  # outside the allowed bounds: not a candidate

    variants = [
        current,
        bounded(reward_multiple=current.reward_multiple + Decimal("1")),
        bounded(horizon_minutes=current.horizon_minutes * 2),
        bounded(horizon_minutes=max(15, current.horizon_minutes // 2)),
        bounded(stop_percent=current.stop_percent * Decimal("1.5")),
    ]
    return [variant for variant in variants if variant is not None]


def candidates(current: StrategyParams) -> list[StrategyParams]:
    """Bounded search space around the active version (current first)."""

    # The active version itself comes first, exactly as it is (including the
    # regimes it already blocks): it is the champion every candidate must beat.
    seen: dict[str, StrategyParams] = {current.model_dump_json(): current}
    for exit_variant in _exit_variants(current):
        for blocked in _BLOCK_OPTIONS:
            candidate = exit_variant.model_copy(update={"blocked_regimes": blocked})
            seen.setdefault(candidate.model_dump_json(), candidate)
    return list(seen.values())


def _pool(stats: Sequence[ReplayStats]) -> ReplayStats:
    exits: dict[str, int] = {}
    for item in stats:
        for reason, count in item.exits.items():
            exits[reason] = exits.get(reason, 0) + count
    return ReplayStats(
        trades=sum(item.trades for item in stats),
        wins=sum(item.wins for item in stats),
        net_pnl_usd=sum((item.net_pnl_usd for item in stats), Decimal("0")),
        fees_usd=sum((item.fees_usd for item in stats), Decimal("0")),
        # Conservative: drawdowns of separate symbols simply add up.
        max_drawdown_usd=sum((item.max_drawdown_usd for item in stats), Decimal("0")),
        exits=exits,
    )


def _metrics(stats: ReplayStats) -> ExperimentMetrics:
    trades = stats.trades
    return ExperimentMetrics(
        expectancy=stats.net_pnl_usd / Decimal(trades) if trades else Decimal("0"),
        max_drawdown=stats.max_drawdown_usd,
        false_positive_rate=(
            Decimal(trades - stats.wins) / Decimal(trades) if trades else Decimal("1")
        ),
        sample_size=trades,
    )


def evaluate(
    strategies: StrategiesConfig,
    strategy_id: str,
    params: StrategyParams,
    candles_by_symbol: Mapping[str, Sequence[Candle]],
    *,
    capital: Decimal,
) -> Pooled:
    build = plugin_factory(strategies, strategy_id, params.plugin_overrides())
    replays = [
        replay_plugin(build, candles, capital=capital)
        for candles in candles_by_symbol.values()
    ]
    return Pooled(
        in_sample=_pool([replay.in_sample for replay in replays]),
        out_of_sample=_pool([replay.out_of_sample for replay in replays]),
    )


def search(
    strategies: StrategiesConfig,
    strategy_id: str,
    current: StrategyParams,
    candles_by_symbol: Mapping[str, Sequence[Candle]],
    *,
    capital: Decimal,
    minimum_samples: int = MIN_OOS_TRADES,
) -> Finding | None:
    """Pick the best candidate in-sample; judge it out-of-sample. CPU only."""

    champion = evaluate(strategies, strategy_id, current, candles_by_symbol, capital=capital)
    challengers = [
        (candidate, evaluate(strategies, strategy_id, candidate, candles_by_symbol,
                             capital=capital))
        for candidate in candidates(current)
        if candidate != current
    ]
    if not challengers:
        return None
    best_candidate, best = max(
        challengers,
        key=lambda item: (item[1].in_sample.net_pnl_usd, -item[1].in_sample.trades),
    )
    # A challenger that is not better in-sample never earns an out-of-sample test.
    if best.in_sample.net_pnl_usd <= champion.in_sample.net_pnl_usd:
        return None
    before, after = _metrics(champion.out_of_sample), _metrics(best.out_of_sample)
    reasons: list[str] = []
    if after.sample_size < minimum_samples:
        reasons.append("insufficient_oos_samples")
    if after.expectancy <= before.expectancy:
        reasons.append("expectancy_not_better")
    if after.max_drawdown > before.max_drawdown:
        reasons.append("drawdown_worse")
    if after.false_positive_rate > before.false_positive_rate:
        reasons.append("false_positive_rate_worse")
    ready = ChampionChallenger().ready_for_review(before, after, minimum_samples=minimum_samples)
    return Finding(
        strategy_id=strategy_id,
        current=current,
        candidate=best_candidate,
        before=champion.out_of_sample,
        after=best.out_of_sample,
        ready=ready,
        reasons=tuple(reasons),
    )


def bump_minor(version: str) -> str:
    parts = version.split(".")
    try:
        major, minor = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return f"{version}.1"
    return f"{major}.{minor + 1}.0"


_REGIME_ES = {
    "ranging": "mercado lateral",
    "trending_up": "tendencia alcista",
    "trending_down": "tendencia bajista",
}


def describe_change(current: StrategyParams, candidate: StrategyParams) -> list[str]:
    """Plain-Spanish list of what the candidate changes."""

    lines: list[str] = []
    added = [r for r in candidate.blocked_regimes if r not in current.blocked_regimes]
    removed = [r for r in current.blocked_regimes if r not in candidate.blocked_regimes]
    if added:
        lines.append("No operar en " + " ni en ".join(_REGIME_ES[r] for r in added))
    if removed:
        lines.append("Volver a operar en " + " y en ".join(_REGIME_ES[r] for r in removed))
    if candidate.stop_percent != current.stop_percent:
        lines.append(f"Stop a {candidate.stop_percent} % (antes {current.stop_percent} %)")
    if candidate.reward_multiple != current.reward_multiple:
        lines.append(
            f"Objetivo a {candidate.reward_multiple} veces el stop "
            f"(antes {current.reward_multiple})"
        )
    if candidate.horizon_minutes != current.horizon_minutes:
        lines.append(
            f"Cierre por tiempo a {candidate.horizon_minutes} min "
            f"(antes {current.horizon_minutes} min)"
        )
    return lines


def _summary(stats: ReplayStats) -> str:
    rate = (
        (Decimal(stats.wins) * 100 / Decimal(stats.trades)).quantize(Decimal("1"))
        if stats.trades
        else Decimal("0")
    )
    net = stats.net_pnl_usd.quantize(Decimal("0.01"))
    money = f"-${abs(net)}" if net < 0 else f"${net}"
    return f"{money} en {stats.trades} operaciones (acierto {rate} %)"


def _stats_json(stats: ReplayStats) -> dict[str, Any]:
    return stats.model_dump(mode="json")


@contextmanager
def optimizer_lock(directory: Path) -> Iterator[bool]:
    """Non-blocking process lock: True when this process may run the optimizer."""

    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / "optimizer.lock").open("a+")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


async def last_run(database: Database) -> dict[str, Any] | None:
    for row in await AuditRepository(database).with_status("system_events", ["OPTIMIZER_RUN"]):
        payload = row.get("payload")
        if isinstance(payload, dict):
            return {**payload, "created_at": row.get("created_at")}
    return None


async def due(database: Database, now: datetime) -> bool:
    previous = await last_run(database)
    created = previous.get("created_at") if previous else None
    if not isinstance(created, datetime):
        return True
    created = created.replace(tzinfo=UTC) if created.tzinfo is None else created
    return now - created >= RUN_EVERY


async def run_optimizer(
    *,
    database: Database,
    strategies: StrategiesConfig,
    symbols: Sequence[str],
    capital: Decimal,
    candle_history: CandleHistory,
    clock: Clock,
    runner: Runner | None = None,
    minutes: int = HISTORY_MINUTES,
) -> dict[str, Any]:
    """Run one optimizer pass and persist its outcome; returns the run summary.

    ``runner`` lets the caller move the CPU-heavy search off the event loop
    (``asyncio.to_thread``); by default it runs inline.
    """

    audit = AuditRepository(database)
    versions = VersionRepository(database)
    supervisor = LearningSupervisor(repository=audit, clock=clock)
    active = await versions.active("strategy")
    open_proposals = {
        str(item.get("candidate_spec", {}).get("strategy_id") or item.get("agent"))
        for item in proposal_timeline(await audit.recent("change_proposals", limit=500))
        if item.get("status") in _OPEN_STATES
    }
    # Strategies are validated only on the session they may trade (09:30-16:00 ET).
    candles = {
        symbol: regular_session_only(await candle_history(symbol, minutes))
        for symbol in symbols
    }
    sessions = max((sessions_in(series) for series in candles.values()), default=0)
    results: list[dict[str, Any]] = []
    for strategy_id in strategies.enabled:
        if strategy_id in open_proposals:
            results.append({"strategy_id": strategy_id, "outcome": "pending_review"})
            continue
        row = active.get(strategy_id)
        current = (
            StrategyParams.model_validate(row["params"])
            if row
            else StrategyParams.from_exit(exit_defaults(strategy_id))
        )
        current_version = str(row["version"]) if row else strategy_version(strategy_id)

        finding = await (runner or _inline)(
            partial(search, strategies, strategy_id, current, candles, capital=capital)
        )
        if finding is None:
            results.append({"strategy_id": strategy_id, "outcome": "no_better_candidate"})
            continue
        experiment_id = str(uuid4())
        now = clock.now()
        await audit.append(
            "experiments",
            {
                "experiment_id": experiment_id,
                "status": "COMPLETED",
                "period": "optimizer",
                "strategy_id": strategy_id,
                "symbols": list(symbols),
                "minutes": minutes,
                "sessions": sessions,
                "champion_params": current.model_dump(mode="json"),
                "challenger_params": finding.candidate.model_dump(mode="json"),
                "before": _stats_json(finding.before),
                "after": _stats_json(finding.after),
                "ready_for_review": finding.ready,
                "reasons": list(finding.reasons),
                "net_pnl": str(finding.after.net_pnl_usd),
                "max_drawdown": str(finding.after.max_drawdown_usd),
            },
            created_at=now,
            asset=strategy_id,
            event_time=now,
            received_time=now,
            processed_time=now,
            record_id=experiment_id,
        )
        changes = describe_change(current, finding.candidate)
        if not finding.ready:
            results.append(
                {
                    "strategy_id": strategy_id,
                    "outcome": "not_proven",
                    "reasons": list(finding.reasons),
                    "experiment_id": experiment_id,
                    "changes": changes,
                    "before": _stats_json(finding.before),
                    "after": _stats_json(finding.after),
                }
            )
            continue
        proposal = await supervisor.propose(
            agent=strategy_id,
            current_version=current_version,
            candidate_version=bump_minor(current_version),
            reason=(
                f"Con precios reales de las últimas {sessions} sesiones, este ajuste gana más "
                "por operación que la versión actual, sin una caída peor, y lo confirma con "
                "datos que no se usaron para elegirlo."
            ),
            evidence=[
                f"experiment:{experiment_id}",
                f"{sessions} sesiones de velas de 1 minuto de {', '.join(symbols)} "
                "(solo horario regular).",
                "El mejor ajuste se eligió con el 70 % inicial y se validó con el 30 % final.",
            ],
            affected_rules=changes or ["strategy_params"],
            expected_improvement=(
                f"Pasar de {_summary(finding.before)} a {_summary(finding.after)}."
            ),
            risk=(
                "Opera menos veces; en otro periodo el resultado puede ser distinto."
                if finding.candidate.blocked_regimes
                else "Cambia cuándo cierra las operaciones; en otro periodo puede variar."
            ),
            candidate_spec={
                "kind": "strategy_params",
                "strategy_id": strategy_id,
                "params": finding.candidate.model_dump(mode="json"),
            },
        )
        proposal = await supervisor.transition(
            proposal, "TESTING", reason="Prueba automática con precios reales"
        )
        await supervisor.transition(
            proposal, "READY_FOR_REVIEW", reason="Mejora demostrada fuera de muestra"
        )
        results.append(
            {
                "strategy_id": strategy_id,
                "outcome": "proposed",
                "proposal_id": proposal.id,
                "experiment_id": experiment_id,
            }
        )
    summary = {
        "status": "OPTIMIZER_RUN",
        "symbols": list(symbols),
        "results": results,
        "proposed": sum(1 for item in results if item["outcome"] == "proposed"),
    }
    await audit.append("system_events", summary, created_at=clock.now())
    return summary
