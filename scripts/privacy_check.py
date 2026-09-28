"""Fail when a tracked file could leak personal data or a secret.

Runs in CI and before a public export. It scans the files git tracks (or every
file under a folder given with ``--root``) for:

* personal e-mail addresses (anything but example/test/noreply domains);
* home-directory paths (``/Users/<name>``, ``/home/<name>``);
* secret-shaped strings (Alpaca ``PK``/``AK`` keys, ``sk-``, ``ghp_``, AWS);
* files that must never be versioned (databases, ``.bak``, ``local.yaml``,
  ``.env``).

Usage: ``uv run python scripts/privacy_check.py [--root DIR]``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Domains that are placeholders, project-neutral, or public institutions.
ALLOWED_EMAIL_DOMAINS = (
    "example.com",
    "example.org",
    "example.test",
    "users.noreply.github.com",
    "noreply.github.com",
    "anthropic.com",
    "claude.ai",  # URL user-info in login-safety tests
    "bea.gov",
    "localhost",
)
# Git refs and package specs look like e-mails; they are not.
NOT_AN_EMAIL = re.compile(r"^(git|npm|pnpm|node)@|@\d")
HOME_PATH = re.compile(r"/(Users|home)/(?!runner\b|user\b|you\b|<)[A-Za-z0-9._-]+/")
SECRETS = {
    "alpaca_key": re.compile(r"\b(PK|AK)[A-Z0-9]{16,}\b"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
FORBIDDEN_FILES = re.compile(
    r"(\.db|\.sqlite3?|\.bak|\.db-wal|\.db-shm)$|(^|/)config/local[^/]*\.yaml$|(^|/)\.env$"
)
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".icns", ".ico", ".woff", ".woff2", ".ttf", ".pdf", ".lock"
}
# Third-party fixtures copied verbatim from public sites.
SKIP_CONTENT = ("tests/fixtures/sources/",)


def tracked_files(root: Path, *, use_git: bool) -> list[str]:
    if use_git:
        output = subprocess.run(
            ["git", "ls-files", "-z"],  # noqa: S607 - fixed argv, git from PATH
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout
        return [name for name in output.decode().split("\0") if name]
    return [
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    ]


def scan(root: Path, files: list[str]) -> list[str]:
    problems: list[str] = []
    for name in files:
        if FORBIDDEN_FILES.search(name):
            problems.append(f"{name}: file type must never be versioned")
            continue
        path = root / name
        if path.suffix.lower() in BINARY_SUFFIXES or name.startswith(SKIP_CONTENT):
            continue
        if name == "scripts/privacy_check.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for match in EMAIL.finditer(line):
                address = match.group(0)
                domain = address.rsplit("@", 1)[1].lower()
                if NOT_AN_EMAIL.search(address) or domain.endswith(ALLOWED_EMAIL_DOMAINS):
                    continue
                problems.append(f"{name}:{number}: e-mail address")
            if HOME_PATH.search(line):
                problems.append(f"{name}:{number}: home-directory path")
            for label, pattern in SECRETS.items():
                if pattern.search(line):
                    problems.append(f"{name}:{number}: looks like a {label}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, help="scan every file under this folder")
    args = parser.parse_args()
    root = (args.root or Path(__file__).resolve().parent.parent).resolve()
    problems = scan(root, tracked_files(root, use_git=args.root is None))
    for problem in problems:
        print(problem)
    print(f"privacy check: {'FAILED' if problems else 'ok'} ({len(problems)} finding(s))")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
