from __future__ import annotations

from typer.testing import CliRunner

from trading_bot.main import app

runner = CliRunner()


def test_cli_backtest_and_help() -> None:
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    assert "paper" in help_result.stdout

    result = runner.invoke(app, ["backtest", "--capital", "1000"])
    assert result.exit_code == 0
    assert '"trades"' in result.stdout

    walk_forward = runner.invoke(app, ["backtest", "--capital", "100", "--walk-forward"])
    assert walk_forward.exit_code == 0
    assert '"windows"' in walk_forward.stdout


def test_cli_paper_fixture_initializes_a_fresh_database(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'paper.db'}")
    result = runner.invoke(app, ["paper", "--fixture", "--capital", "100"])

    assert result.exit_code == 0
    # Five fixture prices are not a session: the QQQ strategies decline, no order.
    assert "NO_TRADE" in result.stdout


def test_cli_rejects_invalid_backtest_capital() -> None:
    result = runner.invoke(app, ["backtest", "--capital", "not-a-number"])
    assert result.exit_code != 0
    assert "capital must be a decimal number" in result.stderr


def test_cli_live_is_hard_blocked() -> None:
    result = runner.invoke(app, ["live", "--confirm-live"])
    assert result.exit_code == 2
    assert "LIVE BLOCKED" in result.stdout
    assert "no live broker adapter" in result.stdout


def test_cli_doctor_reports_the_paper_readiness_summary(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'doctor.db'}")
    result = runner.invoke(app, ["doctor"], env={"COLUMNS": "200"})

    assert result.exit_code == 0
    for row in ("Broker", "Environment", "Instrument", "Market", "Data", "Risk", "AI", "Live"):
        assert row in result.stdout
    assert "SIMULATOR" in result.stdout
    assert "PAPER" in result.stdout
    assert "DISABLED" in result.stdout
    # Without Alpaca keys data is reported unavailable, never faked.
    assert "market_data_credentials_missing" in result.stdout


def test_cli_backup_creates_verified_copy(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'source.db'}")
    destination = tmp_path / "backup.db"
    result = runner.invoke(app, ["backup", "--destination", str(destination)])

    assert result.exit_code == 0
    assert "BACKUP_VERIFIED" in result.stdout
    assert destination.is_file()


def test_cli_retention_defaults_to_safe_dry_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'source.db'}")
    result = runner.invoke(app, ["retention", "--purge"])

    assert result.exit_code == 0
    assert "RETENTION_DRY_RUN" in result.stdout
    assert "deleted_rows" in result.stdout


def test_cli_retention_exports_audit_manifest(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'source.db'}")
    destination = tmp_path / "audit.jsonl"
    result = runner.invoke(app, ["retention", "--export", str(destination)])

    assert result.exit_code == 0
    assert "RETENTION_EXPORT_VERIFIED" in result.stdout
    assert destination.is_file()
    assert destination.with_suffix(".jsonl.manifest.json").is_file()


def test_desktop_command_explains_how_to_build_when_app_is_missing(monkeypatch, tmp_path) -> None:
    import sys as _sys

    from typer.testing import CliRunner

    from trading_bot import main as cli

    monkeypatch.setattr(_sys, "platform", "darwin")
    monkeypatch.setattr(cli, "DESKTOP_APP_CANDIDATES", (tmp_path / "missing.app",))
    result = CliRunner().invoke(cli.app, ["desktop"])
    assert result.exit_code == 1
    assert "pnpm build:app" in result.output
