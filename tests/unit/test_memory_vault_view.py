from __future__ import annotations

from pathlib import Path

import pytest
from conftest import control_client
from fastapi.testclient import TestClient

from trading_bot.config import load_settings
from trading_bot.control_api import create_control_api
from trading_bot.memory.vault import OWNER_MARKER, VaultError
from trading_bot.memory.vault_view import read_note, vault_overview, write_owner_notes

MANAGED = (
    "---\nid: KNOW-1\ntype: lesson\nstatus: active\ntags: [hyverion/lesson]\n---\n\n"
    "# Spread alto falla\n\n> Resumen\n\n"
    "<!-- hyverion:content -->\nContenido gestionado\n<!-- /hyverion:content -->\n\n"
    "<!-- hyverion:links -->\n## Enlaces\n\n- Mercado: [[QQQ]]\n- Mapa: [[Knowledge Map]]\n"
    "<!-- /hyverion:links -->\n"
)


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "knowledge"
    (root / "06-Daily-Lessons").mkdir(parents=True)
    (root / "03-Markets").mkdir()
    (root / "00-System").mkdir()
    (root / "_templates").mkdir()
    hint = "_Escribe debajo: Hyverion conserva esta sección al regenerar la nota._"
    owner = f"{OWNER_MARKER}\n## Notas del owner\n\n{hint}\n"
    (root / "06-Daily-Lessons" / "KNOW-1.md").write_text(MANAGED + "\n" + owner, encoding="utf-8")
    (root / "03-Markets" / "QQQ.md").write_text(
        "---\ntags: [hyverion/index]\n---\n\n# Mercado: QQQ\n\n- [[KNOW-1|Spread alto]]\n",
        encoding="utf-8",
    )
    (root / "00-System" / "Knowledge Map.md").write_text(
        "---\ntags: [hyverion/map]\n---\n\n# Knowledge Map\n\n- [[QQQ]] (1)\n",
        encoding="utf-8",
    )
    (root / "_templates" / "lesson.md").write_text("---\nid: x\n---\n# T\n", encoding="utf-8")
    return root


def test_overview_builds_graph_between_existing_notes(tmp_path) -> None:
    overview = vault_overview(_vault(tmp_path))
    paths = {note["path"] for note in overview["notes"]}
    assert "_templates/lesson.md" not in paths
    kinds = {note["name"]: note["kind"] for note in overview["notes"]}
    assert kinds == {"KNOW-1": "knowledge", "QQQ": "index", "Knowledge Map": "map"}
    edge = {"source": "06-Daily-Lessons/KNOW-1.md", "target": "03-Markets/QQQ.md"}
    assert edge in overview["edges"]


def test_read_note_returns_body_links_and_backlinks(tmp_path) -> None:
    note = read_note(_vault(tmp_path), "06-Daily-Lessons/KNOW-1.md")
    assert note["title"] == "Spread alto falla"
    assert note["editable"] is True
    assert note["owner_notes"] == ""
    assert "hyverion:content" not in note["body"]
    assert "## Enlaces" not in note["body"]
    assert {"name": "QQQ", "path": "03-Markets/QQQ.md"} in note["links"]
    assert [b["path"] for b in note["backlinks"]] == ["03-Markets/QQQ.md"]


def test_owner_notes_never_touch_managed_content(tmp_path) -> None:
    root = _vault(tmp_path)
    note_path = "06-Daily-Lessons/KNOW-1.md"
    updated = write_owner_notes(root, note_path, "Confirmado a mano en 3 sesiones.")
    assert updated["owner_notes"] == "Confirmado a mano en 3 sesiones."
    text = (root / "06-Daily-Lessons" / "KNOW-1.md").read_text(encoding="utf-8")
    assert text.startswith(MANAGED)
    write_owner_notes(root, "06-Daily-Lessons/KNOW-1.md", "")
    assert read_note(root, "06-Daily-Lessons/KNOW-1.md")["owner_notes"] == ""


@pytest.mark.parametrize(
    ("path", "notes"),
    [
        ("../outside.md", "x"),
        ("_templates/lesson.md", "x"),
        ("03-Markets/QQQ.md", "x"),  # index notes have no owner section
        ("06-Daily-Lessons/KNOW-1.md", f"inject {OWNER_MARKER}"),
        ("06-Daily-Lessons/KNOW-1.md", "<!-- hyverion:content -->x"),
        ("06-Daily-Lessons/KNOW-1.md", "x" * 20_001),
    ],
)
def test_owner_notes_reject_unsafe_writes(tmp_path, path, notes) -> None:
    with pytest.raises(VaultError):
        write_owner_notes(_vault(tmp_path), path, notes)


def test_vault_endpoints_require_token_and_edit_owner_notes(tmp_path) -> None:
    root = _vault(tmp_path)
    settings = load_settings()
    settings = settings.model_copy(
        update={
            "public": settings.public.model_copy(
                update={
                    "database": settings.public.database.model_copy(
                        update={"url": f"sqlite+aiosqlite:///{tmp_path / 'v.db'}"}
                    ),
                    "memory": settings.public.memory.model_copy(update={"vault_path": root}),
                }
            )
        }
    )
    app = create_control_api(settings)
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/v1/memory/vault").status_code == 401
    with control_client(app) as client:
        assert len(client.get("/api/v1/memory/vault").json()["notes"]) == 3
        url = "/api/v1/memory/vault/note"
        assert client.get(url, params={"path": "06-Daily-Lessons/KNOW-1.md"}).status_code == 200
        assert client.get(url, params={"path": "../x.md"}).status_code == 404
        saved = client.post(
            "/api/v1/memory/vault/note/owner",
            json={"path": "06-Daily-Lessons/KNOW-1.md", "notes": "Revisar en régimen lateral."},
        )
        assert saved.status_code == 200
        assert saved.json()["owner_notes"] == "Revisar en régimen lateral."
        rejected = client.post(
            "/api/v1/memory/vault/note/owner",
            json={"path": "06-Daily-Lessons/KNOW-1.md", "notes": OWNER_MARKER},
        )
        assert rejected.status_code == 422


def test_notes_are_never_read_through_a_symlink(tmp_path) -> None:
    root = _vault(tmp_path)
    secret = tmp_path / "outside.md"
    secret.write_text("---\nid: x\n---\n# fuera de la bóveda\n", encoding="utf-8")
    link = root / "06-Daily-Lessons" / "KNOW-2.md"
    link.symlink_to(secret)
    with pytest.raises(VaultError):
        read_note(root, "06-Daily-Lessons/KNOW-2.md")
