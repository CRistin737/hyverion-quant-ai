# PyInstaller spec for the headless core sidecar (no GUI toolkit bundled).
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
hiddenimports = [
    name for name in collect_submodules("trading_bot") if not name.startswith("trading_bot.desktop")
] + ["aiosqlite", "aiosqlite.core"] + collect_submodules("exchange_calendars")

analysis = Analysis(
    [str(root / "packaging" / "core_entry.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[
        (str(root / "config"), "config"),
        (str(root / "agents"), "agents"),
        (str(root / "spec"), "spec"),
        (str(root / "docs"), "docs"),
        (str(root / "AGENTS.md"), "."),
        (str(root / ".env.example"), "."),
        # NYSE calendar and IANA zones for the market clock inside the bundle.
        *collect_data_files("exchange_calendars"),
        *collect_data_files("tzdata"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PySide6", "shiboken6", "trading_bot.desktop"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)
EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="hyverion-core",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
