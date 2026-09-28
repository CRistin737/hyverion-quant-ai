"""Executable form of the AGENTS.md safety boundary.

DATA -> AGENTS -> PROPOSAL -> CRITIC -> RISK -> EXECUTION -> BROKER: AI-facing code
must never reach a broker client, and only ExecutionEngine may submit orders.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "trading_bot"
AGENTS = Path(__file__).resolve().parents[2] / "agents"

EXECUTION_MODULES = {"execution", "simulator", "alpaca"}
EXECUTION_NAMES = {"ExecutionEngine", "SimulatedBroker", "AlpacaPaperBroker"}
# Packages that host or feed AI/agent/learning logic: no path to a broker client.
AI_FACING = ("agents", "providers", "memory", "learning", "strategies", "data", "simulation")
# The only modules allowed to construct or hold the execution boundary.
EXECUTION_OWNERS = {
    "main.py",
    "core/orchestrator.py",
    "core/recovery.py",
}


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def _touches_execution(imported: set[str]) -> set[str]:
    hits: set[str] = set()
    for name in imported:
        parts = name.split(".")
        if parts[:2] == ["trading_bot", "broker"] and (
            EXECUTION_MODULES & set(parts) or parts[-1] in EXECUTION_NAMES
        ):
            hits.add(name)
    return hits


def _python_files(*packages: str) -> list[Path]:
    return [f for pkg in packages for f in sorted((SRC / pkg).rglob("*.py"))]


@pytest.mark.parametrize(
    "path", _python_files(*AI_FACING), ids=lambda p: p.relative_to(SRC).as_posix()
)
def test_ai_facing_code_never_imports_execution_or_broker_clients(path: Path) -> None:
    assert _touches_execution(_imports(path)) == set()


@pytest.mark.parametrize("path", _python_files("risk"), ids=lambda p: p.relative_to(SRC).as_posix())
def test_risk_engine_is_pure_and_has_no_broker_dependencies(path: Path) -> None:
    broker = {n for n in _imports(path) if n.startswith("trading_bot.broker")}
    assert broker <= {"trading_bot.broker.models"}, broker


def test_only_the_execution_owners_hold_the_execution_engine() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("broker/") or rel in EXECUTION_OWNERS:
            continue
        names = {n.rsplit(".", 1)[-1] for n in _imports(path)}
        if names & EXECUTION_NAMES:
            offenders.append(rel)
    # control_api may import verification helpers, never the engine or adapters.
    assert offenders == [], offenders


def test_control_api_cannot_construct_an_execution_client() -> None:
    imported = _imports(SRC / "control_api.py")
    assert not any(n.endswith(tuple(EXECUTION_NAMES)) for n in imported)


def test_orders_are_only_submitted_inside_the_broker_package() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("broker/"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"submit", "cancel_replace", "cancel_order", "place_order"}
                # ExecutionEngine.execute is the sanctioned path; adapters are off limits.
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr in {"_adapter", "adapter"}
            ):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], offenders


def test_retired_crypto_venue_is_gone_from_the_core() -> None:
    """QQQ migration: only the v1 -> v2 config migrator may name the old venue."""

    offenders = [
        path.relative_to(SRC).as_posix()
        for path in sorted(SRC.rglob("*.py"))
        if "binance" in path.read_text().lower()
        and path.relative_to(SRC).as_posix() != "config/migrate.py"
    ]
    assert offenders == [], offenders


# Spec-only deterministic components (not model-driven).
DETERMINISTIC_AGENTS = {"master_orchestrator"}
REQUIRED = ("ROLE", "OBJECTIVE", "INPUTS", "OUTPUTS", "TOOLS", "PROHIBITED ACTIONS", "VERSION")


@pytest.mark.parametrize("spec", sorted(AGENTS.glob("*/AGENT.md")), ids=lambda p: p.parent.name)
def test_every_agent_spec_declares_its_limits(spec: Path) -> None:
    text = spec.read_text()
    for heading in REQUIRED:
        assert f"## {heading}" in text, f"{spec.parent.name} missing {heading}"
    tools = text.split("## TOOLS", 1)[1].split("## ", 1)[0].strip().lower()
    # No agent gets shell, filesystem, network or broker tools.
    if spec.parent.name in DETERMINISTIC_AGENTS:
        # Deterministic code, not an LLM: it may hold typed internal interfaces.
        assert "typed interfaces only" in tools
    else:
        assert tools.startswith(("none", "read-only")), tools
    prohibited = text.split("## PROHIBITED ACTIONS", 1)[1].split("## ", 1)[0].lower()
    assert "credential" in prohibited
    assert any(word in prohibited for word in ("order", "trade", "trading"))


def test_agent_tools_are_read_only_and_match_every_spec() -> None:
    from trading_bot.agents.tools import AGENT_TOOLS, TOOLS, tool_view

    forbidden = ("submit", "cancel", "replace", "order", "credential", "secret", "write", "shell")
    for name in TOOLS:
        assert not any(word in name for word in forbidden), name
    for spec in sorted(AGENTS.glob("*/AGENT.md")):
        agent_id = spec.parent.name
        tools = spec.read_text().split("## TOOLS", 1)[1].split("## ", 1)[0]
        for tool in AGENT_TOOLS.get(agent_id, ()):
            assert f"`{tool}`" in tools or agent_id in DETERMINISTIC_AGENTS, (agent_id, tool)
        listed = set(re.findall(r"`(get_[a-z_]+|query_memory)`", tools))
        assert listed <= set(AGENT_TOOLS.get(agent_id, ())), (agent_id, listed)
    # An agent only ever sees its own slices, labelled untrusted.
    view = tool_view("news", {"news": [1], "macro": {"gate": None}, "breadth": None})
    assert view["trust"] == "UNTRUSTED_EXTERNAL_DATA"
    assert set(view["tools"]) == {"get_component_news"}
