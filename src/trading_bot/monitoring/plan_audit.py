"""Machine-checkable audit for the durable implementation plan.

The report checks documentation and local contracts only.  It deliberately
does not turn a ``COMPLETE`` row into LIVE authorization: authenticated
exchange, provider and VPS gates remain represented by their matrix status.
"""

from __future__ import annotations

import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.resources import files as package_files
from importlib.util import find_spec
from pathlib import Path
from typing import Any

_MATRIX_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]+)\|\s*([^|]+)\|([^|]+)\|([^|]+)\|")
_REQUIRED_AGENT_SECTIONS = (
    "ROLE",
    "OBJECTIVE",
    "INPUTS",
    "OUTPUTS",
    "TOOLS",
    "RULES",
    "PROHIBITED ACTIONS",
    "FAILURE CONDITIONS",
    "QUALITY CHECKLIST",
    "VERSION",
    "CHANGE HISTORY",
)
_PATH_TOKEN = re.compile(r"`([^`]+)`")
# The plan matrix is internal material kept under ``private/`` (excluded from the
# public export); the legacy ``spec/`` location is still honoured.
_MATRIX_LOCATIONS = (
    Path("private") / "spec" / "PLAN_COVERAGE.md",
    Path("spec") / "PLAN_COVERAGE.md",
)


@dataclass(frozen=True)
class PlanAuditItem:
    requirement_id: int
    requirement: str
    status: str
    missing_evidence: tuple[str, ...]
    remaining_gate: str

    @property
    def evidence_ok(self) -> bool:
        return not self.missing_evidence


@dataclass(frozen=True)
class PlanAuditReport:
    generated_at: str
    root: str
    overall: str
    requirements_total: int
    status_counts: dict[str, int]
    evidence_missing_count: int
    agent_specs_total: int
    agent_specs_valid: int
    completed_tasks_total: int
    completed_guides_total: int
    items: tuple[PlanAuditItem, ...]

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


def discover_root(root: Path | None = None) -> Path:
    """Find the repository root without relying on an absolute workstation path."""

    if root is not None:
        return root.expanduser().resolve()
    candidates = [Path.cwd(), Path(__file__).resolve().parents[3]]
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.insert(0, Path(str(frozen_root)))
    for candidate in candidates:
        if _matrix_path(candidate) is not None:
            return candidate.resolve()
    return Path.cwd().resolve()


