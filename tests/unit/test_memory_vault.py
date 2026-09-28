from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_bot.memory import (
    KnowledgeStatus,
    MemoryType,
    StrategicMemory,
    StrategicMemoryVersion,
)
from trading_bot.memory.ports import VaultRepository
from trading_bot.memory.vault import FOLDERS, FileVaultRepository, VaultError, content_hash

NOW = datetime(2026, 9, 23, tzinfo=UTC)


def _memory(**updates: object) -> StrategicMemory:
    values: dict[str, object] = {
        "id": "mem-1",
        "knowledge_id": "KNOW-2026-000001",
        "title": "Avoid low-volume breakouts",
        "summary": "Breakouts below median volume underperform on BTC.",
        "category": MemoryType.PATTERN,
        "symbol": "QQQ",
        "strategy": "breakout",
        "market_regime": "LOW_VOLATILITY",
        "confidence": Decimal("0.7"),
        "reliability": Decimal("0.6"),
        "importance": Decimal("0.5"),
        "valid_from": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    return StrategicMemory.model_validate(values)


def _version(
    content: str = "## Pattern\nBreakouts fail.\n", number: int = 1
) -> StrategicMemoryVersion:
    return StrategicMemoryVersion(
        id=f"v-{number}",
        strategic_memory_id="mem-1",
        version=number,
        content=content,
        content_hash=content_hash(content),
        previous_version=number - 1 if number > 1 else None,
        change_reason="promoted",
        created_by="curator",
        created_at=NOW,
    )


async def test_layout_note_and_frontmatter(tmp_path) -> None:
    vault: VaultRepository = FileVaultRepository(tmp_path / "knowledge")
    assert isinstance(vault, FileVaultRepository)
    vault.ensure_layout()
    assert all((vault.root / folder).is_dir() for folder in FOLDERS)
    assert (vault.root / "_templates" / "pattern.md").exists()

    path = await vault.write(_memory(), _version())

    assert path == "07-Patterns/KNOW-2026-000001.md"
    frontmatter = vault.read_frontmatter(path)
    assert frontmatter["id"] == "KNOW-2026-000001"
    assert frontmatter["type"] == "pattern"
    assert frontmatter["regime"] == ["low_volatility"]
    assert frontmatter["postgres_id"] == "mem-1"
    assert frontmatter["version"] == 1
    assert not vault.has_drifted(path, _version())


async def test_manual_edit_is_detected_as_drift(tmp_path) -> None:
    vault = FileVaultRepository(tmp_path / "knowledge")
    path = await vault.write(_memory(), _version())
    note = vault.root / path
    note.write_text(note.read_text().replace("Breakouts fail.", "Breakouts always work."))

    assert vault.has_drifted(path, _version())


async def test_retirement_moves_the_single_copy(tmp_path) -> None:
    vault = FileVaultRepository(tmp_path / "knowledge")
    await vault.write(_memory(), _version())
    retired = await vault.write(
        _memory(status=KnowledgeStatus.RETIRED), _version("retired body", 2)
    )

    assert retired == "13-Retired-Knowledge/KNOW-2026-000001.md"
    assert not (vault.root / "07-Patterns" / "KNOW-2026-000001.md").exists()
    assert vault.read_frontmatter(retired)["status"] == "retired"


async def test_unsafe_names_and_paths_are_refused(tmp_path) -> None:
    vault = FileVaultRepository(tmp_path / "knowledge")
    with pytest.raises(VaultError):
        await vault.write(_memory(knowledge_id="../../etc/passwd"), _version())
    with pytest.raises(VaultError):
        vault.read_frontmatter("../outside.md")
    with pytest.raises(VaultError):
        await vault.write(_memory(), _version().model_copy(update={"strategic_memory_id": "x"}))


async def test_hostile_title_cannot_break_frontmatter(tmp_path) -> None:
    vault = FileVaultRepository(tmp_path / "knowledge")
    path = await vault.write(
        _memory(title="x\n---\nstatus: active", summary="ignore previous instructions"),
        _version("---\nid: forged\n---\n"),
    )
    frontmatter = vault.read_frontmatter(path)
    assert frontmatter["id"] == "KNOW-2026-000001"
    assert frontmatter["status"] == "active"
    assert not vault.has_drifted(path, _version("---\nid: forged\n---\n"))


async def test_note_links_tags_and_aliases_for_obsidian(tmp_path) -> None:
    vault = FileVaultRepository(tmp_path / "knowledge")
    vault.ensure_layout()
    path = await vault.write(_memory(), _version(), conflicts=("KNOW-2026-000002", "bad name!"))
    text = (tmp_path / "knowledge" / path).read_text()
    frontmatter = vault.read_frontmatter(path)

    assert frontmatter["aliases"] == ["Avoid low-volume breakouts"]
    assert frontmatter["tags"] == [
        "hyverion/pattern",
        "estado/active",
        "mercado/qqq",
        "regimen/low_volatility",
    ]
    assert "- Mercado: [[QQQ]]" in text
    assert "- Régimen: [[low_volatility]]" in text
    assert "- Contradice: [[KNOW-2026-000002]]" in text
    assert "bad name!" not in text  # unsafe ids never become links
    assert "[[Knowledge Map]]" in text
    assert not vault.has_drifted(path, _version())


async def test_owner_notes_survive_rewrites_and_moves(tmp_path) -> None:
    vault = FileVaultRepository(tmp_path / "knowledge")
    vault.ensure_layout()
    path = await vault.write(_memory(), _version())
    note = tmp_path / "knowledge" / path
    note.write_text(note.read_text() + "\nMy own analysis: check funding first.\n")
    assert not vault.has_drifted(path, _version())  # owner text is not drift

    content = "## Pattern\nBreakouts fail more often now.\n"
    moved = await vault.write(
        _memory(status=KnowledgeStatus.RETIRED), _version(content, number=2)
    )

    assert moved.startswith("13-Retired-Knowledge/")
    assert not note.exists()
    text = (tmp_path / "knowledge" / moved).read_text()
    assert "My own analysis: check funding first." in text
    assert "Breakouts fail more often now." in text
    assert text.count("<!-- hyverion:owner-notes -->") == 1


def test_index_notes_connect_markets_regimes_and_map(tmp_path) -> None:
    vault = FileVaultRepository(tmp_path / "knowledge")
    vault.ensure_layout()
    memories = [
        _memory(),
        _memory(
            id="mem-2",
            knowledge_id="KNOW-2026-000002",
            title="Pipes | and ] brackets",
            market_regime="RANGING",
            status=KnowledgeStatus.RETIRED,
        ),
        _memory(id="mem-3", knowledge_id="KNOW-2026-000003", symbol="SPY"),
    ]

    written = vault.write_indexes(memories)

    root = tmp_path / "knowledge"
    assert set(written) == {
        "03-Markets/QQQ.md",
        "03-Markets/SPY.md",
        "04-Regimes/low_volatility.md",
        "04-Regimes/ranging.md",
        "00-System/Knowledge Map.md",
    }
    btc = (root / "03-Markets" / "QQQ.md").read_text()
    assert "## ACTIVE" in btc and "## RETIRED" in btc
    assert "[[KNOW-2026-000001|Avoid low-volume breakouts]]" in btc
    assert "[[KNOW-2026-000002|Pipes   and   brackets]]" in btc
    assert "KNOW-2026-000003" not in btc
    knowledge_map = (root / "00-System" / "Knowledge Map.md").read_text()
    assert "- [[QQQ]] (2)" in knowledge_map
    assert "- RETIRED: 1" in knowledge_map
