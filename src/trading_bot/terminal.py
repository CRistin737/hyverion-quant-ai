"""SSH/VPS text terminal. Renders the same snapshot contract as the desktop app.

It reads ``control_api.build_snapshot()`` so the terminal and the app can never
disagree about positions, risk or alerts. It is read-only: no action here
touches orders, configuration or the engine.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from rich import box
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from trading_bot.config.models import Settings
from trading_bot.control_api import build_snapshot
from trading_bot.db.database import Database
from trading_bot.engine_supervisor import engine_lock_held, flatten_pending


async def _state(database: Database, settings: Settings) -> dict[str, Any]:
    snapshot = await build_snapshot(database, settings)
    snapshot["engine_running"] = engine_lock_held(settings)
    snapshot["flatten_pending"] = flatten_pending(settings)
    return snapshot


def _spread_bps(market: dict[str, Any]) -> str:
    try:
        bid = Decimal(str(market["bid"]))
        ask = Decimal(str(market["ask"]))
        midpoint = (bid + ask) / Decimal("2")
        return str(((ask - bid) / midpoint * Decimal("10000")).quantize(Decimal("0.01")))
    except (KeyError, ArithmeticError, ValueError):
        return "—"


def _money(value: object) -> str:
    try:
        return f"${Decimal(str(value)).quantize(Decimal('0.01')):,}"
    except (ArithmeticError, ValueError):
        return "—"


def render_terminal(state: dict[str, Any]) -> Group:
    config: dict[str, Any] = state.get("config", {})
    risk: dict[str, Any] = state.get("risk", {})
    pnl: dict[str, Any] = state.get("pnl", {})
    live = bool(config.get("live_trading"))
    engine_running = bool(state.get("engine_running"))

    overview = Table.grid(padding=(0, 2))
    for style in ("dim", "bold", "dim", "bold"):
        overview.add_column(style=style)
    overview.add_row(
        "Modo",
        Text("REAL" if live else "SIMULACIÓN", style="bold red" if live else "bold green"),
        "Motor",
        Text(
            "activo" if engine_running else "detenido",
            style="bold green" if engine_running else "bold yellow",
        ),
    )
    overview.add_row(
        "Broker",
        str(config.get("broker", "—")),
        "Patrimonio marcado",
        _money(pnl.get("marked_equity") or pnl.get("equity")),
    )
    overview.add_row(
        "Símbolos",
        ", ".join(config.get("allowed_symbols", [])) or "—",
        "IA principal",
        str(config.get("ai_provider", "—")),
    )
    overview.add_row(
        "PnL realizado hoy",
        _money(pnl.get("realized_net_pnl")),
        "Operaciones sin resolver",
        str(state.get("unresolved_operations", 0)),
    )

    protection = Table.grid(padding=(0, 2))
    for style in ("dim", "bold", "dim", "bold"):
        protection.add_column(style=style)
    protection.add_row(
        "Nivel",
        str(risk.get("level", 0)),
        "Multiplicador",
        f"{risk.get('risk_multiplier', '1')}x",
    )
    protection.add_row(
        "Veredicto",
        str(risk.get("verdict", "ESPERANDO")),
        "Riesgo permitido",
        _money(risk.get("allowed_risk_usd")),
    )
    protection.add_row(
        "Ganancia protegida",
        _money(risk.get("protected_profit_floor_usd", "0")),
        "Cierre solicitado",
        "sí" if state.get("flatten_pending") else "no",
    )

    markets = Table(box=box.SIMPLE, expand=True)
    for heading in ("Activo", "Último", "Spread bps", "Procesado UTC"):
        markets.add_column(heading)
    for market in state.get("markets", [])[:6]:
        markets.add_row(
            str(market.get("symbol", "—")),
            str(market.get("last", "—")),
            _spread_bps(market),
            str(market.get("processed_time", "—")),
        )
    if not state.get("markets"):
        markets.add_row("—", "—", "—", "Sin datos de mercado todavía")

    positions = Table(box=box.SIMPLE, expand=True)
    for heading in ("Activo", "Lado", "Cantidad", "Entrada", "Stop", "Objetivo", "PnL"):
        positions.add_column(heading)
    for position in state.get("positions", [])[:8]:
        positions.add_row(
            str(position.get("asset", "—")),
            "compra" if str(position.get("side")) == "buy" else "venta",
            str(position.get("quantity", "—")),
            str(position.get("entry_price", "—")),
            str(position.get("stop_price", "—")),
            str(position.get("target_price", "—")),
            _money(position.get("unrealized_pnl")),
        )
    if not state.get("positions"):
        positions.add_row("—", "—", "—", "—", "—", "—", "Sin posiciones abiertas")

    orders = Table(box=box.SIMPLE, expand=True)
    for heading in ("Activo", "Modo", "Estado", "Cantidad"):
        orders.add_column(heading)
    for order in state.get("orders", [])[:5]:
        orders.add_row(
            str(order.get("asset", "—")),
            str(order.get("mode", "—")).upper(),
            str(order.get("status", "—")),
            str(order.get("filled_quantity", "—")),
        )
    if not state.get("orders"):
        orders.add_row("—", "—", "Sin órdenes todavía", "—")

    alerts = Text()
    open_alerts = [a for a in state.get("alerts", []) if not a.get("acknowledged")]
    if state.get("positions") and not engine_running:
        alerts.append(
            "• Hay posiciones abiertas y el motor está detenido: nadie vigila stops.\n",
            style="bold red",
        )
    for alert in open_alerts[:6]:
        style = "red" if alert.get("severity") == "CRITICAL" else "yellow"
        alerts.append(f"• {alert.get('message', '')}\n", style=style)
    if not open_alerts:
        alerts.append("• Sin alertas abiertas\n", style="dim")
    alerts.append("• El modo real está bloqueado en esta versión\n", style="dim")
    alerts.append(f"• Actualizado {datetime.now(UTC).isoformat(timespec='seconds')}\n", style="dim")

    return Group(
        Panel(overview, title="HYVERION QUANT AI · Sesión", border_style="green"),
        Panel(protection, title="Riesgo determinista", border_style="green"),
        Panel(positions, title="Posiciones abiertas", border_style="blue"),
        Panel(markets, title="Mercado", border_style="blue"),
        Panel(orders, title="Órdenes de simulación", border_style="blue"),
        Panel(alerts, title="Alertas", border_style="yellow"),
    )


async def run_terminal(
    settings: Settings, *, interval_seconds: int = 3, once: bool = False
) -> None:
    database = Database(settings.public.database.url)
    await database.initialize()
    try:
        with Live(render_terminal(await _state(database, settings)), refresh_per_second=4) as live:
            if once:
                return
            while True:
                await asyncio.sleep(interval_seconds)
                live.update(render_terminal(await _state(database, settings)))
    finally:
        await database.close()
