from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_bot.data.sources import ConnectorSchedule, ConnectorScheduler, ConnectorState

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def test_scheduler_starts_due_and_respects_minimum_interval() -> None:
    scheduler = ConnectorScheduler(
        (ConnectorSchedule("news", interval_seconds=60),), now=NOW
    )
    assert scheduler.due("news", now=NOW)
    state = scheduler.mark_success("news", now=NOW)
    assert state.next_run_at == NOW + timedelta(seconds=60)
    assert not scheduler.due("news", now=NOW + timedelta(seconds=59))
    assert scheduler.due("news", now=NOW + timedelta(seconds=60))


def test_failure_uses_exponential_backoff_and_never_polls_sooner() -> None:
    scheduler = ConnectorScheduler(
        (ConnectorSchedule("news", interval_seconds=30, max_backoff_seconds=120),), now=NOW
    )
    first = scheduler.mark_failure("news", now=NOW, error="429", retry_after_seconds=10)
    assert first.next_run_at == NOW + timedelta(seconds=30)
    second = scheduler.mark_failure("news", now=NOW, error="429", retry_after_seconds=90)
    assert second.next_run_at == NOW + timedelta(seconds=90)
    assert second.consecutive_failures == 2


def test_pause_and_naive_timestamps_fail_closed() -> None:
    scheduler = ConnectorScheduler((ConnectorSchedule("news", 60),), now=NOW)
    paused = scheduler.pause("news", until=NOW + timedelta(minutes=5), reason="robots")
    assert not scheduler.due("news", now=NOW + timedelta(minutes=4))
    assert paused.last_error == "robots"
    with pytest.raises(ValueError, match="timezone-aware"):
        scheduler.due("news", now=datetime(2026, 9, 14, 12, 0))


def test_restore_keeps_only_configured_source_state() -> None:
    scheduler = ConnectorScheduler((ConnectorSchedule("news", 60),), now=NOW)
    restored = scheduler.restore(
        ConnectorState(
            source_id="news",
            next_run_at=NOW + timedelta(minutes=10),
            consecutive_failures=2,
            last_error="429",
        )
    )
    assert restored.consecutive_failures == 2
    assert not scheduler.due("news", now=NOW + timedelta(minutes=5))
    with pytest.raises(KeyError, match="not scheduled"):
        scheduler.restore(ConnectorState("unconfigured", NOW))
