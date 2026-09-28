"""Obsidian-compatible Markdown export of strategic memory.

The database is authoritative. The vault is a human-readable, versioned view:
one note per knowledge id with validated YAML frontmatter. Notes are written
atomically, never outside the vault root, and can be checked for drift against
the stored version hash. Obsidian is optional; notes are plain Markdown.

Each note has three parts: the managed content block (authoritative, checked for
drift), a generated links block (wikilinks to market, regime, type and
conflicting knowledge for Obsidian's graph) and an owner section after
``OWNER_MARKER`` that is preserved verbatim on every rewrite. Index notes for
markets, regimes and a knowledge map are regenerated from the database.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from trading_bot.memory.models import (
    KnowledgeStatus,
    MemoryType,
    StrategicMemory,
    StrategicMemoryVersion,
)

FOLDERS: tuple[str, ...] = (
    "00-System",
    "01-Agents",
    "02-Strategies",
    "03-Markets",
    "04-Regimes",
    "05-Trade-Reviews",
    "06-Daily-Lessons",
    "07-Patterns",
    "08-Failures",
    "09-Agent-Learnings",
    "10-Experiments",
    "11-Change-Proposals",
    "12-Research",
    "13-Retired-Knowledge",
    "_templates",
)

_CATEGORY_FOLDER: dict[MemoryType, str] = {
    MemoryType.OBSERVATION: "06-Daily-Lessons",
    MemoryType.LESSON: "06-Daily-Lessons",
    MemoryType.HYPOTHESIS: "07-Patterns",
    MemoryType.PATTERN: "07-Patterns",
    MemoryType.CONFIRMED_KNOWLEDGE: "07-Patterns",
    MemoryType.RULE: "00-System",
    MemoryType.FAILURE: "08-Failures",
    MemoryType.RESEARCH: "12-Research",
    MemoryType.RETIRED_KNOWLEDGE: "13-Retired-Knowledge",
}

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
OWNER_MARKER = "<!-- hyverion:owner-notes -->"
_OWNER_HEADER = (
    f"{OWNER_MARKER}\n## Notas del owner\n\n"
    "_Escribe debajo: Hyverion conserva esta sección al regenerar la nota._\n"
)
_GENERATED = "_Nota generada por Hyverion desde la base de datos; se regenera en cada ciclo._"
MAP_NOTE = "00-System/Knowledge Map.md"

_TEMPLATE_SECTIONS = (
    "Pattern",
    "Evidence",
    "Counter evidence",
    "Conditions",
    "Invalidations",
    "Historical Performance",
    "Last validation",
)


class VaultError(ValueError):
    """A note cannot be written or read safely."""


class FileVaultRepository:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @property
    def root(self) -> Path:
        return self._root

    def ensure_layout(self) -> None:
        """Create the folder structure and templates; existing notes are untouched."""

        for folder in FOLDERS:
            (self._root / folder).mkdir(parents=True, exist_ok=True)
        for name in ("pattern", "lesson", "failure"):
            template = self._root / "_templates" / f"{name}.md"
            if not template.exists():
                _atomic_write(template, _template(name))

    async def write(
        self,
        memory: StrategicMemory,
        version: StrategicMemoryVersion,
        *,
        conflicts: Sequence[str] = (),
    ) -> str:
        """Write the note for ``memory`` at ``version``; returns its vault-relative path.

        ``conflicts`` are knowledge ids linked from the note. The owner's section of
        any previous copy (even one in another folder) is carried over unchanged.
        """

        if version.strategic_memory_id != memory.id:
            raise VaultError("version does not belong to this memory")
        if not _SAFE_NAME.match(memory.knowledge_id):
            raise VaultError("knowledge_id is not a safe note name")
        target = self._note_path(memory)
        owner = _OWNER_HEADER
        # A status change can move a note (e.g. to Retired); keep exactly one copy.
        for existing in self._root.glob(f"*/{memory.knowledge_id}.md"):
            preserved = _owner_section(existing.read_text(encoding="utf-8"))
            if preserved is not None:
                owner = preserved
            if existing.resolve() != target:
                existing.unlink()
        _atomic_write(target, render_note(memory, version, conflicts=conflicts) + "\n" + owner)
        return target.relative_to(self._root).as_posix()

    def write_indexes(self, memories: Sequence[StrategicMemory]) -> list[str]:
        """Regenerate market, regime and map notes so Obsidian's graph connects knowledge."""

        written: list[str] = []
        by_market: dict[str, list[StrategicMemory]] = {}
        by_regime: dict[str, list[StrategicMemory]] = {}
        for memory in memories:
            if memory.symbol:
                by_market.setdefault(_market_note(memory.symbol), []).append(memory)
            if memory.market_regime:
                by_regime.setdefault(_regime_note(memory.market_regime), []).append(memory)
        for folder, groups, label in (
            ("03-Markets", by_market, "Mercado"),
            ("04-Regimes", by_regime, "Régimen"),
        ):
            for name, group in sorted(groups.items()):
                if not _SAFE_NAME.match(name):
                    continue
                path = self._inside_root(f"{folder}/{name}.md")
                _atomic_write(path, _index_note(f"{label}: {name}", group))
                written.append(path.relative_to(self._root).as_posix())
        lines = [
            "---",
            "tags: [hyverion/map]",
            "---",
            "",
            "# Knowledge Map",
            "",
            _GENERATED,
            "",
            "## Mercados",
            *[f"- [[{name}]] ({len(group)})" for name, group in sorted(by_market.items())],
            "",
            "## Regímenes",
            *[f"- [[{name}]] ({len(group)})" for name, group in sorted(by_regime.items())],
            "",
            "## Estado",
            *[
                f"- {status.value}: {sum(1 for m in memories if m.status is status)}"
                for status in KnowledgeStatus
            ],
            "",
        ]
        path = self._inside_root(MAP_NOTE)
        _atomic_write(path, "\n".join(lines))
        written.append(MAP_NOTE)
        return written

    def read_frontmatter(self, relative_path: str) -> dict[str, Any]:
        path = self._inside_root(relative_path)
        text = path.read_text(encoding="utf-8")
        return _parse_frontmatter(text)

    def has_drifted(self, relative_path: str, version: StrategicMemoryVersion) -> bool:
        """True when the note body no longer matches the authoritative version."""

        path = self._inside_root(relative_path)
        text = path.read_text(encoding="utf-8")
        frontmatter = _parse_frontmatter(text)
        body = text.split("\n---\n", 1)[1] if "\n---\n" in text else ""
        return (
            frontmatter.get("content_hash") != version.content_hash
            or frontmatter.get("version") != version.version
            or _body_content(body) != version.content
        )

    def note_path(self, memory: StrategicMemory) -> str:
        """Vault-relative path where ``memory``'s note belongs."""

        return self._note_path(memory).relative_to(self._root).as_posix()

    def _note_path(self, memory: StrategicMemory) -> Path:
        folder = (
            "13-Retired-Knowledge"
            if memory.status is KnowledgeStatus.RETIRED
            else _CATEGORY_FOLDER[memory.category]
        )
        return self._inside_root(f"{folder}/{memory.knowledge_id}.md")

    def _inside_root(self, relative_path: str) -> Path:
        path = (self._root / relative_path).resolve()
        if self._root not in path.parents:
            raise VaultError("path escapes the vault root")
        return path


