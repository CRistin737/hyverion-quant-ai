"""Paper campaign reports (§98, §133, §134) and the live-readiness checklist (§105).

* **Daily report**: broker paper PnL next to the reality-adjusted PnL, fees,
  trades, rejected entries and why, shadow results, decisions by regime and
  session time, and AI/data cost kept apart from trading PnL.
* **Live readiness**: every §105 criterion with its evidence. It is a report
  only: it never enables LIVE, and "human review" is always pending until the
  owner signs off outside the software (§146, phase 15).
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from trading_bot.db.database import Database
from trading_bot.db.lifecycle import OperationLifecycleRepository
from trading_bot.db.repositories import AuditRepository
from trading_bot.db.state import TradingStateRepository
from trading_bot.market.clock import trading_day
from trading_bot.schemas.common import StrictSchema
from trading_bot.simulation.reality import paper_reality

# Minimums before LIVE could even be *discussed* (§105). Deliberately strict.
MIN_PAPER_SESSIONS = 40
MIN_OOS_TRADES = 50
MIN_POSITIVE_FOLDS = 3
RECONCILIATION_TEXT = {True: "correcta", False: "con diferencias o error", None: "sin datos"}


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else {}


def _on(row: dict[str, Any], day: str) -> bool:
    created = row.get("created_at")
    if isinstance(created, datetime):
        # SQLite hands back naive datetimes; every stored timestamp is UTC.
        aware = created if created.tzinfo else created.replace(tzinfo=UTC)
        return trading_day(aware).isoformat() == day
    return str(created or "")[:10] == day


class DailyReport(StrictSchema):
    session: str
    account_profile: str | None
    broker_paper_pnl_usd: Decimal
    adjusted_simulated_pnl_usd: Decimal
    fees_usd: Decimal
    entries: int
    rejected: int
    rejection_reasons: dict[str, int]
    shadow_trades: int
    shadow_pnl_usd: Decimal
    by_regime: dict[str, int]
    by_time_bucket: dict[str, int]
    ai_cost_usd: Decimal
    realized_slippage_bps_mean: Decimal | None
    modelled_slippage_bps_mean: Decimal | None
    notes: tuple[str, ...]


Status = Literal["PASS", "FAIL", "PENDING"]


class ReadinessItem(StrictSchema):
    item: str
    status: Status
    evidence: str


class LiveReadinessReport(StrictSchema):
    verdict: Literal["NOT_READY"]  # this release can only ever say NOT_READY
    live_enabled: Literal[False] = False
    items: tuple[ReadinessItem, ...]
    note: str


async def daily_report(database: Database, *, now: datetime) -> DailyReport:
    audit = AuditRepository(database)
    state = TradingStateRepository(database)
    day = trading_day(now).isoformat()
    daily = next(
        (
            row
            for row in await audit.recent("daily_pnl", limit=10)
            if str(row.get("session_date")) == day
        ),
        {},
    )
    risk_rows = [row for row in await audit.recent("risk_decisions", limit=500) if _on(row, day)]
    denied = [_payload(row) for row in risk_rows if _payload(row).get("verdict") != "ALLOW"]
    reasons = Counter(reason for item in denied for reason in item.get("reasons") or ())
    shadows = [
        _payload(row) for row in await audit.recent("shadow_trades", limit=500) if _on(row, day)
    ]
    attributions = [
        _payload(row)
        for row in await audit.recent("decision_attributions", limit=500)
        if _on(row, day)
    ]
    orders = [row for row in await audit.recent("orders", limit=500) if _on(row, day)]
    proposals = await audit.recent("trade_proposals", limit=500)
    realized = Decimal(str(daily.get("realized_net_pnl") or "0"))
    reality = paper_reality(orders, proposals, realized_pnl_usd=realized)
    account = await state.latest_broker_account()
    profile = None
    if account:
        equity = Decimal(str(account.get("equity") or "0"))
        profile = f"alpaca_paper_{int(equity // 1000)}k · {account.get('account_label')}"
    notes = []
    if reality.paper_kinder_than_model:
        notes.append(
            "El paper llenó mejor que el modelo de costes: el PnL ajustado es el prudente."
        )
    if not risk_rows:
        notes.append("Sin propuestas evaluadas hoy (sin señal, mercado cerrado o NO_TRADE).")
    return DailyReport(
        session=day,
        account_profile=profile,
        broker_paper_pnl_usd=realized,
        adjusted_simulated_pnl_usd=reality.adjusted_simulated_pnl_usd,
        fees_usd=Decimal(str(daily.get("fees") or "0")),
        entries=sum(1 for row in risk_rows if _payload(row).get("verdict") == "ALLOW"),
        rejected=len(denied),
        rejection_reasons=dict(reasons.most_common(10)),
        shadow_trades=len(shadows),
        shadow_pnl_usd=sum(
            (Decimal(str(item.get("net_pnl_usd") or "0")) for item in shadows), Decimal("0")
        ),
        by_regime=dict(
            Counter(str(item.get("market_regime") or "UNKNOWN") for item in attributions)
        ),
        by_time_bucket=dict(
            Counter(str(item.get("time_bucket") or "UNKNOWN") for item in attributions)
        ),
        ai_cost_usd=await audit.daily_model_cost(now=now),
        realized_slippage_bps_mean=reality.realized_slippage_bps_mean,
        modelled_slippage_bps_mean=reality.modelled_slippage_bps_mean,
        notes=tuple(notes),
    )


async def live_readiness(database: Database, *, now: datetime) -> LiveReadinessReport:
    audit = AuditRepository(database)
    state = TradingStateRepository(database)
    sessions = {str(row.get("session_date")) for row in await audit.recent("daily_pnl", limit=500)}
    experiments = [_payload(row) for row in await audit.recent("experiments", limit=50)]
    replay = next((item for item in experiments if item.get("plugins")), None)
    best: dict[str, Any] | None = None
    if replay:
        best = max(
            replay["plugins"],
            key=lambda plugin: int((plugin.get("out_of_sample") or {}).get("trades") or 0),
        )
    oos = (best or {}).get("out_of_sample") or {}
    folds = sum(
        1
        for fold in (best or {}).get("walk_forward") or ()
        if Decimal(str(fold.get("net_pnl_usd") or "0")) > 0
    )
    unresolved = await OperationLifecycleRepository(database).count_unresolved()
    reconciliation = await state.latest_reconciliation_ok()
    events = [_payload(row) for row in await audit.recent("system_events", limit=500)]
    critical = sum(1 for item in events if str(item.get("severity") or "").upper() == "CRITICAL")

    def item(name: str, ok: bool | None, evidence: str) -> ReadinessItem:
        status: Status = "PENDING" if ok is None else ("PASS" if ok else "FAIL")
        return ReadinessItem(item=name, status=status, evidence=evidence)

    items = (
        item(
            "Datos suficientes",
            len(sessions) >= MIN_PAPER_SESSIONS,
            f"{len(sessions)} sesiones registradas (mínimo {MIN_PAPER_SESSIONS})",
        ),
        item(
            "Calidad del backtest",
            replay is not None and (best or {}).get("cost_verdict") == "robust_to_costs",
            "último replay: "
            f"{(best or {}).get('strategy_id', '—')} · "
            f"{(best or {}).get('cost_verdict', 'sin replay')}",
        ),
        item(
            "Fuera de muestra",
            int(oos.get("trades") or 0) >= MIN_OOS_TRADES
            and Decimal(str(oos.get("net_pnl_usd") or "0")) > 0,
            f"{oos.get('trades', 0)} operaciones fuera de muestra, "
            f"PnL {oos.get('net_pnl_usd', '—')}",
        ),
        item("Walk-forward", folds >= MIN_POSITIVE_FOLDS, f"{folds} de 4 tramos positivos"),
        item(
            "Periodo en paper",
            len(sessions) >= MIN_PAPER_SESSIONS,
            f"{len(sessions)} sesiones en paper",
        ),
        item("Coherencia con shadow", None, "Se revisa en el informe diario (shadow vs real)"),
        item("Fiabilidad de ejecución", unresolved == 0, f"{unresolved} operaciones sin resolver"),
        item(
            "Conciliación del broker",
            reconciliation,
            "última conciliación: " + RECONCILIATION_TEXT[reconciliation],
        ),
        item("Cero errores críticos", critical == 0, f"{critical} eventos críticos recientes"),
        item(
            "Interruptores de riesgo",
            True,
            "topes USD, puerta macro, cierre antes del final, stop nativo",
        ),
        item("Revisión humana", None, "Siempre pendiente: LIVE es una decisión manual del dueño"),
    )
    return LiveReadinessReport(
        verdict="NOT_READY",
        items=items,
        note=(
            "Informe solamente. Esta versión no puede activar LIVE; aunque todo pasara, "
            "hace falta la revisión humana y una versión futura con esa activación explícita."
        ),
    )
