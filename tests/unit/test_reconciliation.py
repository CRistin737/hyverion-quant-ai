from decimal import Decimal

from trading_bot.broker.reconciliation import ReconciliationService, ReconciliationSnapshot


def snapshot(**updates) -> ReconciliationSnapshot:
    values = {
        "balances": {"USDT": Decimal("1000")},
        "open_order_ids": frozenset({"order-1"}),
        "position_ids": frozenset({"position-1"}),
        "recent_fill_ids": frozenset({"fill-1"}),
    }
    values.update(updates)
    return ReconciliationSnapshot.model_validate(values)


def test_restart_with_matching_open_position_is_reconciled() -> None:
    result = ReconciliationService().compare(snapshot(), snapshot())

    assert result.ok is True
    assert result.safe_mode is False


def test_restart_position_mismatch_enters_safe_mode() -> None:
    result = ReconciliationService().compare(
        snapshot(), snapshot(position_ids=frozenset({"position-exchange"}))
    )

    assert result.ok is False
    assert result.safe_mode is True
    assert "positions_mismatch" in result.mismatches
