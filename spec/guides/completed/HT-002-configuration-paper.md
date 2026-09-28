# HT-002 — Configuration and PAPER defaults

## Complete

The safe first run is USD 100, spot, no leverage, `BTC/USDT` and `ETH/USDT`,
`PAPER` mode and `LIVE_TRADING=false`. The setup wizard validates capital and
shows the percentage return implied by the advisory $10–$50 target.

## Verify

```bash
uv run python -m trading_bot setup
uv run python -m trading_bot doctor
uv run python -m trading_bot paper --fixture --capital 100 --symbol BTC/USDT
```

Leave provider and exchange secrets empty for this check. A fixture rejection
from asset-capacity or risk limits is a valid fail-closed result.

## Evidence and boundary

Public YAML is in `config/`; drafts are atomically written to
`config/local.yaml`. Secret values are handled by Keychain only. Changing the
capital for a real deployment requires operator confirmation.
