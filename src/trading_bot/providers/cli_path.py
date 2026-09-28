"""Find the AI subscription CLIs (claude, codex, grok, gemini) outside a shell.

Apps opened from the Finder and launchd agents start with a bare PATH
(``/usr/bin:/bin:/usr/sbin:/sbin``), so ``shutil.which("claude")`` fails even
when the CLI is installed. The usual per-user install locations are appended
once at startup; nothing is downloaded or executed here.
"""

from __future__ import annotations

import os
from pathlib import Path


def cli_directories(home: Path | None = None) -> list[str]:
    root = home or Path.home()
    candidates = [
        root / ".local" / "bin",  # Claude Code native installer
        root / ".claude" / "local",
        root / ".grok" / "bin",
        root / ".npm-global" / "bin",
        root / ".bun" / "bin",
        root / ".volta" / "bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]
    # nvm: newest Node version first.
    nvm = sorted((root / ".nvm" / "versions" / "node").glob("*/bin"), reverse=True)
    return [str(path) for path in (*candidates, *nvm) if path.is_dir()]


def ensure_cli_path(environ: dict[str, str] | os._Environ[str] | None = None) -> str:
    """Append the per-user CLI directories to PATH (idempotent). Returns the PATH."""

    env = os.environ if environ is None else environ
    current = [item for item in env.get("PATH", "").split(os.pathsep) if item]
    merged = list(dict.fromkeys([*current, *cli_directories()]))
    env["PATH"] = os.pathsep.join(merged)
    return env["PATH"]
