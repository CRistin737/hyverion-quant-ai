from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from conftest import control_client
from fastapi.testclient import TestClient
from pydantic import SecretStr

from trading_bot.config import load_settings
from trading_bot.control_api import create_control_api
from trading_bot.db import AuditRepository, Database
from trading_bot.providers.access import SubscriptionHealthResult
from trading_bot.providers.base import ProviderError, ProviderResult
from trading_bot.schemas.observability import ProviderProbeOutput


def test_control_api_is_json_only(tmp_path) -> None:
    settings = load_settings()
    settings = settings.model_copy(
        update={
            "public": settings.public.model_copy(
                update={
                    "database": settings.public.database.model_copy(
                        update={"url": f"sqlite+aiosqlite:///{tmp_path / 'control.db'}"}
                    ),
                }
            )
        }
    )
    with control_client(create_control_api(settings)) as client:
        root = client.get("/")
        snapshot = client.get("/api/v1/snapshot")
        assert root.headers["content-type"].startswith("application/json")
        assert root.json() == {
            "service": "hyverion-quant-control-api",
            "ui": "native",
            "mode": "paper",
            "live_trading": False,
        }
        assert snapshot.status_code == 200
        body = snapshot.json()
        assert "capital_usd" not in body["config"]
        assert body["config"]["allowed_symbols"] == ["QQQ"]
        assert body["config"]["primary_instrument"] == "QQQ"
        assert len(body["agents"]) == 17
        assert {row["provider_id"] for row in body["providers"]} == {
            "anthropic",
            "openai",
            "xai",
            "gemini",
        }
        assert all(row["auth_state"] != "CONNECTED" for row in body["providers"])
        assert any(row["source_id"] == "sec-press-releases" for row in body["sources"])
        assert body["config"]["mode"] == "paper"
        assert body["config"]["ai_provider_policy"] == {
            "single_primary": True,
            "primary": None,
            "ordered_fallbacks": [],
            "authentication": "subscription",
            "gateway": "ModelRouter",
            "fail_closed_when_exhausted": True,
        }
        # No broker sync yet: equity is unknown, never a configured number.
        assert body["pnl"]["equity"] is None
        assert body["pnl"]["broker_account"] is None
        assert body["session"]["action"] == "CONTINUE"
        assert body["reconciliation"]["status"] == "NOT_RUN"
        assert body["protection_recovery"] == {
            "ok": True,
            "safe_mode": False,
            "open_positions": 0,
            "protected_positions": 0,
            "unprotected_position_ids": [],
            "reason": None,
        }
        assert body["pnl_history"] == []
        assert body["orders"] == []
        assert body["fills"] == []
        assert {"shadow_trades", "experiments", "change_proposals", "audit"} <= body.keys()
        assert body["learning_metrics"]["sample_size"] == 0
        assert "decision_trace" in body
        backup = client.post("/api/v1/system/backup")
        assert backup.status_code == 200
        assert backup.json()["status"] == "BACKUP_VERIFIED"
        assert backup.json()["verified"] is True
        retention = client.get("/api/v1/observability/retention")
        assert retention.status_code == 200
        assert retention.json()["status"] == "RETENTION_DRY_RUN"
        assert retention.json()["deleted_rows"] == 0
        readiness = client.get("/api/v1/readiness")
        assert readiness.status_code == 200
        assert readiness.json()["overall"] == "PAPER_READY"
        assert readiness.json()["live_authorized"] is False
        assert {row["status"] for row in readiness.json()["checks"]} >= {"PASS", "GATED"}
        plan_audit = client.get("/api/v1/plan-audit")
        assert plan_audit.status_code == 200
        # The plan matrix is internal (private/); the public export reports INCOMPLETE.
        if (Path(__file__).resolve().parents[2] / "private" / "spec").is_dir():
            assert plan_audit.json()["overall"] == "PASS"
            assert plan_audit.json()["requirements_total"] == 43
            assert plan_audit.json()["evidence_missing_count"] == 0
        assert client.get("/api/v1/agents").status_code == 200
        login = client.post("/api/v1/providers/gemini/login")
        assert login.status_code == 200
        # Gemini's CLI login is an interactive TUI: the app explains the manual step.
        assert login.json()["state"] in {"manual", "failed"}
        assert login.json()["url"] is None
        assert client.get("/api/v1/providers/gemini/login").status_code == 200
        assert client.post("/api/v1/providers/unknown/login").status_code == 404
        test = client.post("/api/v1/providers/gemini/test")
        assert test.status_code == 200
        assert test.json()["supported"] is False
        chain_probe = client.post("/api/v1/providers/chain/probe")
        assert chain_probe.status_code == 200
        assert chain_probe.json()["status"] == "BLOCKED"
        assert chain_probe.json()["code"] == "provider_chain_disabled"
        # The crypto testnet check is gone with the QQQ migration.
        assert client.post("/api/v1/exchange/sandbox/reconcile").status_code == 404
        assert client.post("/api/v1/backtest/run", json={}).json()["detail"] == {
            "code": "prices_required"
        }
        prices = ["100", "101", "99", "103", "105", "104", "106", "107", "105", "108", "110"]
        baseline = client.post(
            "/api/v1/backtest/run", json={"walk_forward": False, "prices": prices}
        )
        assert baseline.status_code == 200
        assert baseline.json()["period"] == "deterministic baseline"
        walk_forward = client.post(
            "/api/v1/backtest/run", json={"walk_forward": True, "prices": prices}
        )
        assert walk_forward.status_code == 200
        assert walk_forward.json()["period"] == "walk-forward OOS"

        with client.websocket_connect("/api/v1/stream") as websocket:
            streamed = websocket.receive_json()
            assert streamed["config"]["mode"] == "paper"


