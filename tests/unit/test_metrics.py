from __future__ import annotations

from decimal import Decimal

import pytest

from trading_bot.monitoring.metrics import MetricsRegistry


def test_metrics_registry_supports_bounded_labeled_counters() -> None:
    metrics = MetricsRegistry()
    assert metrics.increment("provider_calls", labels={"provider": "openai"}) == 1
    assert metrics.increment("provider_calls", labels={"provider": "openai"}) == 2
    snapshot = metrics.snapshot()
    assert snapshot[0].name == "provider_calls"
    assert snapshot[0].value == Decimal("2")


def test_metrics_registry_rejects_unbounded_label_sets() -> None:
    metrics = MetricsRegistry()
    with pytest.raises(ValueError, match="eight"):
        metrics.set("bad", Decimal("1"), labels={str(index): "x" for index in range(9)})
