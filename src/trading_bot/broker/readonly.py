"""Read-only broker view for the control API, CLI and doctor.

It exposes account, positions, open orders and the reconciliation snapshot, and
has no path to submit, cancel or replace. Only ExecutionEngine holds a mutable
adapter.
"""

from __future__ import annotations

from typing import Any

from trading_bot.broker.base import (
    BrokerAccount,
    BrokerCapabilities,
    BrokerPosition,
    BrokerUnavailable,
)
from trading_bot.broker.factory import build_broker
from trading_bot.broker.reconciliation import ReconciliationSnapshot
from trading_bot.config.models import Settings
from trading_bot.core.clock import Clock
from trading_bot.db.state import TradingStateRepository


class BrokerReader:
    def __init__(self, settings: Settings, clock: Clock) -> None:
        self._settings = settings
        self._broker = build_broker(settings, clock)

    @property
    def provider(self) -> str:
        return self._broker.provider

    @property
    def external(self) -> bool:
        return not self._broker.simulated

    @property
    def capabilities(self) -> BrokerCapabilities:
        return self._broker.capabilities

    async def status(self) -> dict[str, Any]:
        health = await self._broker.health()
        payload: dict[str, Any] = {
            "provider": self.provider,
            "environment": "paper",
            "connected": health.connected,
            "detail": health.detail,
            "capabilities": self.capabilities.model_dump(mode="json"),
            "account": None,
            "positions": [],
        }
        if self.external and health.connected:
            account = await self._broker.get_account()  # type: ignore[union-attr]
            positions = await self._broker.get_positions()  # type: ignore[union-attr]
            payload["account"] = account.model_dump(mode="json")
            payload["positions"] = [item.model_dump(mode="json") for item in positions]
        return payload

    async def account_state(self) -> tuple[BrokerAccount, list[BrokerPosition]]:
        if not self.external:
            raise BrokerUnavailable("broker_is_simulator")
        account = await self._broker.get_account()  # type: ignore[union-attr]
        positions = await self._broker.get_positions()  # type: ignore[union-attr]
        return account, list(positions)

    async def snapshot(self) -> ReconciliationSnapshot:
        if not self.external:
            raise BrokerUnavailable("broker_is_simulator")
        return await self._broker.reconciliation_snapshot()  # type: ignore[union-attr]


async def reconcile_broker(
    settings: Settings, clock: Clock, state: TradingStateRepository
) -> dict[str, Any]:
    """Compare the external paper account with local state and audit the result.

    The same pass syncs the broker's equity: it is the account's only capital.
    """

    try:
        reader = BrokerReader(settings, clock)
    except BrokerUnavailable as exc:
        status = "AUTH_REQUIRED" if "credentials" in exc.code else "ERROR"
        return {"status": status, "safe_mode": True, "detail": exc.code}
    if not reader.external:
        return {"status": "SIMULATOR", "safe_mode": False, "detail": "in-process simulator"}
    try:
        broker_snapshot = await reader.snapshot()
        account, positions = await reader.account_state()
    except BrokerUnavailable as exc:
        await state.record_reconciliation_error(exc.code, now=clock.now())
        return {"status": "ERROR", "safe_mode": True, "detail": exc.code}
    try:
        synced = await state.sync_broker_account(account, positions, now=clock.now())
    except RuntimeError as exc:
        await state.record_reconciliation_error(str(exc), now=clock.now())
        return {"status": "ERROR", "safe_mode": True, "detail": str(exc)}
    result = await state.reconcile(
        broker_snapshot, now=clock.now(), compare_balances=False, source=reader.provider
    )
    return {
        "status": "OK" if result.ok else "MISMATCH",
        "safe_mode": result.safe_mode,
        "mismatches": list(result.mismatches),
        "open_order_ids": sorted(broker_snapshot.open_order_ids),
        "position_ids": sorted(broker_snapshot.position_ids),
        "checked_at": clock.now().isoformat(),
        "account": synced,
    }