def _matrix_path(root: Path) -> Path | None:
    for relative in _MATRIX_LOCATIONS:
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def build_plan_audit(root: Path | None = None) -> PlanAuditReport:
    """Audit the plan matrix, neutral agent specs and completed task guides."""

    repo_root = discover_root(root)
    matrix_path = _matrix_path(repo_root)
    matrix_text = matrix_path.read_text(encoding="utf-8") if matrix_path is not None else ""
    items: list[PlanAuditItem] = []
    for raw_line in matrix_text.splitlines():
        match = _MATRIX_ROW.match(raw_line)
        if match is None:
            continue
        requirement_id, requirement, status, evidence, remaining = match.groups()
        missing = tuple(
            sorted(
                {
                    token
                    for token in _PATH_TOKEN.findall(evidence)
                    if _looks_like_path(token) and not _resolve_evidence(repo_root, token)
                }
            )
        )
        items.append(
            PlanAuditItem(
                requirement_id=int(requirement_id),
                requirement=requirement.strip(),
                status=status.strip(),
                missing_evidence=missing,
                remaining_gate=remaining.strip(),
            )
        )
    items.sort(key=lambda item: item.requirement_id)

    status_counts: dict[str, int] = {}
    for item in items:
        normalized = _status_family(item.status)
        status_counts[normalized] = status_counts.get(normalized, 0) + 1

    agent_specs = sorted((repo_root / "agents").glob("*/AGENT.md"))
    valid_specs = sum(
        1
        for path in agent_specs
        if _has_required_agent_sections(path.read_text(encoding="utf-8"))
    )
    completed_tasks = sorted(
        (repo_root / "spec" / "guides" / "completed").glob("HT-*.md")
    )
    # Completed task guides currently live under spec/guides/completed; keep the
    # task directory check intentionally tolerant for future task layouts.
    completed_guides = sorted((repo_root / "spec" / "guides" / "completed").glob("HT-*.md"))
    expected_task_ids = {
        f"HT-{index:03d}"
        for index in range(1, 14)
        if any(path.name.startswith(f"HT-{index:03d}") for path in completed_guides)
    }
    matrix_complete = len(items) == 43 and [
        item.requirement_id for item in items
    ] == list(range(43))
    evidence_missing = sum(len(item.missing_evidence) for item in items)
    local_contracts_valid = (
        matrix_complete
        and evidence_missing == 0
        and bool(agent_specs)
        and len(agent_specs) == valid_specs
        and len(expected_task_ids) >= 13
    )
    overall = "PASS" if local_contracts_valid else "INCOMPLETE"
    return PlanAuditReport(
        generated_at=datetime.now(UTC).isoformat(),
        root=str(repo_root),
        overall=overall,
        requirements_total=len(items),
        status_counts=status_counts,
        evidence_missing_count=evidence_missing,
        agent_specs_total=len(agent_specs),
        agent_specs_valid=valid_specs,
        completed_tasks_total=len(completed_tasks),
        completed_guides_total=len(completed_guides),
        items=tuple(items),
    )


def _status_family(status: str) -> str:
    normalized = status.upper().strip()
    if normalized.startswith("COMPLETE"):
        return "COMPLETE"
    if normalized.startswith("FOUNDATION"):
        return "FOUNDATION"
    if normalized.startswith("GATED"):
        return "GATED"
    return normalized or "UNKNOWN"


def _looks_like_path(token: str) -> bool:
    normalized = token.strip().strip(".,;:")
    return (
        "/" in normalized
        and not normalized.startswith("http")
        and not normalized.startswith("/api/")
        and not normalized.startswith("127.")
        and not normalized.startswith("uv ")
    )


def _resolve_evidence(root: Path, token: str) -> Path | None:
    normalized = token.strip().strip(".,;:")
    if "*" in normalized:
        if any(root.glob(normalized)) or any((root / "src" / "trading_bot").glob(normalized)):
            return root
        return None
    candidates = (
        root / normalized,
        root / "src" / "trading_bot" / normalized,
        root / "src" / normalized,
        root / "spec" / normalized,
        root / "private" / normalized,
        root / "private" / "spec" / normalized,
    )
    for candidate in candidates:
        if candidate.is_file() or candidate.is_dir():
            return candidate
    # Frozen native builds keep Python modules inside the PyInstaller archive,
    # so the source-relative path is not present on disk.  Resolve those same
    # evidence paths through the installed package without importing the code.
    package_token = normalized.removeprefix("src/trading_bot/")
    try:
        if normalized == "src/trading_bot":
            return root
        packaged = package_files("trading_bot").joinpath(package_token)
        if packaged.is_file() or packaged.is_dir():
            return root
    except (ModuleNotFoundError, TypeError):
        pass
    if package_token.endswith(".py"):
        module_name = "trading_bot." + package_token[:-3].replace("/", ".")
        try:
            if find_spec(module_name) is not None:
                return root
        except (ImportError, ModuleNotFoundError, ValueError):
            pass
    elif package_token.endswith("/"):
        module_name = "trading_bot." + package_token.rstrip("/").replace("/", ".")
        try:
            if find_spec(module_name) is not None:
                return root
        except (ImportError, ModuleNotFoundError, ValueError):
            pass
    return None


def _has_required_agent_sections(content: str) -> bool:
    return all(f"## {section}" in content for section in _REQUIRED_AGENT_SECTIONS)
