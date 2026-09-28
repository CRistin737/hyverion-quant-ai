"""Read-mostly view of the Knowledge Vault for the desktop app.

The database stays authoritative and ``FileVaultRepository`` keeps writing the
managed notes. This module only *reads* the vault (notes, wikilinks, backlinks,
graph) and lets the owner replace the text of their own section — the part of
a note after ``OWNER_MARKER`` that Hyverion preserves on every regeneration.
Managed content and generated links are never modified here.

Owner notes are human annotations. They are not read by agents or retrieval,
so they cannot become instructions to the AI pipeline.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trading_bot.memory.vault import OWNER_MARKER, VaultError, _atomic_write, _parse_frontmatter

MAX_OWNER_NOTES_CHARS = 20_000
_OWNER_HINT = "_Escribe debajo: Hyverion conserva esta sección al regenerar la nota._"
_OWNER_HEADER = f"{OWNER_MARKER}\n## Notas del owner\n\n{_OWNER_HINT}\n"
_WIKILINK = re.compile(r"\[\[([^\]|#^]+)(?:[#^][^\]|]*)?(?:\|([^\]]*))?\]\]")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# The generated links block is shown as structured links/backlinks in the UI.
_LINKS_BLOCK = re.compile(r"<!-- hyverion:links -->.*?<!-- /hyverion:links -->", re.DOTALL)


@dataclass(frozen=True)
class _Note:
    path: str
    name: str
    text: str


def _read_nofollow(path: Path) -> str:
    """Read a note without following a symlink swapped in after path validation."""

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise VaultError("note cannot be opened safely") from exc
    with os.fdopen(fd, encoding="utf-8") as handle:
        return handle.read()


def _root(root: Path) -> Path:
    return root.resolve()


def _inside(root: Path, relative_path: str) -> Path:
    if not relative_path.endswith(".md") or relative_path.startswith("_templates/"):
        raise VaultError("not a vault note")
    path = (root / relative_path).resolve()
    if root not in path.parents:
        raise VaultError("path escapes the vault root")
    return path


def _notes(root: Path) -> list[_Note]:
    if not root.exists():
        return []
    notes: list[_Note] = []
    for path in sorted(root.rglob("*.md")):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("_templates/") or path.name.startswith(".tmp-"):
            continue
        notes.append(_Note(relative, path.stem, path.read_text(encoding="utf-8")))
    return notes


def _split(text: str) -> tuple[dict[str, Any], str]:
    try:
        frontmatter = _parse_frontmatter(text)
        body = text[4:].split("\n---\n", 1)[1]
    except VaultError:
        frontmatter, body = {}, text
    return frontmatter, body


def _title(name: str, body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return name


def _links(text: str) -> list[str]:
    seen: list[str] = []
    for match in _WIKILINK.finditer(text):
        target = match.group(1).strip()
        if target and target not in seen:
            seen.append(target)
    return seen


def _kind(path: str, frontmatter: dict[str, Any]) -> str:
    tags = frontmatter.get("tags") or []
    if path.startswith("00-System/") or "hyverion/map" in tags:
        return "map"
    if "hyverion/index" in tags:
        return "index"
    return "knowledge"


def vault_overview(root: Path) -> dict[str, Any]:
    """All notes plus the wikilink graph (edges only between existing notes)."""

    root = _root(root)
    notes = _notes(root)
    by_name = {note.name: note.path for note in notes}
    items: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    for note in notes:
        frontmatter, body = _split(note.text)
        items.append(
            {
                "path": note.path,
                "name": note.name,
                "title": _title(note.name, body),
                "folder": note.path.split("/", 1)[0] if "/" in note.path else "",
                "kind": _kind(note.path, frontmatter),
                "status": frontmatter.get("status"),
                "type": frontmatter.get("type"),
                "has_owner_notes": bool(_owner_text(note.text)),
            }
        )
        for target in _links(note.text):
            if target in by_name and by_name[target] != note.path:
                edges.append({"source": note.path, "target": by_name[target]})
    return {"root_exists": root.exists(), "notes": items, "edges": edges}


def _owner_text(text: str) -> str:
    index = text.find(OWNER_MARKER)
    if index < 0:
        return ""
    section = text[index + len(OWNER_MARKER) :]
    lines = [
        line
        for line in section.splitlines()
        if line.strip() not in {"## Notas del owner", _OWNER_HINT}
    ]
    return "\n".join(lines).strip()


def read_note(root: Path, relative_path: str) -> dict[str, Any]:
    """One note: frontmatter, managed body (without owner section), owner notes, links."""

    root = _root(root)
    path = _inside(root, relative_path)
    if not path.is_file():
        raise VaultError("note not found")
    text = _read_nofollow(path)
    frontmatter, body = _split(text)
    index = body.find(OWNER_MARKER)
    managed = body[:index] if index >= 0 else body
    notes = _notes(root)
    by_name = {note.name: note.path for note in notes}
    name = path.stem
    backlinks = [
        {"path": note.path, "title": _title(note.name, _split(note.text)[1])}
        for note in notes
        if note.path != relative_path and name in _links(note.text)
    ]
    return {
        "path": relative_path,
        "name": name,
        "title": _title(name, managed),
        "frontmatter": frontmatter,
        "body": _COMMENT.sub("", _LINKS_BLOCK.sub("", managed)).strip(),
        "owner_notes": _owner_text(text),
        "editable": index >= 0,
        "links": [{"name": target, "path": by_name.get(target)} for target in _links(managed)],
        "backlinks": backlinks,
    }


def write_owner_notes(root: Path, relative_path: str, notes: str) -> dict[str, Any]:
    """Replace only the owner's section of a knowledge note."""

    root = _root(root)
    path = _inside(root, relative_path)
    if not path.is_file():
        raise VaultError("note not found")
    if len(notes) > MAX_OWNER_NOTES_CHARS:
        raise VaultError("owner notes are too long")
    if OWNER_MARKER in notes or "<!-- hyverion:" in notes:
        raise VaultError("owner notes cannot contain Hyverion markers")
    # _atomic_write replaces the path itself (never writes through a symlink).
    text = _read_nofollow(path)
    index = text.find(OWNER_MARKER)
    if index < 0:
        raise VaultError("this note has no owner section")
    cleaned = notes.replace("\r\n", "\n").strip()
    _atomic_write(path, text[:index] + _OWNER_HEADER + (f"\n{cleaned}\n" if cleaned else ""))
    return read_note(root, relative_path)
