# Public stream and replay safety

The terminal supports two public-data runtime paths:

- `uv run python -m trading_bot run` uses bounded REST polling and is the
  conservative default.
- `uv run python -m trading_bot run --stream` uses the Alpaca market-data WebSocket (IEX quotes and trades)
  WebSocket through `PublicStreamCoordinator`.

The stream never authorizes a trade by itself. Each frame passes through
`ReplayGapMonitor` before it can reach features, agents or `RiskEngine`:

| State | Meaning | Action |
|---|---|---|
| `OK` | New, ordered and fresh event | Run the PAPER cycle |
| `DUPLICATE` | Same event timestamp as the previous frame | Audit and skip |
| `OUT_OF_ORDER` | Event timestamp moved backwards | Audit and skip |
| `GAP` | Event-time gap exceeded the configured threshold | Audit and skip |
| `STALE` | Event timestamp is older than the exchange freshness limit | Audit and skip |

The adapter reconnects with bounded exponential backoff. Malformed frames are
discarded at the transport boundary and never enter an agent context. A stream
cycle exception is recorded as `MARKET_STREAM_CYCLE_FAILED` while other symbols
continue independently.

## Local acceptance

1. Run `uv run python -m trading_bot doctor`.
2. Start the native terminal in PAPER mode.
3. Run `uv run python -m trading_bot run --stream` in a separate terminal.
4. Inspect **Mercados** and **Auditoría** for freshness and stream rejection
   events.
5. Stop with `Ctrl+C`; no private exchange endpoint is contacted.

Do not promote stream mode to a production default until reconnects, gaps,
staleness and restart behavior have been observed and recorded. Authenticated
exchange user-data streams require a separate sandbox gate.
