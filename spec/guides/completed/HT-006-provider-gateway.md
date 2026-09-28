# HT-006 — Subscription provider gateway

## Complete

There is one `ModelRouter`, one saved primary and an ordered fallback list. The
chain uses one billing mode: subscription CLI **or** explicit API adapters, never
both. Claude, Codex, Grok and Gemini have provider-owned subscription adapters.

```yaml
primary_auth_mode: subscription
primary_provider: anthropic
fallback_providers: [openai, xai, gemini]
```

If a bounded call fails, the next subscription is attempted. If all fail, the
cycle is `NO_TRADE`; it never opens an API console or creates an order.

## Verify

Use **Proveedores** → official login → **Comprobar suscripción**. Then configure
only one primary in **Configuración** and list fallbacks in order. `UNKNOWN`
quota values are correct when the provider has no official machine-readable
telemetry.

```bash
uv run pytest -q tests/unit/test_providers.py tests/unit/test_subscription.py
```

## Evidence and boundary

See `providers/router.py` and `providers/subscription_cli.py`. Local state currently proves Claude and Codex
connected, Grok installed/unknown access, and Gemini requiring CLI setup.
