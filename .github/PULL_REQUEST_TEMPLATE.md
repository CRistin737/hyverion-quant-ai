## Summary

<!-- What does this change and why? Link the issue it resolves. -->

## How it was tested

<!-- Commands you ran and what you checked by hand. -->

## Checklist

- [ ] Gates pass locally: `uv run ruff check .`, `uv run mypy src`, `uv run pytest`, and for app changes `pnpm typecheck` and `pnpm test` in `app/`.
- [ ] New or changed financial/security behaviour has focused tests.
- [ ] No secrets, API keys, account IDs, e-mails or personal paths (`uv run python scripts/privacy_check.py` is clean).
- [ ] Nothing enables, unlocks or weakens LIVE trading.
- [ ] Hard rails (AGENTS.md, "Hard rails") are untouched, **or** the justification section below explains why the change is safe.
- [ ] Every commit is signed off (`git commit -s`, Developer Certificate of Origin).
- [ ] Docs updated where behaviour changed (UI copy in Spanish, identifiers in English).

## Hard-rail justification (only if applicable)

<!-- Which rail, why it must change, and how the change keeps the system fail-closed. -->