def test_snapshot_keeps_candle_history_bound_to_each_market_symbol(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'market-history.db'}"
    now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

    async def seed() -> None:
        database = Database(database_url)
        await database.initialize()
        repository = AuditRepository(database)
        for symbol, price in (("QQQ", "100"), ("SPY", "200")):
            await repository.append(
                "market_snapshots",
                {"symbol": symbol, "last": price},
                created_at=now,
                asset=symbol,
                event_time=now,
                received_time=now,
                processed_time=now,
            )
            await repository.append(
                "candles",
                {"symbol": symbol, "close": price},
                created_at=now,
                asset=symbol,
                event_time=now,
                received_time=now,
                processed_time=now,
            )
        await database.close()

    import asyncio

    asyncio.run(seed())
    settings = load_settings().model_copy(
        update={
            "public": load_settings().public.model_copy(
                update={
                    "database": load_settings().public.database.model_copy(
                        update={"url": database_url}
                    )
                }
            )
        }
    )
    with control_client(create_control_api(settings)) as client:
        response = client.get("/api/v1/snapshot")

    assert response.status_code == 200
    markets = {row["symbol"]: row for row in response.json()["markets"]}
    assert markets["QQQ"]["candles"][0]["symbol"] == "QQQ"
    assert markets["SPY"]["candles"][0]["symbol"] == "SPY"


def test_control_api_token_protects_state_routes(tmp_path) -> None:
    settings = load_settings()
    settings = settings.model_copy(
        update={
            "secrets": settings.secrets.model_copy(
                update={"control_api_token": SecretStr("local-token")}
            ),
            "public": settings.public.model_copy(
                update={
                    "database": settings.public.database.model_copy(
                        update={"url": f"sqlite+aiosqlite:///{tmp_path / 'protected.db'}"}
                    )
                }
            ),
        }
    )
    with TestClient(create_control_api(settings)) as client:
        assert client.get("/api/v1/snapshot").status_code == 401
        assert client.get("/api/v1/readiness").status_code == 401
        assert client.get("/api/v1/plan-audit").status_code == 401
        authorized = client.get(
            "/api/v1/snapshot", headers={"Authorization": "Bearer local-token"}
        )
        assert authorized.status_code == 200