def render_note(
    memory: StrategicMemory,
    version: StrategicMemoryVersion,
    *,
    conflicts: Sequence[str] = (),
) -> str:
    """The managed and generated parts of a note (the owner section is appended by write)."""

    frontmatter = {
        "id": memory.knowledge_id,
        "type": memory.category.value.lower(),
        "status": memory.status.value.lower(),
        "symbol": memory.symbol,
        "regime": [memory.market_regime.lower()] if memory.market_regime else [],
        "strategies": [memory.strategy] if memory.strategy else [],
        "confidence": float(memory.confidence),
        "reliability": float(memory.reliability),
        "importance": float(memory.importance),
        "valid_from": memory.valid_from.isoformat(),
        "valid_until": memory.valid_until.isoformat() if memory.valid_until else None,
        "created_at": memory.created_at.isoformat(),
        "updated_at": memory.updated_at.isoformat(),
        "postgres_id": memory.id,
        "version": version.version,
        "content_hash": version.content_hash,
        "aliases": [memory.title.replace("\n", " ").strip()],
        "tags": _tags(memory),
    }
    header = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    title = memory.title.replace("\n", " ").strip()
    summary = memory.summary.replace("\n", " ").strip()
    links = [f"- Tipo: {memory.category.value}", f"- Estado: {memory.status.value}"]
    if memory.symbol:
        links.append(f"- Mercado: [[{_market_note(memory.symbol)}]]")
    if memory.market_regime:
        links.append(f"- Régimen: [[{_regime_note(memory.market_regime)}]]")
    links += [
        f"- Contradice: [[{knowledge_id}]]"
        for knowledge_id in sorted(set(conflicts))
        if _SAFE_NAME.match(knowledge_id)
    ]
    links.append("- Mapa: [[Knowledge Map]]")
    return (
        f"---\n{header}\n---\n\n# {title}\n\n> {summary}\n\n"
        f"<!-- hyverion:content -->\n{version.content}\n<!-- /hyverion:content -->\n\n"
        "<!-- hyverion:links -->\n## Enlaces\n\n"
        + "\n".join(links)
        + "\n<!-- /hyverion:links -->\n"
    )


