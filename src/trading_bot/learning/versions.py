"""Versions of agents and strategy parameters, and what a proposal may change.

A version is an immutable row in ``component_versions``; the active one has no
``active_to``. Only two kinds of change can be applied from the app:

- ``strategy_params``: the exit geometry and blocked regimes of one strategy,
  inside fixed bounds;
- ``agent_spec``: the full AGENT.md text of a non-safety LLM agent.

Nothing here can touch ``RiskConfig``, the critic, the risk engine or the
execution path: candidates are parsed by strict schemas and any risk-limit key
fails closed with ``forbidden_target``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, ValidationError
from sqlalchemy import insert, select, update

from trading_bot.config.models import RiskConfig
from trading_bot.db.database import Database
from trading_bot.db.models import component_versions
from trading_bot.memory.meta import stance_correct
from trading_bot.schemas.common import StrictSchema
from trading_bot.schemas.learning import ChangeProposal
from trading_bot.strategies.base import ExitParams, PluginOverrides

FilterableRegime = Literal["trending_up", "trending_down", "ranging"]
ComponentKind = Literal["agent", "strategy"]

# Deterministic safety components and the optimizer itself are never editable
# from a proposal: the critic and risk keep final authority over every entry.
PROTECTED_AGENTS = frozenset(
    {"critic", "optimizer", "position_manager", "session_guardian", "master_orchestrator"}
)
AGENT_REQUIRED_SECTIONS = (
    "ROLE",
    "OBJECTIVE",
    "INPUTS",
    "OUTPUTS",
    "RULES",
    "PROHIBITED ACTIONS",
    "VERSION",
)
_RISK_KEYS = frozenset(RiskConfig.model_fields) | {"risk", "ladder", "leverage", "live_trading"}
_VERSION = re.compile(r"^##\s+VERSION\s*\n+\s*([^\n]+)", re.MULTILINE)


class ChangeNotApplicable(ValueError):
    """A proposal that cannot be applied; ``code`` is shown in Spanish by the app."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StrategyParams(StrictSchema):
    """Tunable, bounded parameters of one strategy (never a risk limit)."""

    stop_percent: Decimal = Field(ge=Decimal("0.3"), le=Decimal("3"))
    reward_multiple: Decimal = Field(ge=Decimal("1"), le=Decimal("5"))
    horizon_minutes: int = Field(ge=15, le=1440)
    blocked_regimes: tuple[FilterableRegime, ...] = ()

    @classmethod
    def from_exit(cls, exit_params: ExitParams) -> StrategyParams:
        return cls(
            stop_percent=exit_params.stop_percent,
            reward_multiple=exit_params.reward_multiple,
            horizon_minutes=max(15, exit_params.horizon_seconds // 60),
        )

    def plugin_overrides(self) -> PluginOverrides:
        return PluginOverrides(
            exit_params=self.exit_params(), blocked_regimes=self.blocked_regimes
        )

    def exit_params(self) -> ExitParams:
        return ExitParams(
            stop_percent=self.stop_percent,
            reward_multiple=self.reward_multiple,
            horizon_seconds=self.horizon_minutes * 60,
        )


class _StrategyCandidate(StrictSchema):
    kind: Literal["strategy_params"]
    strategy_id: str
    params: StrategyParams


class _AgentCandidate(StrictSchema):
    kind: Literal["agent_spec"]
    spec_markdown: str = Field(min_length=40, max_length=30_000)


@dataclass(frozen=True, slots=True)
class ApplicableChange:
    component_id: str
    kind: ComponentKind
    version: str
    params: dict[str, Any]
    spec_hash: str | None


def _keys(value: object) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _keys(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _keys(item)


def spec_version(markdown: str) -> str | None:
    match = _VERSION.search(markdown)
    return match.group(1).strip() if match else None


def parse_change(
    proposal: ChangeProposal,
    *,
    strategies: Iterable[str],
    agents: Iterable[str],
) -> ApplicableChange:
    """Validate what a proposal would change; fail closed on anything else."""

    spec = proposal.candidate_spec
    if set(_keys(spec)) & _RISK_KEYS:
        raise ChangeNotApplicable("forbidden_target")
    kind = spec.get("kind")
    if kind == "strategy_params":
        try:
            strategy = _StrategyCandidate.model_validate(spec)
        except ValidationError as exc:
            raise ChangeNotApplicable("invalid_params") from exc
        if strategy.strategy_id not in set(strategies):
            raise ChangeNotApplicable("unknown_component")
        return ApplicableChange(
            component_id=strategy.strategy_id,
            kind="strategy",
            version=proposal.candidate_version,
            params=strategy.params.model_dump(mode="json"),
            spec_hash=None,
        )
    if kind == "agent_spec":
        try:
            agent = _AgentCandidate.model_validate(spec)
        except ValidationError as exc:
            raise ChangeNotApplicable("invalid_params") from exc
        if proposal.agent in PROTECTED_AGENTS:
            raise ChangeNotApplicable("forbidden_target")
        if proposal.agent not in set(agents):
            raise ChangeNotApplicable("unknown_component")
        headings = set(re.findall(r"^##\s+([A-Z][A-Z _/-]*)\s*$", agent.spec_markdown, re.M))
        if not set(AGENT_REQUIRED_SECTIONS) <= {h.strip() for h in headings}:
            raise ChangeNotApplicable("invalid_params")
        if spec_version(agent.spec_markdown) != proposal.candidate_version:
            raise ChangeNotApplicable("version_mismatch")
        return ApplicableChange(
            component_id=proposal.agent,
            kind="agent",
            version=proposal.candidate_version,
            params={"spec_markdown": agent.spec_markdown},
            spec_hash=hashlib.sha256(agent.spec_markdown.encode("utf-8")).hexdigest(),
        )
    raise ChangeNotApplicable("not_applicable")


def _utc(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class VersionRepository:
    """Append-only store of component versions."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def history(self, component_id: str) -> list[dict[str, Any]]:
        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                select(component_versions)
                .where(component_versions.c.component_id == component_id)
                .order_by(component_versions.c.active_from, component_versions.c.id)
            )
            return [dict(row) for row in result.mappings().all()]

    async def active(self, kind: ComponentKind | None = None) -> dict[str, dict[str, Any]]:
        statement = select(component_versions).where(component_versions.c.active_to.is_(None))
        if kind is not None:
            statement = statement.where(component_versions.c.kind == kind)
        async with self._database.engine.connect() as connection:
            result = await connection.execute(statement)
            return {str(row["component_id"]): dict(row) for row in result.mappings().all()}

    async def activate(
        self,
        change: ApplicableChange,
        *,
        proposal_id: str | None,
        reason: str,
        now: datetime,
        replaces_id: str | None = None,
        use_active_as_replaced: bool = True,
    ) -> None:
        """Close the active version (if any) and open the new one in one transaction."""

        async with self._database.engine.begin() as connection:
            current = (
                await connection.execute(
                    select(component_versions.c.id).where(
                        component_versions.c.component_id == change.component_id,
                        component_versions.c.active_to.is_(None),
                    )
                )
            ).scalar_one_or_none()
            await connection.execute(
                update(component_versions)
                .where(
                    component_versions.c.component_id == change.component_id,
                    component_versions.c.active_to.is_(None),
                )
                .values(active_to=now)
            )
            await connection.execute(
                insert(component_versions).values(
                    id=str(uuid4()),
                    component_id=change.component_id,
                    kind=change.kind,
                    version=change.version,
                    spec_hash=change.spec_hash,
                    params=change.params,
                    proposal_id=proposal_id,
                    replaces_id=current if use_active_as_replaced else replaces_id,
                    reason=reason[:255],
                    active_from=now,
                    active_to=None,
                )
            )

    async def strategy_params(self) -> dict[str, StrategyParams]:
        """Active approved parameters per strategy (built-in defaults otherwise)."""

        params: dict[str, StrategyParams] = {}
        for component_id, row in (await self.active("strategy")).items():
            try:
                params[component_id] = StrategyParams.model_validate(row["params"])
            except ValidationError:
                continue  # a corrupt row never loosens anything: defaults apply
        return params

    async def plugin_overrides(self) -> dict[str, PluginOverrides]:
        params = await self.strategy_params()
        return {key: value.plugin_overrides() for key, value in params.items()}

    async def agent_overrides(self) -> dict[str, str]:
        """Approved AGENT.md texts that replace the bundled spec."""

        overrides: dict[str, str] = {}
        for component_id, row in (await self.active("agent")).items():
            text = (row.get("params") or {}).get("spec_markdown")
            if isinstance(text, str) and component_id not in PROTECTED_AGENTS:
                overrides[component_id] = text
        return overrides

    async def record_baselines(
        self,
        baselines: Iterable[ApplicableChange],
        *,
        now: datetime,
    ) -> None:
        """Open a first version for new components and record edited AGENT.md files.

        A bundled spec whose hash changed is recorded as a new version, unless an
        approved override is active (the override stays in charge).
        """

        active = await self.active()
        for baseline in baselines:
            current = active.get(baseline.component_id)
            if current is None:
                await self.activate(baseline, proposal_id=None, reason="Versión inicial", now=now)
                continue
            overridden = "spec_markdown" in (current.get("params") or {})
            if (
                baseline.kind == "agent"
                and not overridden
                and current.get("spec_hash") != baseline.spec_hash
            ):
                await self.activate(
                    baseline,
                    proposal_id=None,
                    reason="Cambio detectado en AGENT.md",
                    now=now,
                )

    async def rollback(self, component_id: str, *, reason: str, now: datetime) -> dict[str, Any]:
        """Undo the active approved change: go back to the version it replaced.

        Only a version that came from an approved change can be undone, and the
        restored copy keeps the origin of the version it restores, so repeated
        undos walk further back and never re-apply what was just undone.
        """

        history = await self.history(component_id)
        active = next((row for row in history if row.get("active_to") is None), None)
        by_id = {str(row["id"]): row for row in history}
        target = by_id.get(str(active.get("replaces_id"))) if active else None
        if active is None or not active.get("proposal_id") or target is None:
            raise ChangeNotApplicable("nothing_to_undo")
        restored = ApplicableChange(
            component_id=component_id,
            kind=target["kind"],
            version=str(target["version"]),
            params=dict(target["params"] or {}),
            spec_hash=target.get("spec_hash"),
        )
        await self.activate(
            restored,
            proposal_id=target.get("proposal_id"),
            reason=f"Deshacer: {reason}",
            now=now,
            replaces_id=target.get("replaces_id"),
            use_active_as_replaced=False,
        )
        return active


def window_metrics(
    *,
    component_id: str,
    kind: ComponentKind,
    start: datetime,
    end: datetime | None,
    evaluations: Iterable[Mapping[str, Any]],
    strategy_of: Mapping[str, str],
    attributions: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Real results while one version was active.

    Strategies: evaluated trades whose proposal came from that strategy.
    Agents: how often the agent's stance matched the trade outcome.
    """

    def inside(value: object) -> bool:
        moment = _utc(value) if isinstance(value, datetime) else _parse(value)
        return moment is not None and moment >= start and (end is None or moment < end)

    outcomes = {
        str(item.get("trade_id")): Decimal(str(item.get("realized_net_pnl")))
        for item in evaluations
        if item.get("trade_id") and item.get("realized_net_pnl") is not None
        and inside(item.get("evaluated_at"))
    }
    if kind == "strategy":
        pnls = [pnl for trade, pnl in outcomes.items() if strategy_of.get(trade) == component_id]
        wins = sum(1 for pnl in pnls if pnl > 0)
        return {
            "samples": len(pnls),
            "correct": wins,
            "accuracy_percent": _percent(wins, len(pnls)),
            "net_pnl_usd": str(sum(pnls, Decimal("0"))) if pnls else None,
        }
    samples = correct = 0
    for item in attributions:
        decision = str(item.get("decision_id") or "")
        stances = item.get("stances")
        if decision not in outcomes or not isinstance(stances, Mapping):
            continue
        entry = stances.get(component_id)
        stance = entry.get("stance") if isinstance(entry, Mapping) else None
        verdict = stance_correct(str(stance), str(item.get("side") or ""), outcomes[decision])
        if verdict is None:
            continue
        samples += 1
        correct += 1 if verdict else 0
    return {
        "samples": samples,
        "correct": correct,
        "accuracy_percent": _percent(correct, samples),
        "net_pnl_usd": None,
    }


def _percent(part: int, total: int) -> str | None:
    if total == 0:
        return None
    return str((Decimal(part) * Decimal(100) / Decimal(total)).quantize(Decimal("0.1")))


def _parse(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return _utc(datetime.fromisoformat(str(value)))
    except ValueError:
        return None
