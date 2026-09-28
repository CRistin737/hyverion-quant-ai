# Architecture

Hyverion Quant AI is an asynchronous modular monolith. This keeps financial invariants transactional and
testable locally while preserving ports that can later be separated into VPS services.

```text
Public market/news/social/derivatives data
            |
 sanitized normalization/features/event detector
            |
 provider-neutral agents -> TradeProposal -> CriticAssessment
            |
              RiskEngine (pure deterministic gate)
            |
              ExecutionEngine (only private API boundary)
            |
 exchange adapter / paper adapter -> reconciliation -> audit store
```

AI agents never receive an execution adapter or credentials. `MasterOrchestrator` coordinates the
pipeline but can only request execution through a matching `RiskDecision(ALLOW)`. Exchange timeout
with unknown submission state triggers lookup by client order ID before any retry.

SQLite stores control-plane/business records in WAL mode. High-volume analytical batches belong in
partitioned Parquet and are queried with an isolated read-only DuckDB connection.

The application records the agent/provider/model, schema versions, evidence, signal components,
risk result and execution result so a decision can be reconstructed.

## Mapa de módulos

Paquetes de `src/trading_bot/` tras la consolidación (el detalle completo vive en
`docs/MODULE_CATALOG.md`):

- `core/`: orquestador, pipeline de agentes, contexto, reloj UTC, recuperación y post-cierre.
- `agents/`: registry y runtime provider-neutral, y el crítico determinista.
- `providers/`: adapters por proveedor, `router.py`, `access.py` (suscripción oficial por
  CLI, salud de API keys y tarjetas de estado), `auth.py`, `capabilities.py` y
  `subscription_cli.py`.
- `data/`: `sources.py` (catálogo, registro revisado de fuentes y scheduler),
  `intelligence.py` (noticias, social, derivados y conectores oficiales X/Reddit),
  features, colector externo y stream público.
- `risk/`: `engine.py` (gate determinista con autoridad final), `positions.py`
  (protección determinista de posiciones), profit ladder y session guardian.
- `exchange/`: adapters público/privado/paper, `ExecutionEngine` (único punto de envío,
  reemplazo o cancelación de órdenes) y reconciliación.
- `simulation/`: `backtest.py`, `shadow.py` y `costs.py` (fees/slippage en bps compartidos).
- `strategies/`, `learning/`, `memory/`, `db/`, `schemas/`, `monitoring/`, `security/`,
  `config/`, `control_api.py`, `terminal.py` y `main.py`. La app de escritorio vive fuera del
  paquete Python, en `app/` (Tauri 2 + React).

## Learning and code-change isolation

The optimizer can create a typed `ChangeProposal` for prompts, strategies, configuration, or
software changes. It writes proposal metadata and candidate artifacts only; it never mutates the
active runtime checkout. Code candidates run in an isolated worktree or CI sandbox with tests,
replay, backtest and security checks. Human approval is required before deployment, and each
deployment retains a rollback reference. `AUTO_PROMOTE=false` is the default and live trading is
not a prerequisite for learning.

## Límite del cliente de escritorio

El producto visible es la app de escritorio Tauri 2 + React 19/TypeScript de `app/`. El antiguo
dashboard HTML de navegador y la app PySide6 anterior se eliminaron. `trading_bot.control_api`
expone snapshots JSON acotados, health checks y un stream WebSocket para la app y futuros clientes
VPS. No expone credenciales de exchange ni evita el orquestador, `RiskEngine` o `ExecutionEngine`.

En modo local, el shell Rust arranca el core (`trading_bot api`, empaquetado como sidecar
`hyverion-core` en el build de release) en un puerto loopback con un token por arranque y hace de
proxy de todas las llamadas del webview. Un despliegue VPS futuro ejecutará la misma API y motor sin
interfaz mientras la app se conecta por una red privada autenticada. La terminal Rich
(`terminal.py`) sigue siendo la superficie para SSH/VPS sin GUI. Detalles en
`docs/DESKTOP_TERMINAL.md`.

## Runtime degradation

- AI unavailable: no AI-dependent new entries; deterministic protection continues.
- Stale market data: no new entry.
- Database unhealthy: SAFE MODE; never delete/recreate the database automatically.
- Exchange reconciliation mismatch: no new LIVE entry; alert and protective exits only.
- Daily cap/giveback/loss/streak/drawdown breaker: shadow analysis continues.