def test_observability_metrics_exports_json_and_prometheus_without_secrets(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'metrics.db'}"
    settings = load_settings().model_copy(
        update={
            "public": load_settings().public.model_copy(
                update={
                    "database": load_settings().public.database.model_copy(
                        update={"url": database_url}
                    )
                }
            )
        }
    )

    async def seed() -> None:
        database = Database(database_url)
        await database.initialize()
        now = datetime.now(UTC)
        await AuditRepository(database).append(
            "system_events",
            {
                "status": "RUNTIME_METRICS",
                "samples": [
                    {
                        "name": "provider_calls_total",
                        "value": "2",
                        "labels": {"provider": "anthropic"},
                    },
                    {
                        "name": "invalid value",
                        "value": "not-a-number",
                        "labels": {"secret": "never-export"},
                    },
                ],
            },
            created_at=now,
            asset="runtime",
        )
        await database.close()

    import asyncio

    asyncio.run(seed())
    with control_client(create_control_api(settings)) as client:
        exported = client.get("/api/v1/observability/metrics")
        assert exported.status_code == 200
        body = exported.json()
        assert body["source"] == "persisted_runtime_metrics"
        assert body["samples"][0]["name"] == "provider_calls_total"
        assert "never-export" not in exported.text

        prometheus = client.get(
            "/api/v1/observability/metrics", params={"format": "prometheus"}
        )
        assert prometheus.status_code == 200
        assert "provider_calls_total" in prometheus.text
        assert "not-a-number" not in prometheus.text
        assert 'mode="paper"' in prometheus.text

        invalid = client.get(
            "/api/v1/observability/metrics", params={"format": "xml"}
        )
        assert invalid.status_code == 422


def test_control_api_exposes_subscription_status_without_secrets(tmp_path, monkeypatch) -> None:
    settings = load_settings()
    settings = settings.model_copy(
        update={
            "public": settings.public.model_copy(
                update={
                    "database": settings.public.database.model_copy(
                        update={"url": f"sqlite+aiosqlite:///{tmp_path / 'subscription.db'}"}
                    )
                }
            )
        }
    )

    async def fake_check(provider_id: str) -> SubscriptionHealthResult:
        return SubscriptionHealthResult(
            provider_id=provider_id,
            supported=True,
            status="CONNECTED",
            code="subscription_authenticated",
            detail="official status",
            plan="pro",
        )

    monkeypatch.setattr("trading_bot.control_api.check_subscription", fake_check)
    with control_client(create_control_api(settings)) as client:
        result = client.post("/api/v1/providers/anthropic/subscription")
        assert result.status_code == 200
        assert result.json()["status"] == "CONNECTED"
        assert result.json()["plan"] == "pro"
        rows = client.get("/api/v1/providers").json()
        anthropic = next(row for row in rows if row["provider_id"] == "anthropic")
        assert anthropic["subscription"]["auth_state"] == "CONNECTED"
        assert "secret" not in result.text.lower()


def test_provider_chain_probe_uses_one_router_and_persists_safe_metadata(
    tmp_path, monkeypatch
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'probe.db'}"
    settings = load_settings().model_copy(
        update={
            "public": load_settings().public.model_copy(
                update={
                    "database": load_settings().public.database.model_copy(
                        update={"url": database_url}
                    ),
                    "ai": load_settings().public.ai.model_copy(
                        update={
                            "primary_provider": "anthropic",
                            "fallback_providers": ("openai",),
                            "primary_auth_mode": "subscription",
                        }
                    ),
                }
            )
        }
    )

    class FakeRouter:
        async def invoke(self, **kwargs):
            assert kwargs["agent_id"] == "provider_probe"
            assert kwargs["context"] == {
                "purpose": "subscription_connectivity_probe",
                "mode": "paper",
            }
            return ProviderResult(
                output=ProviderProbeOutput(summary="structured response validated"),
                provider="openai_subscription",
                model="cli-default-profile",
                latency_ms=12,
                input_tokens=3,
                output_tokens=4,
                cost_usd=None,
                billing_mode="subscription",
                attempted_providers=("anthropic_subscription", "openai_subscription"),
                fallback_reason="anthropic_subscription:auth_required",
            )

    monkeypatch.setattr(
        "trading_bot.control_api.build_model_router",
        lambda *args, **kwargs: FakeRouter(),
    )
    with control_client(create_control_api(settings)) as client:
        result = client.post("/api/v1/providers/chain/probe")
        assert result.status_code == 200
        body = result.json()
        assert body["status"] == "SUCCEEDED"
        assert body["provider"] == "openai_subscription"
        assert body["attempted_providers"] == [
            "anthropic_subscription",
            "openai_subscription",
        ]
        assert body["fallback_reason"] == "anthropic_subscription:auth_required"
        assert "structured response validated" not in result.text
        audit = client.get("/api/v1/snapshot").json()["audit"]
        assert any(
            row["type"] == "system_events"
            and row["asset"] == "anthropic"
            and row["status"] == "PROVIDER_CHAIN_PROBE"
            and row["detail"] == "PROVIDER_CHAIN_PROBE"
            for row in audit
        )


