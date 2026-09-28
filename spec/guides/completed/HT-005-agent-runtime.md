# HT-005 — Agent runtime

## Complete

Neutral `agents/*/AGENT.md` files define role, inputs, outputs, tools,
prohibitions, failure conditions, quality checks and versions. The specialist
pipeline is market → technical → regime → news → social → derivatives →
strategy → critic. Every response is validated by Pydantic before persistence.

## Verify

```bash
uv run pytest -q tests/unit/test_agent_pipeline.py tests/unit/test_agent_runtime.py
```

Use **Agentes** to inspect state, provider, model, latency, version, output and
error. A provider/schema/context failure records an audit event and prevents a
new entry; deterministic position protection still runs.

## Evidence and boundary

See `core/agent_pipeline.py`, `agents/runtime.py` and the neutral AGENT specs.
Continuous event scheduling and richer position/session triggers remain active
implementation gates.
