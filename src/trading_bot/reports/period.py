"""Business-style report for any period (a day, a month, a year or a range).

Answers what an owner asks at the end of a month: how much the account was
worth at the start and at the end, what was earned, how many trades, how often
they won, profit factor, best/worst trade, the deepest drawdown and fees.
Everything comes from persisted rows (broker equity syncs, closed positions,
daily PnL); nothing is estimated. The CSV export lists every closed trade.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from trading_bot.db.database import Database
from trading_bot.db.repositories import AuditRepository
from trading_bot.db.state import position_recency
from trading_bot.market.clock import trading_day
from trading_bot.schemas.common import StrictSchema

ZERO = Decimal("0")
CSV_COLUMNS = (
    "position_id",
    "asset",
    "side",
    "opened_at",
    "closed_at",
    "quantity",
    "entry_price",
    "exit_price",
    "stop_price",
    "target_price",
    "exit_reason",
    "fees_usd",
    "net_pnl_usd",
)


def _dec(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _aware(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _payload(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    return dict(payload) if isinstance(payload, dict) else {}


class TradeRow(StrictSchema):
    position_id: str
    asset: str
    side: str | None
    opened_at: str | None
    closed_at: str
    quantity: str | None
    entry_price: str | None
    exit_price: str | None
    stop_price: str | None
    target_price: str | None
    exit_reason: str | None
    fees_usd: Decimal
    net_pnl_usd: Decimal


class DayPoint(StrictSchema):
    day: str
    net_pnl_usd: Decimal
    equity: Decimal | None = None


class PeriodReport(StrictSchema):
    start: str | None
    end: str
    equity_start: Decimal | None
    equity_end: Decimal | None
    equity_change_usd: Decimal | None
    return_percent: Decimal | None
    net_pnl_usd: Decimal
    fees_usd: Decimal
    trades: int
    wins: int
    losses: int
    win_rate_percent: Decimal | None
    profit_factor: Decimal | None
    average_win_usd: Decimal | None
    average_loss_usd: Decimal | None
    expectancy_usd: Decimal | None
    best_trade_usd: Decimal | None
    worst_trade_usd: Decimal | None
    max_drawdown_usd: Decimal
    max_drawdown_percent: Decimal | None
    days: tuple[DayPoint, ...]
    trade_rows: tuple[TradeRow, ...]

    def summary(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "net_pnl_usd": str(self.net_pnl_usd),
            "trades": self.trades,
            "win_rate_percent": None
            if self.win_rate_percent is None
            else str(self.win_rate_percent),
            "profit_factor": None if self.profit_factor is None else str(self.profit_factor),
            "max_drawdown_usd": str(self.max_drawdown_usd),
        }


def closed_trades(
    position_rows: Iterable[Mapping[str, Any]], *, start: datetime | None, end: datetime
) -> list[TradeRow]:
    latest: dict[str, dict[str, Any]] = {}
    # The entry fee shrinks on each partial exit; the largest value seen is the
    # fee actually paid to open the position.
    entry_fees: dict[str, Decimal] = {}
    for row in position_rows:
        record = _payload(row)
        position_id = str(record.get("position_id") or "")
        if not position_id:
            continue
        entry_fees[position_id] = max(
            entry_fees.get(position_id, ZERO), _dec(record.get("entry_fee_usd")) or ZERO
        )
        current = latest.get(position_id)
        if current is None or position_recency(record) > position_recency(current):
            latest[position_id] = record
    trades: list[TradeRow] = []
    for position_id, record in latest.items():
        if str(record.get("status") or "") != "CLOSED":
            continue
        closed_at = _aware(record.get("closed_at"))
        if closed_at is None or closed_at > end or (start is not None and closed_at < start):
            continue
        # Totals over every partial exit, not just the last one.
        exit_fees = _dec(record.get("cumulative_exit_fees_usd"))
        fees = entry_fees.get(position_id, ZERO) + (
            exit_fees if exit_fees is not None else _dec(record.get("exit_fee_usd")) or ZERO
        )
        cumulative = _dec(record.get("cumulative_realized_net_pnl"))
        net = cumulative if cumulative is not None else _dec(record.get("realized_net_pnl")) or ZERO
        trades.append(
            TradeRow(
                position_id=position_id,
                asset=str(record.get("asset") or ""),
                side=None if record.get("side") is None else str(record.get("side")),
                opened_at=None if record.get("opened_at") is None else str(record.get("opened_at")),
                closed_at=closed_at.isoformat(),
                quantity=None if record.get("quantity") is None else str(record.get("quantity")),
                entry_price=None
                if record.get("entry_price") is None
                else str(record.get("entry_price")),
                exit_price=None
                if record.get("exit_price") is None
                else str(record.get("exit_price")),
                stop_price=None
                if record.get("stop_price") is None
                else str(record.get("stop_price")),
                target_price=None
                if record.get("target_price") is None
                else str(record.get("target_price")),
                exit_reason=None
                if record.get("exit_reason") is None
                else str(record.get("exit_reason")),
                fees_usd=fees,
                net_pnl_usd=net,
            )
        )
    trades.sort(key=lambda trade: trade.closed_at)
    return trades


def _ratio(numerator: Decimal, denominator: Decimal, places: str = "0.01") -> Decimal | None:
    if denominator == 0:
        return None
    return (numerator / denominator).quantize(Decimal(places))


def build_period_report(
    *,
    trades: list[TradeRow],
    equity_rows: list[tuple[datetime, Decimal]],
    equity_before: Decimal | None,
    start: datetime | None,
    end: datetime,
) -> PeriodReport:
    pnls = [trade.net_pnl_usd for trade in trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_win = sum(wins, ZERO)
    gross_loss = -sum(losses, ZERO)
    net = sum(pnls, ZERO)

    # Per New York session: net PnL of trades closed that day and the last equity.
    by_day: dict[str, Decimal] = {}
    for trade in trades:
        closed = _aware(trade.closed_at)
        if closed is not None:
            day = trading_day(closed).isoformat()
            by_day[day] = by_day.get(day, ZERO) + trade.net_pnl_usd
    equity_by_day: dict[str, Decimal] = {}
    for at, equity in equity_rows:
        equity_by_day[trading_day(at).isoformat()] = equity
    days = tuple(
        DayPoint(day=day, net_pnl_usd=by_day.get(day, ZERO), equity=equity_by_day.get(day))
        for day in sorted(set(by_day) | set(equity_by_day))
    )

    equity_start = (
        equity_before if equity_before is not None else (equity_rows[0][1] if equity_rows else None)
    )
    equity_end = equity_rows[-1][1] if equity_rows else None

    # Drawdown on the equity path when there is one; otherwise on cumulative PnL.
    path = [equity for _, equity in equity_rows]
    if not path:
        # Start at zero so a losing first trade counts as drawdown.
        running = ZERO
        path = [ZERO]
        for pnl in pnls:
            running += pnl
            path.append(running)
    peak: Decimal | None = None
    max_dd = ZERO
    max_dd_pct: Decimal | None = None
    for value in path:
        peak = value if peak is None or value > peak else peak
        drawdown = peak - value
        if drawdown > max_dd:
            max_dd = drawdown
            if equity_rows and peak > 0:
                max_dd_pct = (drawdown / peak * 100).quantize(Decimal("0.01"))

    change = None if equity_start is None or equity_end is None else equity_end - equity_start
    return PeriodReport(
        start=None if start is None else start.isoformat(),
        end=end.isoformat(),
        equity_start=equity_start,
        equity_end=equity_end,
        equity_change_usd=change,
        return_percent=(
            None
            if change is None or not equity_start
            else (change / equity_start * 100).quantize(Decimal("0.01"))
        ),
        net_pnl_usd=net,
        fees_usd=sum((trade.fees_usd for trade in trades), ZERO),
        trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate_percent=_ratio(Decimal(len(wins)) * 100, Decimal(len(trades))),
        profit_factor=_ratio(gross_win, gross_loss),
        average_win_usd=_ratio(gross_win, Decimal(len(wins))),
        average_loss_usd=None if not losses else _ratio(-gross_loss, Decimal(len(losses))),
        expectancy_usd=_ratio(net, Decimal(len(trades))),
        best_trade_usd=max(pnls) if pnls else None,
        worst_trade_usd=min(pnls) if pnls else None,
        max_drawdown_usd=max_dd,
        max_drawdown_percent=max_dd_pct,
        days=days,
        trade_rows=tuple(trades),
    )


async def period_report(
    database: Database,
    *,
    start: datetime | None,
    end: datetime,
    days: int | None = None,
) -> PeriodReport:
    """Report for ``[start, end]``; ``days`` is a shortcut for "the last N days"."""

    if start is None and days is not None:
        start = end - timedelta(days=days)
    audit = AuditRepository(database)
    # Positions opened before the window can close inside it.
    lookback = None if start is None else start - timedelta(days=10)
    positions = await audit.between("positions", start=lookback, end=end)
    trades = closed_trades(positions, start=start, end=end)
    equity_rows: list[tuple[datetime, Decimal]] = []
    for row in await audit.between("equity_snapshots", start=start, end=end):
        at = _aware(row.get("created_at"))
        equity = _dec(_payload(row).get("equity"))
        if at is not None and equity is not None:
            equity_rows.append((at, equity))
    equity_before: Decimal | None = None
    if start is not None:
        earlier = await audit.between(
            "equity_snapshots", start=start - timedelta(days=45), end=start
        )
        if earlier:
            equity_before = _dec(_payload(earlier[-1]).get("equity"))
    return build_period_report(
        trades=trades,
        equity_rows=equity_rows,
        equity_before=equity_before,
        start=start,
        end=end,
    )


def _safe_cell(value: object) -> object:
    """Spreadsheet-safe text: a leading = + - @ would run as a formula in Excel.

    Numbers stay numbers; only text that starts with those characters is quoted.
    """

    if isinstance(value, str) and value[:1] in {"=", "+", "@", "\t", "\r"}:
        return "'" + value
    if isinstance(value, str) and value.startswith("-"):
        try:
            Decimal(value)
        except InvalidOperation:
            return "'" + value
    return value


def trades_csv(report: PeriodReport) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for trade in report.trade_rows:
        data = trade.model_dump(mode="json")
        writer.writerow([_safe_cell(data[column]) for column in CSV_COLUMNS])
    return buffer.getvalue()