def test_provider_chain_probe_exhaustion_is_no_trade(tmp_path, monkeypatch) -> None:
    settings = load_settings().model_copy(
        update={
            "public": load_settings().public.model_copy(
                update={
                    "database": load_settings().public.database.model_copy(
                        update={"url": f"sqlite+aiosqlite:///{tmp_path / 'probe-fail.db'}"}
                    ),
                    "ai": load_settings().public.ai.model_copy(
                        update={
                            "primary_provider": "anthropic",
                            "fallback_providers": ("openai",),
                            "primary_auth_mode": "subscription",
                        }
                    ),
                }
            )
        }
    )

    class FailingRouter:
        async def invoke(self, **kwargs):
            del kwargs
            raise ProviderError(
                "provider_chain_exhausted",
                retryable=False,
                attempted_providers=("anthropic_subscription", "openai_subscription"),
                fallback_reason=(
                    "anthropic_subscription:auth_required; "
                    "openai_subscription:auth_required"
                ),
            )

    monkeypatch.setattr(
        "trading_bot.control_api.build_model_router",
        lambda *args, **kwargs: FailingRouter(),
    )
    with control_client(create_control_api(settings)) as client:
        result = client.post("/api/v1/providers/chain/probe")
        assert result.status_code == 200
        body = result.json()
        assert body["status"] == "NO_TRADE"
        assert body["attempted_providers"] == [
            "anthropic_subscription",
            "openai_subscription",
        ]
        assert body["detail"] == "No subscription provider returned a valid structured probe."


def test_provider_checks_survive_control_api_restart(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'provider-restart.db'}"
    settings = load_settings().model_copy(
        update={
            "public": load_settings().public.model_copy(
                update={
                    "database": load_settings().public.database.model_copy(
                        update={"url": database_url}
                    )
                }
            )
        }
    )

    async def fake_check(provider_id: str) -> SubscriptionHealthResult:
        return SubscriptionHealthResult(
            provider_id=provider_id,
            supported=True,
            status="CONNECTED",
            code="subscription_authenticated",
            detail="official status",
            plan="pro",
            checked_at=datetime.now(UTC),
        )

    monkeypatch.setattr("trading_bot.control_api.check_subscription", fake_check)
    with control_client(create_control_api(settings)) as client:
        assert client.post("/api/v1/providers/anthropic/subscription").status_code == 200

    # A fresh API process has empty in-memory overrides.  The persisted audit
    # event must still project the last known connection into the provider UI.
    with control_client(create_control_api(settings)) as restarted:
        rows = restarted.get("/api/v1/providers").json()
        anthropic = next(row for row in rows if row["provider_id"] == "anthropic")
        assert anthropic["subscription"]["auth_state"] == "CONNECTED"
        assert anthropic["subscription"]["plan"] == "pro"
        assert anthropic["subscription"]["last_checked_at"]
        snapshot = restarted.get("/api/v1/snapshot").json()
        provider_events = [
            row
            for row in snapshot["audit"]
            if row["type"] == "system_events" and row["asset"] == "anthropic"
        ]
        assert provider_events


