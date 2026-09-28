"""Component versions, human-approved apply/undo and the deterministic optimizer.

Safety invariants covered here:
- a proposal can only change bounded strategy parameters or a non-safety agent spec;
- any risk-limit key fails closed and nothing is written;
- the regime filter only removes entries and reads past closes only;
- the optimizer never applies anything: it writes a proposal for review, once.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import control_client
from test_snapshot_consolidation import _settings
from test_strategy_replay import candles, zigzag

from trading_bot.agents.registry import AgentRegistry
from trading_bot.config import load_settings
from trading_bot.control_api import create_control_api
from trading_bot.core.clock import FixedClock
from trading_bot.data.features import FeatureEngine
from trading_bot.db import Database
from trading_bot.learning import optimizer_job
from trading_bot.learning.optimizer_job import (
    Finding,
    bump_minor,
    candidates,
    describe_change,
    optimizer_lock,
    run_optimizer,
)
from trading_bot.learning.versions import (
    ApplicableChange,
    ChangeNotApplicable,
    StrategyParams,
    VersionRepository,
    parse_change,
)
from trading_bot.schemas.learning import ChangeProposal
from trading_bot.simulation.strategy_replay import ReplayStats, replay_plugin
from trading_bot.strategies.base import PluginOverrides
from trading_bot.strategies.regime import RegimeFilteredStrategy, classify_regime
from trading_bot.strategies.registry import (
    build_strategy_ensemble,
    exit_defaults,
    plugin_factory,
)

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
# The version mechanics are exercised on the price-only baseline plugin.
STRATEGIES = load_settings().public.strategies.model_copy(update={"enabled": ("trend_momentum",)})
AGENTS = [item.agent_id for item in AgentRegistry().descriptors()]
DEFAULT = StrategyParams.from_exit(exit_defaults("trend_momentum"))


def _proposal(candidate_spec: dict, *, agent: str = "trend_momentum",
              candidate_version: str = "1.1.0") -> ChangeProposal:
    return ChangeProposal(
        id="p-1",
        agent=agent,
        current_version="1.0.0",
        candidate_version=candidate_version,
        reason="r",
        evidence=("e",),
        affected_rules=("x",),
        expected_improvement="i",
        risk="k",
        candidate_spec=candidate_spec,
        created_at=NOW,
        status="READY_FOR_REVIEW",
    )


def _strategy_spec(strategy_id: str = "trend_momentum", **params: object) -> dict:
    defaults = StrategyParams.from_exit(exit_defaults(strategy_id))
    return {
        "kind": "strategy_params",
        "strategy_id": strategy_id,
        "params": {**defaults.model_dump(mode="json"), **params},
    }


def _parse(spec: dict, **kwargs: object) -> ApplicableChange:
    return parse_change(_proposal(spec, **kwargs), strategies=STRATEGIES.enabled, agents=AGENTS)


def _code(spec: dict, **kwargs: object) -> str:
    with pytest.raises(ChangeNotApplicable) as caught:
        _parse(spec, **kwargs)
    return caught.value.code


def test_only_bounded_strategy_params_and_safe_agent_specs_apply() -> None:
    change = _parse(_strategy_spec(blocked_regimes=["ranging"]))
    assert change.kind == "strategy"
    assert change.params["blocked_regimes"] == ["ranging"]

    # Risk limits can never be changed through a proposal, wherever the key hides.
    assert _code({**_strategy_spec(), "daily_loss_hard_cap_usd": "999"}) == "forbidden_target"
    assert _code(_strategy_spec(max_account_drawdown_percent="50")) == "forbidden_target"
    assert _code(_strategy_spec(stop_percent="9")) == "invalid_params"  # outside 0.3..3
    assert _code(_strategy_spec(horizon_minutes=5)) == "invalid_params"
    assert _code({"kind": "signal_weights"}) == "not_applicable"
    assert (
        _code({**_strategy_spec(), "strategy_id": "martingale"}) == "unknown_component"
    )

    spec = (Path("agents") / "news" / "AGENT.md").read_text().replace(
        "## VERSION\n2.0.0", "## VERSION\n2.1.0"
    )
    agent = _parse(
        {"kind": "agent_spec", "spec_markdown": spec}, agent="news", candidate_version="2.1.0"
    )
    assert agent.kind == "agent" and agent.version == "2.1.0"
    assert _code(
        {"kind": "agent_spec", "spec_markdown": spec}, agent="critic", candidate_version="2.1.0"
    ) == (
        "forbidden_target"
    )
    assert _code(
        {"kind": "agent_spec", "spec_markdown": spec}, agent="news", candidate_version="3.0.0"
    ) == "version_mismatch"
    assert _code(
        {"kind": "agent_spec", "spec_markdown": "## ROLE\nsolo un rol, sin nada más " * 3},
        agent="news",
    ) == "invalid_params"


def _database(tmp_path, name: str) -> Database:
    return Database(f"sqlite+aiosqlite:///{tmp_path / name}")


def test_versions_activate_and_rollback_append_only(tmp_path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path, "versions.db")
        await database.initialize()
        repo = VersionRepository(database)
        baseline = ApplicableChange("trend_momentum", "strategy", "1.0.0",
                                    DEFAULT.model_dump(mode="json"), None)
        await repo.record_baselines([baseline], now=NOW)
        await repo.record_baselines([baseline], now=NOW)  # idempotent
        assert len(await repo.history("trend_momentum")) == 1
        with pytest.raises(ChangeNotApplicable, match="nothing_to_undo"):
            await repo.rollback("trend_momentum", reason="nada", now=NOW)

        change = _parse(_strategy_spec(blocked_regimes=["ranging"]))
        await repo.activate(change, proposal_id="p-1", reason="ok", now=NOW + timedelta(1))
        params = await repo.strategy_params()
        assert params["trend_momentum"].blocked_regimes == ("ranging",)

        second = ApplicableChange("trend_momentum", "strategy", "1.2.0",
                                  {**change.params, "horizon_minutes": 120}, None)
        await repo.activate(second, proposal_id="p-2", reason="ok", now=NOW + timedelta(2))

        undone = await repo.rollback("trend_momentum", reason="peor", now=NOW + timedelta(3))
        assert undone["version"] == "1.2.0"
        assert (await repo.active())["trend_momentum"]["version"] == "1.1.0"
        # Undoing again walks further back; it never re-applies what was just undone.
        undone = await repo.rollback("trend_momentum", reason="tampoco", now=NOW + timedelta(4))
        assert undone["version"] == "1.1.0"
        history = await repo.history("trend_momentum")
        assert [row["version"] for row in history] == ["1.0.0", "1.1.0", "1.2.0", "1.1.0", "1.0.0"]
        assert [row["active_to"] is None for row in history] == [False, False, False, False, True]
        assert (await repo.strategy_params())["trend_momentum"].blocked_regimes == ()
        with pytest.raises(ChangeNotApplicable, match="nothing_to_undo"):
            await repo.rollback("trend_momentum", reason="nada más", now=NOW + timedelta(5))
        await database.close()

    asyncio.run(scenario())


def test_agent_file_edits_are_recorded_unless_an_override_is_active(tmp_path) -> None:
    async def scenario() -> None:
        database = _database(tmp_path, "agents.db")
        await database.initialize()
        repo = VersionRepository(database)

        def bundle(spec_hash: str) -> ApplicableChange:
            return ApplicableChange("news", "agent", "1.0.0", {"source": "bundle"}, spec_hash)

        await repo.record_baselines([bundle("a")], now=NOW)
        await repo.record_baselines([bundle("b")], now=NOW + timedelta(1))
        assert [r["reason"] for r in await repo.history("news")] == [
            "Versión inicial",
            "Cambio detectado en AGENT.md",
        ]
        override = ApplicableChange("news", "agent", "1.1.0", {"spec_markdown": "x"}, "c")
        await repo.activate(override, proposal_id="p", reason="ok", now=NOW + timedelta(2))
        await repo.record_baselines([bundle("d")], now=NOW + timedelta(3))
        assert (await repo.active())["news"]["version"] == "1.1.0"
        assert await repo.agent_overrides() == {"news": "x"}
        await database.close()

    asyncio.run(scenario())


def test_regime_filter_only_silences_the_blocked_regime() -> None:
    features = FeatureEngine()
    flat = tuple(Decimal("100") + Decimal(i % 2) / 100 for i in range(20))
    rising = tuple(Decimal("100") + Decimal(i) for i in range(20))
    assert classify_regime(flat) == "ranging"
    assert classify_regime(rising) == "trending_up"

    class Always:
        strategy_id = "always"
        version = "1"

        def propose(self, snapshot, features, *, risk_budget_usd):
            return "proposal"

    wrapped = RegimeFilteredStrategy(Always(), ("ranging",))
    assert wrapped.propose(None, features.compute("X", flat), risk_budget_usd=Decimal(1)) is None
    assert wrapped.propose(None, features.compute("X", rising), risk_budget_usd=Decimal(1)) == (
        "proposal"
    )


def test_engine_and_replay_build_the_same_filtered_plugin() -> None:
    overrides = {"trend_momentum": PluginOverrides(blocked_regimes=("ranging",))}
    live = build_strategy_ensemble(STRATEGIES, clock=FixedClock(NOW), overrides=overrides)
    research = plugin_factory(STRATEGIES, "trend_momentum", overrides["trend_momentum"])
    assert isinstance(live, RegimeFilteredStrategy)
    assert isinstance(research(FixedClock(NOW)), RegimeFilteredStrategy)

    bars = candles(zigzag(400))
    plain = replay_plugin(plugin_factory(STRATEGIES, "trend_momentum", None), bars,
                          capital=Decimal("1000"))
    blocked = replay_plugin(research, bars, capital=Decimal("1000"))
    total = plain.in_sample.trades + plain.out_of_sample.trades
    assert blocked.in_sample.trades + blocked.out_of_sample.trades <= total


def test_optimizer_candidates_stay_inside_bounds() -> None:
    options = candidates(DEFAULT)
    assert options[0] == DEFAULT
    assert len(options) == len({o.model_dump_json() for o in options})
    for option in options:
        StrategyParams.model_validate(option.model_dump())  # all within the allowlist
    assert bump_minor("1.0.0") == "1.1.0"
    assert bump_minor("1.4.2") == "1.5.0"
    lines = describe_change(DEFAULT, DEFAULT.model_copy(update={"blocked_regimes": ("ranging",)}))
    assert lines == ["No operar en mercado lateral"]


def _stats(trades: int, wins: int, net: str) -> ReplayStats:
    return ReplayStats(trades=trades, wins=wins, net_pnl_usd=Decimal(net),
                       fees_usd=Decimal("1"), max_drawdown_usd=Decimal("5"), exits={})


def test_optimizer_proposes_once_for_review_and_never_applies(tmp_path) -> None:
    candidate = DEFAULT.model_copy(update={"blocked_regimes": ("ranging",)})
    outcomes = {
        "trend_momentum": Finding("trend_momentum", DEFAULT, candidate,
                                  _stats(90, 14, "-26"), _stats(60, 19, "-4.10"), True, ()),
    }
    fetched: list[tuple[str, int]] = []

    async def history(symbol: str, minutes: int):
        fetched.append((symbol, minutes))
        return ()

    async def runner(task):
        return outcomes[task.args[1]] if task.args[1] in outcomes else None

    strategies = STRATEGIES.model_copy(update={"enabled": ("trend_momentum",)})

    async def scenario() -> tuple[dict, dict]:
        database = _database(tmp_path, "optimizer.db")
        await database.initialize()
        kwargs = dict(database=database, strategies=strategies, symbols=("QQQ",),
                      capital=Decimal("1000"), candle_history=history,
                      clock=FixedClock(NOW), runner=runner)
        first = await run_optimizer(**kwargs)
        second = await run_optimizer(**kwargs)
        active = await VersionRepository(database).active()
        await database.close()
        return first, {"second": second, "active": active}

    first, rest = asyncio.run(scenario())
    assert fetched[0] == ("QQQ", optimizer_job.HISTORY_MINUTES)
    assert first["proposed"] == 1
    assert rest["second"]["results"][0]["outcome"] == "pending_review"  # no duplicates
    assert rest["active"] == {}  # nothing applied by the optimizer


def test_optimizer_lock_admits_one_runner(tmp_path) -> None:
    with optimizer_lock(tmp_path) as first:
        with optimizer_lock(tmp_path) as second:
            assert first is True
            assert second is False


def test_apply_and_undo_from_the_api(tmp_path) -> None:
    settings = _settings(tmp_path, "apply.db")
    request = {
        "agent": "trend_pullback",
        "current_version": "1.0.0",
        "candidate_version": "1.1.0",
        "reason": "Pierde en lateral.",
        "evidence": ["experiment:1"],
        "affected_rules": ["blocked_regimes"],
        "expected_improvement": "Menos pérdidas.",
        "risk": "Opera menos.",
        "candidate_spec": _strategy_spec("trend_pullback", blocked_regimes=["ranging"]),
    }
    risky = {
        **request,
        "candidate_spec": _strategy_spec("trend_pullback", daily_loss_hard_cap_usd="1000"),
    }
    with control_client(create_control_api(settings)) as client:
        good = client.post("/api/v1/learning/proposals", json=request).json()["id"]
        bad = client.post("/api/v1/learning/proposals", json=risky).json()["id"]
        for proposal_id in (good, bad):
            for target in ("TESTING", "READY_FOR_REVIEW"):
                client.post(
                    f"/api/v1/learning/proposals/{proposal_id}/transition",
                    json={"target": target, "reason": "revisión"},
                )
        forbidden = client.post(
            f"/api/v1/learning/proposals/{bad}/apply", json={"reason": "intento"}
        )
        applied = client.post(
            f"/api/v1/learning/proposals/{good}/apply", json={"reason": "Aprobado tras revisar"}
        )
        again = client.post(f"/api/v1/learning/proposals/{good}/apply", json={"reason": "otra"})
        components = {c["component_id"]: c for c in client.get("/api/v1/components").json()}
        history = client.get("/api/v1/components/trend_pullback/history").json()
        undone = client.post(
            "/api/v1/learning/components/trend_pullback/rollback", json={"reason": "empeoró"}
        )
        undone_again = client.post(
            "/api/v1/learning/components/trend_pullback/rollback", json={"reason": "otra vez"}
        )
        timeline = {i["id"]: i for i in client.get("/api/v1/learning/timeline").json()}
        after = {c["component_id"]: c for c in client.get("/api/v1/components").json()}
        spec = client.get("/api/v1/agents/news/spec").json()

    assert forbidden.status_code == 422
    assert forbidden.json()["detail"]["code"] == "forbidden_target"
    assert timeline[bad]["status"] == "READY_FOR_REVIEW"  # nothing written for it
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "DEPLOYED"
    assert again.json()["detail"]["code"] == "invalid_state"
    assert components["trend_pullback"]["version"] == "1.1.0"
    assert [h["version"] for h in history] == ["1.0.0", "1.1.0"]
    assert history[-1]["proposal"]["reason"] == "Pierde en lateral."
    assert history[-1]["results"]["samples"] == 0
    assert undone.json()["active_version"] == "1.0.0"
    assert undone_again.json()["detail"]["code"] == "nothing_to_undo"
    assert timeline[good]["status"] == "ROLLED_BACK"
    assert [h["status"] for h in timeline[good]["history"]][-3:] == [
        "APPROVED",
        "DEPLOYED",
        "ROLLED_BACK",
    ]
    assert after["trend_pullback"]["params"]["blocked_regimes"] == []
    assert spec["agent_id"] == "news" and "## VERSION" in spec["spec_markdown"]


def test_champion_is_the_active_version_including_its_blocked_regimes(monkeypatch) -> None:
    """Regression: the baseline must be the version in use, not a copy without its filter."""

    active = DEFAULT.model_copy(update={"blocked_regimes": ("ranging",)})
    assert candidates(active)[0] == active

    evaluated: list[StrategyParams] = []

    def fake_evaluate(strategies, strategy_id, params, candles_by_symbol, *, capital):
        evaluated.append(params)
        # Trading in ranging markets loses: a baseline without the filter would look worse.
        in_sample = "5" if params == active else "-10" if not params.blocked_regimes else "6"
        out = "-1" if params == active else "-2"
        return optimizer_job.Pooled(
            in_sample=_stats(80, 20, in_sample), out_of_sample=_stats(80, 20, out)
        )

    monkeypatch.setattr(optimizer_job, "evaluate", fake_evaluate)
    finding = optimizer_job.search(
        STRATEGIES, "trend_momentum", active, {}, capital=Decimal("1000")
    )

    assert evaluated[0] == active
    assert finding is not None
    assert finding.before.net_pnl_usd == Decimal("-1")  # the real active version's result
    assert finding.candidate != active
    assert finding.ready is False  # -2 OOS is not better than the real -1


def test_no_proposal_when_no_challenger_beats_the_champion_in_sample(monkeypatch) -> None:
    def fake_evaluate(strategies, strategy_id, params, candles_by_symbol, *, capital):
        value = "5" if params == DEFAULT else "1"
        return optimizer_job.Pooled(
            in_sample=_stats(80, 20, value), out_of_sample=_stats(80, 20, value)
        )

    monkeypatch.setattr(optimizer_job, "evaluate", fake_evaluate)
    found = optimizer_job.search(STRATEGIES, "trend_momentum", DEFAULT, {}, capital=Decimal("1000"))
    assert found is None
