from __future__ import annotations

from datetime import UTC, datetime

from trading_bot.monitoring.alerts import project_operational_alerts


def test_operational_alerts_project_critical_events_and_protection_state() -> None:
    now = datetime.now(UTC).isoformat()
    alerts = project_operational_alerts(
        [
            {
                "id": "event-1",
                "created_at": now,
                "payload": {
                    "status": "RECONCILIATION_MISMATCH",
                    "detail": "missing fill",
                },
            }
        ],
        [],
        {"safe_mode": True},
    )

    assert [item["severity"] for item in alerts] == ["CRITICAL", "CRITICAL"]
    assert any("missing fill" in item["message"] for item in alerts)
    assert any(item["source"] == "protection_recovery" for item in alerts)


def test_operational_alerts_ignore_unclassified_events_and_preserve_acknowledged() -> None:
    alerts = project_operational_alerts(
        [
            {
                "id": "event-ignored",
                "created_at": datetime.now(UTC).isoformat(),
                "payload": {"status": "RUNTIME_METRICS"},
            }
        ],
        [
            {
                "id": "alert-1",
                "created_at": datetime.now(UTC).isoformat(),
                "payload": {
                    "alert_id": "alert-1",
                    "severity": "WARNING",
                    "source": "test",
                    "message": "review source",
                    "acknowledged": True,
                },
            }
        ],
        {"safe_mode": False},
    )

    assert alerts == [
        {
            "alert_id": "alert-1",
            "severity": "WARNING",
            "source": "test",
            "message": "review source",
            "created_at": alerts[0]["created_at"],
            "acknowledged": True,
        }
    ]


def test_runtime_failure_events_project_into_alerts() -> None:
    rows = [
        {
            "id": "e1",
            "created_at": "2026-09-26T10:00:00+00:00",
            "payload": {"status": "POSITION_PROTECTION_FAILED", "error": "HTTPStatusError"},
        },
        {
            "id": "e2",
            "created_at": "2026-09-26T10:01:00+00:00",
            "payload": {"status": "PAPER_CYCLE_FAILED", "error": "ReadTimeout"},
        },
    ]
    alerts = project_operational_alerts(rows, [], {})
    by_id = {alert["alert_id"]: alert for alert in alerts}
    assert by_id["e1"]["severity"] == "CRITICAL"
    assert by_id["e1"]["message"].endswith("HTTPStatusError")
    assert by_id["e2"]["severity"] == "WARNING"