def test_control_api_projects_source_health_and_agent_memory(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'projections.db'}"
    settings = load_settings().model_copy(
        update={
            "public": load_settings().public.model_copy(
                update={
                    "database": load_settings().public.database.model_copy(
                        update={"url": database_url}
                    )
                }
            )
        }
    )

    async def seed() -> None:
        database = Database(database_url)
        await database.initialize()
        repository = AuditRepository(database)
        now = datetime.now(UTC)
        await repository.append(
            "source_runs",
            {
                "source_id": "sec-press-releases",
                "status": "SUCCEEDED",
                "records_count": 2,
            },
            created_at=now,
            asset="QQQ",
            event_time=now,
            received_time=now,
            processed_time=now,
        )
        await repository.append(
            "agent_outputs",
            {
                "agent_id": "market",
                "agent_version": "1.0.0",
                "spec_hash": "abc123",
                "status": "SUCCEEDED",
                "output": {"evidence": ["volume"], "decision": "READY"},
            },
            created_at=now,
            asset="QQQ",
        )
        await repository.append(
            "positions",
            {
                "position_id": "closed-1",
                "asset": "QQQ",
                "status": "CLOSED",
                "realized_net_pnl": "1.25",
            },
            created_at=now,
            asset="QQQ",
        )
        await database.close()

    import asyncio

    asyncio.run(seed())
    with control_client(create_control_api(settings)) as client:
        body = client.get("/api/v1/snapshot").json()
        source = next(row for row in body["sources"] if row["source_id"] == "sec-press-releases")
        assert source["last_success_at"]
        assert source["records_today"] == 2
        memory = next(row for row in body["agent_memory"] if row["agent_id"] == "market")
        assert memory["prompt_hash"] == "abc123"
        assert memory["evidence_count"] == 1
        assert body["observability"]["source_runs_total"] == 1
        assert body["positions"] == []
        assert body["position_history"][0]["status"] == "CLOSED"
        assert body["decision_trace"]
        assert any(item["type"] == "source_runs" for item in body["decision_trace"])


def test_control_api_validates_and_applies_public_configuration(tmp_path) -> None:
    settings = load_settings().model_copy(
        update={
            "public": load_settings().public.model_copy(
                update={
                    "database": load_settings().public.database.model_copy(
                        update={"url": f"sqlite+aiosqlite:///{tmp_path / 'config.db'}"}
                    )
                }
            )
        }
    )
    app = create_control_api(settings, config_dir=str(tmp_path / "config"))
    with control_client(app) as client:
        payload = {
            "broker_provider": "alpaca",
            "primary_provider": "openai",
            "primary_auth_mode": "subscription",
        }
        valid = client.post("/api/v1/config/validate", json=payload)
        assert valid.status_code == 200
        assert valid.json()["valid"] is True

        applied = client.post("/api/v1/config/apply", json=payload)
        assert applied.status_code == 200
        assert applied.json()["applied"] is True
        assert (tmp_path / "config" / "local.yaml").is_file()

        invalid = client.post(
            "/api/v1/config/validate",
            json={"daily_loss_percent": "3", "weekly_loss_percent": "2"},
        )
        assert invalid.status_code == 200
        assert invalid.json()["valid"] is False


