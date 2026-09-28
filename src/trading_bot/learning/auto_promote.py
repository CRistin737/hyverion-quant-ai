"""Promote proven strategy changes in PAPER without waiting for the owner.

A proposal becomes eligible only when it is ``READY_FOR_REVIEW`` (it already
beat the active version out-of-sample) and is a ``strategy_params`` change.
Then it must also pass a *forward shadow* test: the candidate and the active
version are replayed on the sessions that happened **after** the proposal was
created (data that could not have influenced it). With at least
``shadow_sessions_before_promotion`` new sessions, enough trades and no worse
result or drawdown, it is deployed with the reason "autopromoción en paper",
recorded as ``AUTO_PROMOTED`` so the app can show it with a one-click undo.

Never in LIVE, never in ``supervised`` mode, never for agent instructions or
feature requests (those cannot be replayed and always wait for the owner).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from trading_bot.config.models import Settings
from trading_bot.core.clock import Clock
from trading_bot.db.database import Database
from trading_bot.db.repositories import AuditRepository
from trading_bot.learning.deployer import ChangeDeployer
from trading_bot.learning.optimizer_job import _metrics, _pool
from trading_bot.learning.proposals import proposal_timeline
from trading_bot.learning.versions import StrategyParams, VersionRepository
from trading_bot.market.clock import regular_session_only, sessions_in
from trading_bot.schemas.trading import Candle
from trading_bot.simulation.costs import SIMULATION_CAPITAL_USD
from trading_bot.simulation.strategy_replay import replay_plugin
from trading_bot.strategies.registry import exit_defaults, plugin_factory

MIN_FORWARD_TRADES = 3
AUTO_PROMOTED = "AUTO_PROMOTED"

CandleHistory = Callable[[str, int], Awaitable[tuple[Candle, ...]]]


def _aware(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def forward_verdict(
    before: Any, after: Any, *, sessions: int, required_sessions: int
) -> tuple[bool, str]:
    """Whether the forward (shadow) sessions confirm the candidate."""

    if sessions < required_sessions:
        return False, "waiting_for_sessions"
    champion, challenger = _metrics(before), _metrics(after)
    if challenger.sample_size < MIN_FORWARD_TRADES or champion.sample_size < MIN_FORWARD_TRADES:
        return False, "waiting_for_trades"
    # It must beat the active version, not tie it (AGENTS.md: beat the champion).
    if challenger.expectancy <= champion.expectancy:
        return False, "forward_expectancy_not_better"
    if challenger.max_drawdown > champion.max_drawdown:
        return False, "forward_drawdown_worse"
    return True, "forward_confirmed"


def _forward(
    settings: Settings,
    strategy_id: str,
    params: StrategyParams,
    candles: dict[str, list[Candle]],
) -> Any:
    build = plugin_factory(settings.public.strategies, strategy_id, params.plugin_overrides())
    stats = [
        replay_plugin(build, series, capital=SIMULATION_CAPITAL_USD)
        for series in candles.values()
        if series
    ]
    # The whole forward window is new data: pool both halves.
    return _pool([part for item in stats for part in (item.in_sample, item.out_of_sample)])


async def promote_proven_changes(
    settings: Settings,
    database: Database,
    clock: Clock,
    *,
    candle_history: CandleHistory,
) -> list[dict[str, Any]]:
    autonomy = settings.public.autonomy
    trading = settings.public.trading
    if (
        autonomy.mode != "paper_autonomous"
        or not autonomy.auto_promote_paper
        or trading.live_trading
        or trading.mode == "live"
    ):
        return []
    audit = AuditRepository(database)
    now = clock.now()
    ready = [
        item
        for item in proposal_timeline(await audit.recent("change_proposals", limit=500))
        if item.get("status") == "READY_FOR_REVIEW"
        and (item.get("candidate_spec") or {}).get("kind") == "strategy_params"
    ]
    if not ready:
        return []
    active = await VersionRepository(database).active("strategy")
    deployer = ChangeDeployer(
        database=database,
        clock=clock,
        strategies=settings.public.strategies.enabled,
        agents=(),
    )
    outcomes: list[dict[str, Any]] = []
    for item in ready:
        spec = item["candidate_spec"]
        strategy_id = str(spec.get("strategy_id"))
        created = _aware(item.get("created_at"))
        if strategy_id not in settings.public.strategies.enabled or created is None:
            continue
        row = active.get(strategy_id)
        current = (
            StrategyParams.model_validate(row["params"])
            if row
            else StrategyParams.from_exit(exit_defaults(strategy_id))
        )
        candidate = StrategyParams.model_validate(spec["params"])
        minutes = max(1, int((now - created).total_seconds() // 60))
        candles = {
            symbol: [
                candle
                for candle in regular_session_only(await candle_history(symbol, minutes))
                if candle.event_time > created
            ]
            for symbol in settings.public.trading.allowed_symbols
        }
        sessions = max((sessions_in(series) for series in candles.values()), default=0)

        if sessions >= autonomy.shadow_sessions_before_promotion:
            # CPU-heavy replays run off the event loop so trading ticks keep going.
            before = await asyncio.to_thread(_forward, settings, strategy_id, current, candles)
            after = await asyncio.to_thread(_forward, settings, strategy_id, candidate, candles)
            promote, reason = forward_verdict(
                before,
                after,
                sessions=sessions,
                required_sessions=autonomy.shadow_sessions_before_promotion,
            )
        else:
            promote, reason = False, "waiting_for_sessions"
        outcome: dict[str, Any] = {
            "proposal_id": item.get("id"),
            "strategy_id": strategy_id,
            "forward_sessions": sessions,
            "outcome": reason,
        }
        if promote:
            result = await deployer.apply(
                str(item["id"]),
                reason=f"Autopromoción en paper: confirmada en {sessions} sesiones nuevas",
            )
            await audit.append(
                "system_events",
                {
                    "status": AUTO_PROMOTED,
                    "proposal_id": item.get("id"),
                    "component_id": result["component_id"],
                    "version": result["version"],
                    "forward_sessions": sessions,
                },
                created_at=clock.now(),
            )
            outcome["version"] = result["version"]
        outcomes.append(outcome)
    return outcomes


__all__ = ["AUTO_PROMOTED", "forward_verdict", "promote_proven_changes"]

