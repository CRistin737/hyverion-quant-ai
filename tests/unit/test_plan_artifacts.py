from __future__ import annotations

from pathlib import Path

import pytest

from trading_bot.monitoring.plan_audit import build_plan_audit

ROOT = Path(__file__).resolve().parents[2]
# Internal plan material lives in private/, which the public export omits.
PRIVATE_SPEC = ROOT / "private" / "spec"
requires_private = pytest.mark.skipif(
    not PRIVATE_SPEC.is_dir(), reason="internal plan docs are not part of the public export"
)


@requires_private
def test_full_plan_audit_covers_every_numbered_requirement() -> None:
    matrix = (PRIVATE_SPEC / "PLAN_COVERAGE.md").read_text(encoding="utf-8")
    audit = (PRIVATE_SPEC / "FULL_PLAN_AUDIT.md").read_text(encoding="utf-8")
    for requirement in range(43):
        marker = f"| {requirement} |"
        assert marker in matrix
        assert marker in audit


@requires_private
def test_machine_plan_audit_verifies_matrix_specs_and_guides() -> None:
    report = build_plan_audit(ROOT)
    assert report.overall == "PASS"
    assert report.requirements_total == 43
    assert report.evidence_missing_count == 0
    assert report.agent_specs_total >= 11
    assert report.agent_specs_valid == report.agent_specs_total
    assert report.completed_guides_total >= 13


@requires_private
def test_completed_task_ledger_has_one_guide_for_each_completed_unit() -> None:
    ledger = (PRIVATE_SPEC / "tasks" / "COMPLETED_TASKS.md").read_text(encoding="utf-8")
    completed_dir = ROOT / "spec" / "guides" / "completed"
    for task_id in range(1, 14):
        prefix = f"HT-{task_id:03d}"
        assert prefix in ledger
        assert any(path.name.startswith(prefix) for path in completed_dir.glob("*.md"))


@requires_private
def test_provider_subscription_tasks_cover_all_supported_accounts() -> None:
    provider_dir = PRIVATE_SPEC / "tasks" / "providers"
    acceptance = (PRIVATE_SPEC / "tasks" / "PROVIDER_SUBSCRIPTION_ACCEPTANCE.md").read_text(
        encoding="utf-8"
    )
    assert "HT-006A" in acceptance
    assert "one saved primary subscription" in acceptance
    assert "ModelRouter" in acceptance
    assert "NO_TRADE" in acceptance
    assert (ROOT / "spec" / "guides" / "completed" / "HT-006A-provider-accounts.md").is_file()
    control_api = (ROOT / "src" / "trading_bot" / "control_api.py").read_text(encoding="utf-8")
    queries = (ROOT / "app" / "src" / "api" / "queries.ts").read_text(encoding="utf-8")
    models = (ROOT / "app" / "src" / "screens" / "inteligencia" / "Modelos.tsx").read_text(
        encoding="utf-8"
    )
    assert "/api/v1/providers/chain/probe" in control_api
    assert "/api/v1/providers/chain/probe" in queries
    assert "Probar la cadena" in models
    for provider in ("CLAUDE", "CODEX", "GROK", "GEMINI"):
        content = (provider_dir / f"{provider}_SUBSCRIPTION.md").read_text(encoding="utf-8")
        assert "subscription" in content.lower()
        assert "NO_TRADE" in content
        assert "one primary" in content.lower()