def test_learning_control_path_persists_and_manually_transitions_proposals(tmp_path) -> None:
    settings = load_settings().model_copy(
        update={
            "public": load_settings().public.model_copy(
                update={
                    "database": load_settings().public.database.model_copy(
                        update={"url": f"sqlite+aiosqlite:///{tmp_path / 'learning-api.db'}"}
                    )
                }
            )
        }
    )
    request = {
        "agent": "optimizer",
        "current_version": "weights-v1",
        "candidate_version": "weights-v2",
        "reason": "The challenger improves OOS expectancy after costs.",
        "evidence": ["experiment:123", "replay:oos:456"],
        "affected_rules": ["signal_weights"],
        "expected_improvement": "Higher net expectancy.",
        "risk": "Could reduce precision in thin liquidity.",
        "candidate_spec": {"type": "signal_weights", "files": ["config/strategies.yaml"]},
    }
    with control_client(create_control_api(settings)) as client:
        created = client.post("/api/v1/learning/proposals", json=request)
        assert created.status_code == 200
        proposal = created.json()
        assert proposal["status"] == "PROPOSED"
        proposal_id = proposal["id"]

        listed = client.get("/api/v1/learning/proposals")
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == proposal_id

        no_reason = client.post(
            f"/api/v1/learning/proposals/{proposal_id}/transition",
            json={"target": "TESTING"},
        )
        assert no_reason.status_code == 422  # every review decision needs a reason
        transitioned = client.post(
            f"/api/v1/learning/proposals/{proposal_id}/transition",
            json={"target": "TESTING", "reason": "Probar en sombra una semana"},
        )
        assert transitioned.status_code == 200
        assert transitioned.json()["status"] == "TESTING"
        audit_rows = client.get("/api/v1/snapshot").json()["change_proposals"]
        assert any(
            row.get("transition_reason") == "Probar en sombra una semana"
            for row in audit_rows
        )

        blocked = client.post(
            f"/api/v1/learning/proposals/{proposal_id}/transition",
            json={"target": "DEPLOYED", "reason": "intento de despliegue"},
        )
        assert blocked.status_code == 422


def test_learning_control_path_rejects_sensitive_candidate_fields(tmp_path) -> None:
    settings = load_settings().model_copy(
        update={
            "public": load_settings().public.model_copy(
                update={
                    "database": load_settings().public.database.model_copy(
                        update={"url": f"sqlite+aiosqlite:///{tmp_path / 'learning-sensitive.db'}"}
                    )
                }
            )
        }
    )
    request = {
        "agent": "optimizer",
        "current_version": "weights-v1",
        "candidate_version": "weights-v2",
        "reason": "Candidate evidence.",
        "evidence": ["replay:oos:456"],
        "affected_rules": ["signal_weights"],
        "expected_improvement": "Higher expectancy.",
        "risk": "Candidate risk.",
        "candidate_spec": {"api_token": "must-never-persist"},
    }
    with control_client(create_control_api(settings)) as client:
        response = client.post("/api/v1/learning/proposals", json=request)
        assert response.status_code == 422
        assert "sensitive field" in response.json()["detail"]


def test_operation_timeline_and_operator_resolution(tmp_path) -> None:
    import asyncio

    from trading_bot.broker.lifecycle import OperationState
    from trading_bot.db import OperationLifecycleRepository
    from trading_bot.schemas.common import TradingMode

    settings = load_settings()
    url = f"sqlite+aiosqlite:///{tmp_path / 'operations.db'}"
    settings = settings.model_copy(
        update={
            "public": settings.public.model_copy(
                update={
                    "database": settings.public.database.model_copy(update={"url": url})
                }
            )
        }
    )
    now = datetime(2026, 9, 23, tzinfo=UTC)

    async def seed() -> None:
        database = Database(url)
        await database.initialize()
        lifecycle = OperationLifecycleRepository(database)
        await lifecycle.open(
            operation_id="op-1",
            proposal_id="op-1",
            asset="QQQ",
            mode=TradingMode.PAPER,
            now=now,
        )
        await lifecycle.advance("op-1", OperationState.UNKNOWN, reason="setup", now=now)
        await database.close()

    asyncio.run(seed())
    with control_client(create_control_api(settings)) as client:
        snapshot = client.get("/api/v1/snapshot").json()
        assert snapshot["unresolved_operations"] == 1
        assert snapshot["operations"][0]["state"] == "RECOVERY_REQUIRED"

        timeline = client.get("/api/v1/operations/op-1/events")
        assert timeline.status_code == 200
        assert [row["to_state"] for row in timeline.json()] == [
            "PROPOSED",
            "RECOVERY_REQUIRED",
        ]
        assert client.get("/api/v1/operations/missing/events").status_code == 404

        invalid = client.post(
            "/api/v1/operations/op-1/resolve", json={"target": "EVALUATED", "reason": "skip"}
        )
        assert invalid.status_code == 422
        erased = client.post(
            "/api/v1/operations/op-1/resolve", json={"target": "OPEN", "reason": "no position"}
        )
        assert erased.status_code == 422
        resolved = client.post(
            "/api/v1/operations/op-1/resolve",
            json={"target": "REJECTED", "reason": "venue shows no order"},
        )
        assert resolved.status_code == 200
        assert resolved.json() == {"operation_id": "op-1", "state": "REJECTED"}
        assert client.get("/api/v1/snapshot").json()["unresolved_operations"] == 0