def _tags(memory: StrategicMemory) -> list[str]:
    tags = [f"hyverion/{memory.category.value.lower()}", f"estado/{memory.status.value.lower()}"]
    if memory.symbol:
        tags.append(f"mercado/{_market_note(memory.symbol).lower()}")
    if memory.market_regime:
        tags.append(f"regimen/{_regime_note(memory.market_regime)}")
    return tags


def _market_note(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", symbol.strip().upper())[:64]


def _regime_note(regime: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", regime.strip().lower())[:64]


def _index_note(title: str, memories: Sequence[StrategicMemory]) -> str:
    lines = ["---", "tags: [hyverion/index]", "---", "", f"# {title}", "", _GENERATED, ""]
    for status in KnowledgeStatus:
        group = sorted(
            (memory for memory in memories if memory.status is status),
            key=lambda memory: (-memory.reliability, memory.knowledge_id),
        )
        if not group:
            continue
        lines += [f"## {status.value}", ""]
        lines += [
            f"- [[{memory.knowledge_id}|{_link_label(memory.title)}]] · "
            f"{memory.category.value} · fiabilidad {memory.reliability}"
            for memory in group
        ]
        lines.append("")
    lines.append("[[Knowledge Map]]")
    return "\n".join(lines) + "\n"


def _link_label(title: str) -> str:
    # "|" and "]" would break a wikilink alias.
    return re.sub(r"[\[\]|#^]", " ", title.replace("\n", " ")).strip()[:120]


def _owner_section(text: str) -> str | None:
    index = text.find(OWNER_MARKER)
    return text[index:] if index >= 0 else None


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _body_content(body: str) -> str:
    start, end = "<!-- hyverion:content -->\n", "\n<!-- /hyverion:content -->"
    if start not in body or end not in body:
        return ""
    return body.split(start, 1)[1].rsplit(end, 1)[0]


def _parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise VaultError("note has no frontmatter")
    raw = text[4:].split("\n---\n", 1)[0]
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise VaultError("frontmatter is not a mapping")
    return value


def _template(name: str) -> str:
    sections = "\n\n".join(f"## {section}\n" for section in _TEMPLATE_SECTIONS)
    return (
        "---\nid: KNOW-YYYY-NNNNNN\n"
        f"type: {name}\nstatus: active\nsymbol:\nregime: []\nstrategies: []\n"
        "confidence: 0\nreliability: 0\nsample_size: 0\nsources: []\nversion: 1\n---\n\n"
        f"# Title\n\n{sections}"
    )


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
