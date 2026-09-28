from __future__ import annotations

import hashlib
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from trading_bot.schemas.observability import AgentDescriptor

_HEADING = re.compile(r"^##\s+([A-Z][A-Z _/-]*)\s*$", re.MULTILINE)


class AgentRegistry:
    """Loads neutral AGENT.md specifications without granting them execution access."""

    def __init__(
        self, root: Path | str = "agents", overrides: Mapping[str, str] | None = None
    ) -> None:
        # Human-approved AGENT.md texts (component_versions) replace the bundled
        # file for that agent; the bundle itself is never edited.
        self._overrides = dict(overrides or {})
        requested = Path(root)
        if requested.is_absolute() or requested.exists():
            self._root = requested
        else:
            frozen_root = getattr(sys, "_MEIPASS", None)
            candidates = []
            if frozen_root:
                candidates.append(Path(frozen_root) / requested)
            candidates.extend(
                (Path.cwd() / requested, Path(__file__).resolve().parents[3] / requested)
            )
            self._root = next(
                (candidate for candidate in candidates if candidate.exists()), requested
            )

    def descriptors(self) -> tuple[AgentDescriptor, ...]:
        descriptors: list[AgentDescriptor] = []
        for path in sorted(self._root.glob("*/AGENT.md")):
            descriptors.append(self._parse(path))
        return tuple(descriptors)

    def descriptor(self, agent_id: str) -> AgentDescriptor:
        """Return one descriptor or fail closed when it is not registered."""

        for descriptor in self.descriptors():
            if descriptor.agent_id == agent_id:
                return descriptor
        raise KeyError(f"agent is not registered: {agent_id}")

    def specification(self, agent_id: str) -> tuple[AgentDescriptor, str]:
        """Load the exact neutral spec that is hashed and shown in the UI."""

        descriptor = self.descriptor(agent_id)
        return descriptor, self._content(Path(descriptor.spec_path))

    def _content(self, path: Path) -> str:
        override = self._overrides.get(path.parent.name)
        return override if override is not None else path.read_text(encoding="utf-8")

    def _parse(self, path: Path) -> AgentDescriptor:
        content = self._content(path)
        sections = self._sections(content)
        agent_id = path.parent.name
        return AgentDescriptor(
            agent_id=agent_id,
            role=sections.get("ROLE", agent_id),
            objective=sections.get("OBJECTIVE", "No objective declared."),
            version=sections.get("VERSION", "0.0.0"),
            spec_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            spec_path=str(path),
            status="IDLE",
            last_run_at=None,
        )

    @staticmethod
    def _sections(content: str) -> dict[str, str]:
        matches = list(_HEADING.finditer(content))
        sections: dict[str, str] = {}
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            value = " ".join(
                line.strip() for line in content[match.end() : end].splitlines()
            ).strip()
            section_name = match.group(1).strip()
            if value and section_name in {"ROLE", "OBJECTIVE", "VERSION"}:
                sections[section_name] = value
        return sections

    def last_run_map(self, rows: list[dict[str, object]]) -> dict[str, tuple[str, datetime]]:
        latest: dict[str, tuple[str, datetime]] = {}
        for row in rows:
            agent_id = str(row.get("agent_id") or "")
            if not agent_id:
                continue
            raw = row.get("created_at")
            if not isinstance(raw, datetime):
                continue
            timestamp = raw.astimezone(UTC)
            previous = latest.get(agent_id)
            if previous is None or timestamp > previous[1]:
                latest[agent_id] = (str(row.get("status") or "UNKNOWN"), timestamp)
        return latest