def _isolated_settings(tmp_path):
    settings = load_settings()
    return settings.model_copy(
        update={
            "public": settings.public.model_copy(
                update={
                    "database": settings.public.database.model_copy(
                        update={"url": f"sqlite+aiosqlite:///{tmp_path / 'secure.db'}"}
                    )
                }
            )
        }
    )


def test_control_api_fails_closed_without_configured_token(tmp_path) -> None:
    app = create_control_api(_isolated_settings(tmp_path))
    token = app.state.control_api_token
    assert len(token) >= 32
    with TestClient(app) as client:
        assert client.get("/api/v1/health/live").status_code == 200
        assert client.get("/api/v1/snapshot").status_code == 401
        assert client.post("/api/v1/system/backup").status_code == 401
        assert client.post("/api/v1/memory/cycle").status_code == 401
        assert (
            client.get("/api/v1/snapshot", headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )


def test_control_api_cors_allows_only_explicit_loopback_dev_origins(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "CONTROL_API_EXTRA_ORIGINS", "http://localhost:1427,https://evil.example,tauri://localhost"
    )
    with control_client(create_control_api(_isolated_settings(tmp_path))) as client:
        allowed = client.options(
            "/api/v1/snapshot",
            headers={
                "Origin": "http://localhost:1427",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert allowed.headers.get("access-control-allow-origin") == "http://localhost:1427"
        for origin in ("https://evil.example", "tauri://localhost"):
            rejected = client.options(
                "/api/v1/snapshot",
                headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
            )
            assert "access-control-allow-origin" not in rejected.headers


def test_control_api_stream_accepts_token_subprotocol_only(tmp_path) -> None:
    from starlette.websockets import WebSocketDisconnect

    app = create_control_api(_isolated_settings(tmp_path))
    token = app.state.control_api_token
    with TestClient(app) as client:
        try:
            with client.websocket_connect("/api/v1/stream") as socket:
                socket.receive_json()
            raise AssertionError("unauthenticated stream must be rejected")
        except WebSocketDisconnect as exc:
            assert exc.code == 1008
        with client.websocket_connect(
            "/api/v1/stream", subprotocols=["hyverion.v1", f"hyverion.bearer.{token}"]
        ) as socket:
            assert socket.accepted_subprotocol == "hyverion.v1"
            assert "generated_at" in socket.receive_json()


def test_setup_status_requires_broker_keys(tmp_path) -> None:
    settings = _isolated_settings(tmp_path)
    with control_client(
        create_control_api(settings, config_dir=str(tmp_path / "config"))
    ) as client:
        body = client.get("/api/v1/setup/status").json()
    assert body["configured"] is False
    assert "broker_credentials" in body["missing"]
    assert body["local_config_exists"] is False
    assert body["live_trading"] is False


def test_broker_client_stays_inside_broker_layer() -> None:
    from pathlib import Path

    import trading_bot

    root = Path(trading_bot.__file__).parent
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "AlpacaPaperBroker" in path.read_text(encoding="utf-8")
        and path.parent.name != "broker"
        and path.name != "factory.py"
    ]
    assert offenders == []


def test_desktop_snapshot_contract_matches_build_snapshot(tmp_path) -> None:
    """The Tauri frontend pins the snapshot's top-level keys in snapshot-keys.json."""
    import json
    from pathlib import Path

    expected = json.loads(
        (Path(__file__).parents[2] / "app" / "src" / "api" / "snapshot-keys.json").read_text()
    )
    with control_client(create_control_api(_isolated_settings(tmp_path))) as client:
        body = client.get("/api/v1/snapshot").json()
    assert sorted(body) == expected
