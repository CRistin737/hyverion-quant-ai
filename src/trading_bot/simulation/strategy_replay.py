"""Replay each deterministic strategy plugin over regular-session QQQ bars.

This is research evidence, never a trading path: it does not create execution
intents, touch RiskEngine limits or change configuration. Each plugin sees only
the data a live cycle would have seen at that bar (no lookahead), and exits are
simulated with the same ShadowSimulator and cost model as shadow trading.

Assumptions (reported with every result):
- regular-session 1-minute bars only (09:30-16:00 ET), audited before use;
- the live cycle's 60-close window plus the session's bars for VWAP/ATR/RSI;
- the base risk budget of the live loop (min(0.25% of capital, $10));
- one position at a time per plugin; exit at stop, target, the proposal's
  horizon or the session close (never overnight), whichever comes first;
- costs in three scenarios (optimistic, base, stress); verdicts use base and
  a strategy that is only profitable in the optimistic one is rejected (§101);
- the first 70% of bars are in-sample, the last 30% out-of-sample (OOS), and
  the whole period is also cut into walk-forward folds by session.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import pairwise

from pydantic import Field

from trading_bot.core.clock import Clock, FixedClock
from trading_bot.data.features import FeatureEngine
from trading_bot.learning.optimizer import ChampionChallenger, ExperimentMetrics
from trading_bot.macro.calendar import MacroEvent
from trading_bot.market.clock import trading_day
from trading_bot.schemas.common import Side, StrictSchema
from trading_bot.schemas.trading import Candle, MarketSnapshot
from trading_bot.simulation.costs import SCENARIOS, CostScenario
from trading_bot.simulation.data_audit import DATA_AUDIT_FAILED, audit_minute_bars
from trading_bot.simulation.monte_carlo import MonteCarloReport, monte_carlo
from trading_bot.simulation.shadow import ShadowSimulator
from trading_bot.strategies.base import StrategyPlugin
from trading_bot.strategies.regime import REGIME_WINDOW, classify_regime

FEATURE_WINDOW = 60
# Earlier bars kept with the session for ATR/EMA warm-up.
WARMUP_BARS = 30
SPREAD_BPS = Decimal("1")
IN_SAMPLE_FRACTION = Decimal("0.7")
# Regular US equity session: 09:30-16:00 ET.
SESSION_MINUTES = 390
MIN_CANDLES = FEATURE_WINDOW + 30
WALK_FORWARD_FOLDS = 4

PluginFactory = Callable[[Clock], StrategyPlugin]
# (net pnl, fees, exit reason, initial risk in USD)
Trade = tuple[Decimal, Decimal, str, Decimal]


class ReplayStats(StrictSchema):
    trades: int = Field(ge=0)
    wins: int = Field(ge=0)
    net_pnl_usd: Decimal
    fees_usd: Decimal = Field(ge=0)
    max_drawdown_usd: Decimal = Field(ge=0)
    exits: dict[str, int]
    # §104: never judge by win rate alone.
    expectancy_usd: Decimal | None = None
    profit_factor: Decimal | None = None
    average_r: Decimal | None = None

    @property
    def win_rate(self) -> Decimal | None:
        return Decimal(self.wins) / Decimal(self.trades) if self.trades else None


class PluginReplay(StrictSchema):
    strategy_id: str
    version: str
    in_sample: ReplayStats
    out_of_sample: ReplayStats
    # Out-of-sample results split by the market regime at entry.
    oos_by_regime: dict[str, ReplayStats] = Field(default_factory=dict)
    # Out-of-sample under each cost scenario (base is ``out_of_sample``).
    oos_by_scenario: dict[str, ReplayStats] = Field(default_factory=dict)
    # Whole period cut into consecutive folds by session (stability, not fitting).
    walk_forward: tuple[ReplayStats, ...] = ()
    cost_verdict: str = "not_evaluated"
    # Trade-sequence stress of the out-of-sample BASE trades (§102).
    monte_carlo: MonteCarloReport | None = None
    # Entries skipped because the MacroRiskGate was closed at that bar.
    macro_blocked: int = 0


class StrategyReplayReport(StrictSchema):
    symbol: str
    interval: str
    candles: int
    sessions: int = 0
    first_bar: str
    last_bar: str
    split_bar: str
    capital_usd: Decimal
    plugins: tuple[PluginReplay, ...]
    assumptions: tuple[str, ...]
    data_audit: dict[str, object] = Field(default_factory=dict)


def _snapshot(symbol: str, candles: Sequence[Candle], index: int) -> MarketSnapshot:
    bar = candles[index]
    half_spread = bar.close * SPREAD_BPS / Decimal("20000")
    # Volume of the trailing session window, known at this bar (no lookahead).
    seen = candles[max(0, index - SESSION_MINUTES + 1) : index + 1]
    volume = sum((c.volume for c in seen), Decimal("0"))
    dollar_volume = sum((c.volume * (c.vwap or c.close) for c in seen), Decimal("0"))
    return MarketSnapshot(
        symbol=symbol,
        bid=bar.close - half_spread,
        ask=bar.close + half_spread,
        last=bar.close,
        session_volume=volume,
        session_dollar_volume=dollar_volume,
        provider="replay",
        event_time=bar.event_time,
        received_time=bar.event_time,
        processed_time=bar.event_time,
    )


POINTS_PER_BAR = 3


def _price_path(bars: Sequence[Candle], side: Side) -> tuple[Decimal, ...]:
    """Intrabar path, pessimistic: the adverse extreme is visited before the favorable one.

    The live engine re-checks positions every few seconds, so a stop or target
    touched inside a minute would be seen; closes alone would miss it. When a bar
    touches both, the stop wins (never an optimistic fill).
    """

    path: list[Decimal] = []
    for bar in bars:
        adverse, favorable = (bar.low, bar.high) if side == Side.BUY else (bar.high, bar.low)
        path.extend((adverse, favorable, bar.close))
    return tuple(path)


def _stats(results: Sequence[Trade]) -> ReplayStats:
    equity = peak = drawdown = Decimal("0")
    exits: dict[str, int] = {}
    for pnl, _fees, reason, _risk in results:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        exits[reason] = exits.get(reason, 0) + 1
    trades = len(results)
    gains = sum((pnl for pnl, *_ in results if pnl > 0), Decimal("0"))
    losses = -sum((pnl for pnl, *_ in results if pnl < 0), Decimal("0"))
    net = sum((pnl for pnl, *_ in results), Decimal("0"))
    r_multiples = [pnl / risk for pnl, _, _, risk in results if risk > 0]
    return ReplayStats(
        trades=trades,
        wins=sum(1 for pnl, *_ in results if pnl > 0),
        net_pnl_usd=net,
        fees_usd=sum((fees for _, fees, _, _ in results), Decimal("0")),
        max_drawdown_usd=drawdown,
        exits=exits,
        expectancy_usd=net / trades if trades else None,
        profit_factor=gains / losses if losses > 0 else None,
        average_r=sum(r_multiples, Decimal("0")) / len(r_multiples) if r_multiples else None,
    )


def _cost_verdict(by_scenario: Mapping[str, ReplayStats]) -> str:
    def positive(name: str) -> bool:
        stats = by_scenario.get(name)
        return bool(stats and stats.trades and stats.net_pnl_usd > 0)

    if positive(CostScenario.STRESS) and positive(CostScenario.BASE):
        return "robust_to_costs"
    if positive(CostScenario.BASE):
        return "fails_stress_costs"
    if positive(CostScenario.OPTIMISTIC):
        # Only works with optimistic costs: rejected (§101).
        return "rejected_only_optimistic"
    return "not_profitable"


def replay_plugin(
    factory: PluginFactory,
    candles: Sequence[Candle],
    *,
    capital: Decimal,
    bar_seconds: int = 60,
    macro_events: Sequence[MacroEvent] = (),
    macro_pre_minutes: int = 15,
    macro_post_minutes: int = 15,
) -> PluginReplay:
    """Replay one plugin; the proposal at bar i only uses bars <= i.

    With ``macro_events`` the MacroRiskGate is applied as in live trading: no
    entry from ``macro_pre_minutes`` before to ``macro_post_minutes`` after a
    HIGH event. Official release schedules are published well in advance, so
    using them in a replay is not look-ahead.
    """

    if len(candles) < MIN_CANDLES:
        raise ValueError(f"strategy replay needs at least {MIN_CANDLES} candles")
    symbol = candles[0].symbol
    risk_budget = min(capital * Decimal("0.0025"), Decimal("10"))
    split = int(Decimal(len(candles)) * IN_SAMPLE_FRACTION)
    days = [trading_day(candle.event_time) for candle in candles]
    session_start: list[int] = []
    for position, day in enumerate(days):
        session_start.append(
            position if position == 0 or days[position - 1] != day else session_start[-1]
        )
    features = FeatureEngine()
    simulator = ShadowSimulator()
    closes = [candle.close for candle in candles]
    scenario_trades: dict[CostScenario, list[Trade]] = {name: [] for name in SCENARIOS}
    in_sample: list[Trade] = []
    oos_regimes: dict[str, list[Trade]] = {}
    fold_of_day = _folds(days)
    folds: dict[int, list[Trade]] = {}
    plugin = factory(FixedClock(candles[0].event_time))
    blocked_count = 0
    high_events = sorted(
        event.scheduled_at for event in macro_events
        if event.importance == "HIGH" and event.time_known
    )
    pre = timedelta(minutes=macro_pre_minutes)
    post = timedelta(minutes=macro_post_minutes)

    def gated(moment: datetime) -> bool:
        return any(when - pre <= moment <= when + post for when in high_events)

    index = FEATURE_WINDOW - 1
    while index < len(candles) - 1:
        plugin = factory(FixedClock(candles[index].event_time))
        window = tuple(closes[index - FEATURE_WINDOW + 1 : index + 1])
        context = candles[max(0, session_start[index] - WARMUP_BARS) : index + 1]
        proposal = plugin.propose(
            _snapshot(symbol, candles, index),
            features.compute(symbol, window, context),
            risk_budget_usd=risk_budget,
        )
        if proposal is None:
            index += 1
            continue
        if high_events and gated(candles[index].event_time):
            blocked_count += 1
            index += 1
            continue
        horizon = max(1, proposal.time_horizon_seconds // bar_seconds)
        # Never hold past the session close: the path ends with the day.
        ahead = [
            bar
            for bar in candles[index + 1 : index + 1 + horizon]
            if trading_day(bar.event_time) == days[index]
        ]
        if not ahead:
            index += 1
            continue
        future = _price_path(ahead, proposal.side)
        risk = abs(proposal.entry_price - proposal.stop_price) * proposal.quantity
        results = {
            name: simulator.simulate(proposal, future, costs=model)
            for name, model in SCENARIOS.items()
        }
        for name, result in results.items():
            reason = result.exit_reason
            if reason == "horizon_expired" and len(ahead) < horizon:
                reason = "session_close"
            trade = (result.net_pnl_usd, result.fees_usd, reason, risk)
            if index >= split:
                scenario_trades[name].append(trade)
            if name == CostScenario.BASE:
                if index < split:
                    in_sample.append(trade)
                else:
                    regime = classify_regime(closes[max(0, index - REGIME_WINDOW + 1) : index + 1])
                    oos_regimes.setdefault(regime, []).append(trade)
                folds.setdefault(fold_of_day[days[index]], []).append(trade)
        # One position at a time: three path points per bar; resume after the exit bar.
        base = results[CostScenario.BASE]
        index += max(1, -(-base.observations // POINTS_PER_BAR))
    by_scenario = {name.value: _stats(trades) for name, trades in scenario_trades.items()}
    return PluginReplay(
        strategy_id=plugin.strategy_id,
        version=plugin.version,
        in_sample=_stats(in_sample),
        out_of_sample=by_scenario[CostScenario.BASE.value],
        oos_by_regime={name: _stats(trades) for name, trades in sorted(oos_regimes.items())},
        oos_by_scenario=by_scenario,
        walk_forward=tuple(_stats(folds.get(fold, [])) for fold in range(WALK_FORWARD_FOLDS)),
        cost_verdict=_cost_verdict(by_scenario),
        monte_carlo=monte_carlo([pnl for pnl, *_ in scenario_trades[CostScenario.BASE]]),
        macro_blocked=blocked_count,
    )


def _folds(days: Sequence[object]) -> dict[object, int]:
    """Assign each trading day to one of WALK_FORWARD_FOLDS consecutive folds."""

    ordered = list(dict.fromkeys(days))
    size = max(1, -(-len(ordered) // WALK_FORWARD_FOLDS))
    return {
        day: min(WALK_FORWARD_FOLDS - 1, position // size)
        for position, day in enumerate(ordered)
    }


def replay_strategies(
    factories: Mapping[str, PluginFactory],
    candles: Sequence[Candle],
    *,
    capital: Decimal,
    audit: bool = True,
    macro_events: Sequence[MacroEvent] = (),
) -> StrategyReplayReport:
    ordered = sorted(candles, key=lambda candle: candle.event_time)
    if len(ordered) < MIN_CANDLES:
        raise ValueError(f"strategy replay needs at least {MIN_CANDLES} candles")
    if len({candle.symbol for candle in ordered}) != 1:
        raise ValueError("strategy replay takes candles of one symbol")
    report = audit_minute_bars(ordered) if audit else None
    if report is not None and not report.ok:
        raise ValueError(f"{DATA_AUDIT_FAILED}: {', '.join(report.issues)}")
    gaps = {b.event_time - a.event_time for a, b in pairwise(ordered)}
    bar = min(gaps) if gaps else timedelta(minutes=1)
    split = int(Decimal(len(ordered)) * IN_SAMPLE_FRACTION)
    plugins = tuple(
        replay_plugin(
            factory, ordered, capital=capital, bar_seconds=int(bar.total_seconds()),
            macro_events=macro_events,
        )
        for _, factory in sorted(factories.items())
    )
    return StrategyReplayReport(
        symbol=ordered[0].symbol,
        interval=ordered[0].interval,
        candles=len(ordered),
        sessions=len({trading_day(candle.event_time) for candle in ordered}),
        first_bar=ordered[0].event_time.isoformat(),
        last_bar=ordered[-1].event_time.isoformat(),
        split_bar=ordered[split].event_time.isoformat(),
        capital_usd=capital,
        plugins=plugins,
        assumptions=(
            "Cada propuesta usa solo las velas hasta su barra (sin mirar el futuro).",
            "Solo barras del horario regular (09:30-16:00 ET), auditadas antes de usarse.",
            "Ventana de 60 cierres más las barras de la sesión para VWAP, ATR y RSI.",
            "Presupuesto de riesgo min(0,25 % del capital, $10); una posición a la vez.",
            "Salida en stop, objetivo, horizonte o cierre de la sesión; nunca de un día a otro.",
            "Costes en tres escenarios; el veredicto usa el base y rechaza lo que solo gana "
            "en el optimista.",
            "Se mira primero el extremo adverso de cada barra: si toca stop y objetivo, "
            "cuenta el stop.",
            "70 % inicial dentro de muestra, 30 % final fuera de muestra; además, "
            f"{WALK_FORWARD_FOLDS} tramos consecutivos por sesión.",
            "Solo evidencia de investigación: sin RiskEngine, sin órdenes, sin cambios de "
            "configuración.",
            (
                f"Puerta macro aplicada con {len(macro_events)} eventos del calendario oficial "
                "(publicado de antemano)."
                if macro_events
                else "Sin calendario macro: la puerta macro no se aplicó en este replay."
            ),
            "Sin inteligencia guardada de días pasados (amplitud, noticias): la confluencia "
            "solo usa precio y volumen (modo cuantitativo, §130).",
            "Monte Carlo: 2000 reordenaciones con reemplazo de las operaciones fuera de "
            "muestra; es una prueba de estrés, no una promesa.",
        ),
        data_audit=report.model_dump(mode="json") if report is not None else {},
    )


class ChallengerVerdict(StrictSchema):
    ready_for_review: bool
    reasons: tuple[str, ...]


class ChampionChallengerReport(StrictSchema):
    strategy_id: str
    symbol: str
    candles: int
    first_bar: str
    last_bar: str
    split_bar: str
    champion_params: dict[str, str]
    challenger_params: dict[str, str]
    champion: PluginReplay
    challenger: PluginReplay
    verdict: ChallengerVerdict
    minimum_samples: int


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


def compare_champion_challenger(
    champion: PluginFactory,
    challenger: PluginFactory,
    candles: Sequence[Candle],
    *,
    capital: Decimal,
    champion_params: dict[str, str],
    challenger_params: dict[str, str],
    minimum_samples: int = 100,
) -> ChampionChallengerReport:
    """Replay both on the same candles; judge only the out-of-sample segment.

    The verdict uses the project's ChampionChallenger rule (more OOS samples than
    the minimum, higher expectancy, no worse drawdown or false-positive rate).
    A positive verdict only makes the challenger eligible for human review.
    """

    ordered = sorted(candles, key=lambda candle: candle.event_time)
    if len(ordered) < MIN_CANDLES:
        raise ValueError(f"strategy replay needs at least {MIN_CANDLES} candles")
    gaps = {b.event_time - a.event_time for a, b in pairwise(ordered)}
    bar_seconds = int((min(gaps) if gaps else timedelta(minutes=1)).total_seconds())
    champ = replay_plugin(champion, ordered, capital=capital, bar_seconds=bar_seconds)
    chall = replay_plugin(challenger, ordered, capital=capital, bar_seconds=bar_seconds)
    a, b = _metrics(champ.out_of_sample), _metrics(chall.out_of_sample)
    reasons: list[str] = []
    if b.sample_size < minimum_samples:
        reasons.append("insufficient_oos_samples")
    if b.expectancy <= a.expectancy:
        reasons.append("expectancy_not_better")
    if b.max_drawdown > a.max_drawdown:
        reasons.append("drawdown_worse")
    if b.false_positive_rate > a.false_positive_rate:
        reasons.append("false_positive_rate_worse")
    ready = ChampionChallenger().ready_for_review(a, b, minimum_samples=minimum_samples)
    split = int(Decimal(len(ordered)) * IN_SAMPLE_FRACTION)
    return ChampionChallengerReport(
        strategy_id=champ.strategy_id,
        symbol=ordered[0].symbol,
        candles=len(ordered),
        first_bar=ordered[0].event_time.isoformat(),
        last_bar=ordered[-1].event_time.isoformat(),
        split_bar=ordered[split].event_time.isoformat(),
        champion_params=champion_params,
        challenger_params=challenger_params,
        champion=champ,
        challenger=chall,
        verdict=ChallengerVerdict(ready_for_review=ready, reasons=tuple(reasons)),
        minimum_samples=minimum_samples,
    )