def test_agent_specs_have_the_required_neutral_sections() -> None:
    required = (
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
    specs = sorted((ROOT / "agents").glob("*/AGENT.md"))
    assert len(specs) >= 11
    for spec in specs:
        content = spec.read_text(encoding="utf-8")
        for section in required:
            assert f"## {section}" in content, f"{section} missing from {spec}"


@requires_private
def test_full_audit_records_safe_runtime_defaults() -> None:
    audit = (PRIVATE_SPEC / "FULL_PLAN_AUDIT.md").read_text(encoding="utf-8")
    assert "`paper`, capital `100`, `live_trading=false`" in audit
    assert "`single_primary=true`" in audit
    assert "not yet LIVE-ready" in audit


def test_observability_guide_and_export_contract_are_indexed() -> None:
    guide = ROOT / "spec" / "guides" / "observability.md"
    index = (ROOT / "spec" / "guides" / "README.md").read_text(encoding="utf-8")
    api = (ROOT / "src" / "trading_bot" / "control_api.py").read_text(encoding="utf-8")
    assert guide.is_file()
    assert "observability.md" in index
    assert "/api/v1/observability/metrics" in api
    assert "prometheus" in api.lower()
    assert "/api/v1/readiness" in api
    assert "build_readiness_report" in api


@requires_private
def test_readiness_guide_and_contract_are_indexed() -> None:
    guide = ROOT / "spec" / "guides" / "completed" / "HT-013-readiness.md"
    module = ROOT / "src" / "trading_bot" / "monitoring" / "readiness.py"
    index = (ROOT / "spec" / "guides" / "completed" / "README.md").read_text(
        encoding="utf-8"
    )
    ledger = (PRIVATE_SPEC / "tasks" / "COMPLETED_TASKS.md").read_text(encoding="utf-8")
    assert guide.is_file()
    assert module.is_file()
    assert "HT-013" in index
    assert "live_authorized" in guide.read_text(encoding="utf-8")
    assert "PAPER gate" in ledger


def test_visual_regression_matrix_is_reproducible_and_indexed() -> None:
    guide = ROOT / "spec" / "guides" / "visual-regression.md"
    config = (ROOT / "app" / "playwright.config.ts").read_text(encoding="utf-8")
    spec = (ROOT / "app" / "tests" / "visual" / "routes.spec.ts").read_text(encoding="utf-8")
    index = (ROOT / "spec" / "guides" / "README.md").read_text(encoding="utf-8")
    assert guide.is_file()
    assert "visual-regression.md" in index
    for viewport in ("1024x700", "1280x800", "1440x900", "1920x1080"):
        assert viewport in config
    # Deterministic: fixture data and a frozen clock; the mode badge is always asserted.
    assert "fixture=demo" in spec
    assert "clock.install" in spec
    assert "SIMULACIÓN" in spec


def test_backup_guide_and_cli_contract_are_indexed() -> None:
    guide = ROOT / "spec" / "guides" / "backup-and-restore.md"
    database_backup = ROOT / "src" / "trading_bot" / "db" / "backup.py"
    main = (ROOT / "src" / "trading_bot" / "main.py").read_text(encoding="utf-8")
    index = (ROOT / "spec" / "guides" / "README.md").read_text(encoding="utf-8")
    assert guide.is_file()
    assert database_backup.is_file()
    assert "backup" in main
    assert "backup-and-restore.md" in index


def test_retention_guide_and_cli_contract_are_indexed() -> None:
    guide = ROOT / "spec" / "guides" / "retention.md"
    module = ROOT / "src" / "trading_bot" / "monitoring" / "retention.py"
    main = (ROOT / "src" / "trading_bot" / "main.py").read_text(encoding="utf-8")
    index = (ROOT / "spec" / "guides" / "README.md").read_text(encoding="utf-8")
    assert guide.is_file()
    assert module.is_file()
    assert "retention" in main
    assert "retention.md" in index


def test_operational_alert_projection_is_indexed() -> None:
    module = ROOT / "src" / "trading_bot" / "monitoring" / "alerts.py"
    control_api = (ROOT / "src" / "trading_bot" / "control_api.py").read_text(encoding="utf-8")
    assert module.is_file()
    assert "project_operational_alerts" in control_api


def test_doc_screenshot_gallery_matches_verified_baselines() -> None:
    """The docs gallery is a copy of the verified visual baselines, never stale."""

    from pathlib import Path

    root = Path(__file__).parents[2]
    gallery = root / "docs" / "ui" / "capturas"
    baselines = root / "app" / "tests" / "visual" / "__screenshots__"
    pairs = {
        "inicio-claro.png": "inicio-1440x900-light.png",
        "onboarding.png": "onboarding-1440x900.png",
    }
    for image in sorted(gallery.glob("*.png")):
        source = baselines / pairs.get(image.name, image.name.replace(".png", "-1440x900.png"))
        assert source.exists(), f"no baseline for {image.name}"
        assert image.read_bytes() == source.read_bytes(), (
            f"{image.name} is stale; run scripts/update_doc_screenshots.sh"
        )
    readme = (gallery / "README.md").read_text()
    for image in gallery.glob("*.png"):
        assert f"({image.name})" in readme, f"{image.name} missing from the gallery README"
