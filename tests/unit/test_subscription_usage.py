"""Subscription limits (5 h / weekly) and model roles per agent."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from trading_bot.core.agent_pipeline import SENSOR_AGENTS, role_for
from trading_bot.providers.usage import SubscriptionUsage, codex_usage


def test_codex_limits_come_from_its_own_session_log(tmp_path) -> None:
    day = tmp_path / "sessions" / "2026" / "09" / "28"
    day.mkdir(parents=True)
    log = day / "rollout.jsonl"
    rate = {
        "primary": {"used_percent": 3.0, "window_minutes": 300, "resets_at": 1790599937},
        "secondary": {"used_percent": 47.0, "window_minutes": 10080, "resets_at": 1791047422},
    }
    log.write_text(
        "\n".join(
            [
                json.dumps({"type": "message", "payload": {"text": "secret prompt"}}),
                json.dumps(
                    {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": rate}}
                ),
            ]
        ),
        encoding="utf-8",
    )
    usage = codex_usage(tmp_path, now=datetime.now(UTC))
    assert usage.available is True
    assert usage.session_used_percent == Decimal("3.0")
    assert usage.weekly_used_percent == Decimal("47.0")
    assert usage.session_resets_at is not None and usage.session_resets_at.startswith("2026-09")
    assert "secret prompt" not in json.dumps(usage.as_dict())


def test_missing_codex_data_is_unavailable_not_invented(tmp_path) -> None:
    usage = codex_usage(tmp_path)
    assert usage.available is False
    assert usage.session_used_percent is None


def test_a_full_window_is_exhausted() -> None:
    usage = SubscriptionUsage("anthropic", True, session_used_percent=Decimal("100"))
    assert usage.exhausted is True
    assert usage.as_dict()["session_used_percent"] == "100"


def test_decision_agents_run_on_the_decision_model() -> None:
    assert role_for("strategy") == "decision"
    assert role_for("critic") == "decision"
    assert role_for("optimizer") == "improvement"
    assert all(role_for(agent) == "analysis" for agent in ("market", "news", *SENSOR_AGENTS))
