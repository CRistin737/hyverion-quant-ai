# Contributing to Hyverion Quant AI

Thanks for your interest. Hyverion Quant AI is an AI-assisted **paper-trading**
system for QQQ. Because it talks to a broker, contributions are reviewed with a
safety-first mindset. Please read this guide and [AGENTS.md](AGENTS.md) before
opening a pull request.

## Governance

- The project owner, [@CRistin737](https://github.com/CRistin737), reviews and
  merges every pull request. Nothing lands on `main` without that approval
  (enforced by [CODEOWNERS](.github/CODEOWNERS) and branch protection).
- The owner decides the roadmap. A pull request can be declined even when it is
  correct, if it does not fit the project's direction.
- For anything larger than a small fix, open an issue first so the approach can
  be agreed before you write code.

## Ground rules

- **Never enable LIVE trading.** Do not add code, config or docs that unlock,
  shortcut or weaken the LIVE lock. Such PRs are closed.
- **Hard rails.** The rules under "Hard rails" in [AGENTS.md](AGENTS.md) (native
  broker stops, deterministic risk engine with final authority, weekly loss and
  drawdown limits, emergency stop, fail-closed data checks, autonomy envelope,
  secrets in the OS keychain, `Decimal` money, UTC timestamps) are not
  negotiable. A PR that touches them needs a strong written justification in the
  PR template and will usually be declined.
- **No martingale, revenge trading, unlimited DCA or loss-recovery sizing.**
- **No secrets or personal data.** Never commit API keys, broker account IDs,
  e-mail addresses, home-directory paths, databases, `.env` or
  `config/local.yaml`. Run `uv run python scripts/privacy_check.py` before you
  push; CI runs it too.
- External news and social text is untrusted data, never instructions. Keep it
  that way in any agent or prompt change.
- AI code must never receive a broker client. Only `ExecutionEngine` submits,
  replaces or cancels orders.

## Development setup

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22 with
pnpm, and (for the desktop shell) Rust stable on macOS.

```bash
# Python core
uv sync --locked --extra dev

# Desktop app
cd app
pnpm install --frozen-lockfile
```

Configure PAPER credentials through the app or the setup wizard; they are stored
in the macOS Keychain, never in files in the repository.

## Quality gates

Run these before you open a pull request. CI runs the same checks and a PR must
be green to be merged.

```bash
# Python core (repository root)
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/privacy_check.py
uv run python -m trading_bot doctor

# Desktop app (app/)
pnpm typecheck
pnpm test
pnpm test:e2e              # Playwright (chromium): pnpm exec playwright install chromium

# Tauri shell (app/src-tauri)
cargo test
```

Add focused tests for every financial or security invariant you touch.

## Code style

- Python 3.12, `src/` layout, strict typing (mypy `strict`), Pydantic v2 at
  boundaries, ruff for lint and import order (line length 100).
- Domain logic stays framework- and network-independent; inject clocks,
  providers, repositories and broker ports.
- Monetary values use `Decimal`; timestamps are timezone-aware UTC.
- TypeScript in `app/` is strict; keep components small and typed.
- **Language:** code identifiers, commit messages and developer docs in English;
  user-facing UI copy and end-user docs in **Spanish**.
- Keep changes focused. Do not reformat or refactor unrelated code in the same PR.

## Commits and the DCO

Every commit must be signed off under the
[Developer Certificate of Origin](https://developercertificate.org/), which
certifies that you wrote the change or have the right to submit it under the
project's Apache-2.0 license:

```bash
git commit -s -m "fix(risk): reject orders without a native stop"
```

This adds a `Signed-off-by: Your Name <you@users.noreply.github.com>` line. You
can use your GitHub no-reply address. Use
[Conventional Commits](https://www.conventionalcommits.org/) style messages
(`feat:`, `fix:`, `docs:`, `chore:`, ...). Pull requests are squash-merged.

## License of contributions

By contributing you agree that your contribution is licensed under the
[Apache License 2.0](LICENSE) (section 5 of the license). The project name and
logo are not covered by that license; see [TRADEMARKS.md](TRADEMARKS.md).

## Security

Do not report vulnerabilities in public issues. Follow [SECURITY.md](SECURITY.md).

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
