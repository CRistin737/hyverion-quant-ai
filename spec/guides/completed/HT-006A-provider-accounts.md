# HT-006A — Subscription account completion and failover

## Implemented contract

Hyverion stores one provider chain in Settings: one `primary_provider`, one
authentication mode and an ordered `fallback_providers` list. The single
`ModelRouter` is the only gateway used by agents. A provider failure may move
the request to the next same-mode subscription, but a fallback never becomes a
new saved primary.

```text
one saved primary -> ordered subscriptions -> validated output -> NO_TRADE
```

Subscription mode uses only the provider-owned official CLIs for Claude,
Codex, Grok and Gemini. API keys, browser cookies and OAuth/session tokens are
not copied into the terminal. If the chain is exhausted, deterministic risk,
existing protective stops and position management continue while new AI-based
proposals stop.

## Verify locally

1. Open **Proveedores** and run the official account check for each provider.
2. Select exactly one primary and save the ordered fallback list once in
   **Configuración**.
3. Use **Probar cadena de suscripción** for one bounded PAPER request and
   inspect `attempted_providers` in **Auditoría**.
4. Confirm that a forced primary failure uses the next subscription and that an
   all-provider failure records `NO_TRADE` without an order.

```bash
uv run pytest -q tests/unit/test_providers.py tests/unit/test_subscription.py
curl -fsS -X POST http://127.0.0.1:8787/api/v1/providers/chain/probe
curl -fsS http://127.0.0.1:8787/api/v1/snapshot
```

## Current account boundary

- Claude and Codex are connected in the current local account context.
- Grok requires an authenticated bounded invocation; its quota remains
  `UNKNOWN` without an official machine-readable source.
- Gemini requires the official CLI installation and Google-account sign-in;
  it never falls back to Gemini API billing in subscription mode.

This guide documents implemented code plus its operator gate. It does not
authorize LIVE trading or claim vendor quota values that were not verified.
