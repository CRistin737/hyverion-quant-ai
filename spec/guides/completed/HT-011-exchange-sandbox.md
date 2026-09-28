# HT-011 — Exchange sandbox boundary

> **Superseded on 2026-09-27 (QQQ migration).** The Binance adapter was removed.
> The same boundary now applies to the Alpaca PAPER broker: pinned paper host,
> read-only reconciliation, SAFE MODE on mismatch. See
> [broker-paper.md](../broker-paper.md). The historical record follows.

## Complete

The Binance Spot Testnet adapter supports signed read-only balances, open-order
and recent-fill reconciliation. Production private hosts are rejected. A
mismatch is persisted and projected as `SAFE MODE`.

## Verify

Use **Configuración → Exchange → Verify sandbox account** only after storing
testnet credentials in Keychain. Never paste them into chat. Without credentials,
`AUTH_REQUIRED` is the expected safe result.

```bash
uv run pytest -q tests/unit/test_alpaca_broker.py
```

## Boundary

Submit/cancel mutations, native protective stops and LIVE remain blocked until
the exchange sandbox reconciliation acceptance checklist passes
with authenticated evidence and independent review. The typed atomic
cancel/replace contract is covered offline but is not enabled by default.
