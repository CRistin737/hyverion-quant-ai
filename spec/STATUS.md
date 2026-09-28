# Implementation status

Updated: 2026-09-28

## Publicación como código abierto (2026-09-28, sin publicar todavía)

- **Licencia y marca:** Apache-2.0; la marca y el logo Hyverion no se licencian.
- **Repositorio público nuevo** (`CRistin737/hyverion-quant-ai`) con un único commit generado por `scripts/export_public.sh`: copia `HEAD`, quita `private/`, pasa `privacy_check.py`, busca cadenas personales y crea el commit con la identidad noreply, sin hacer push.
- **Docs:** `README.md` en inglés, guía de usuario en español `docs/es/GUIA.md`; planes, auditorías del plan, tareas, prompts y pendientes movidos a `private/` (este repositorio los conserva). `plan-audit` y sus tests leen `private/spec/` y se omiten si no existe.
- **Agentes:** las ejecuciones canceladas quedan `CANCELLED`, se limpian al arrancar y al parar, y el motor corre en su propio grupo de procesos.
- **Datos:** purga definitiva de la era cripto con `maintenance purge-legacy`, por decisión del dueño.

## IA autónoma en paper y app simplificada (rama `autonomy/paper-year`, 2026-09-28, solo local)

- **Reglas:** `AGENTS.md` separa protecciones fijas de ajustes; `config/autonomy.yaml` (`paper_autonomous`, margen por parámetro).
- **Riesgo:** perfiles en % del patrimonio de Alpaca (medio 0,5/2/5); topes USD opcionales en paper y obligatorios para LIVE; escalera en % del patrimonio; la devolución de beneficios frena también en paper; ventana macro y `REVISE` del crítico como contexto en modo autónomo.
- **IA:** sin presupuesto en USD; límites reales de 5 h y semana (Claude `/usage`, Codex `rate_limits`); modelos por rol (Sonnet analiza, Opus decide y mejora), cambiables sin reiniciar; «Cambiar de cuenta»; 6 sensores QQQ en paralelo.
- **Operación:** servicio launchd (`service install|uninstall|status`); rutina diaria en hora de Nueva York (preparación, cierre con ciclo de memoria, mejora nocturna, revisión semanal).
- **Automejora:** `learning/ai_improver.py` (PARAMS / PROMPT / FEATURE_REQUEST) y `learning/auto_promote.py` (≥ 5 sesiones nuevas en sombra).
- **Informes:** `reports/period.py`, tabla `equity_snapshots` (migración 0011), CSV y PDF.
- **App:** módulo Noticias; Inteligencia = Agentes, Modelos, Aprendizaje, Memoria; Trading = Mercado, Operaciones (con órdenes), Informe; Riesgo = Límites, Decisiones; Salud del sistema como modal en Ajustes; botón Modelo en la barra superior; sin barras de scroll.
- **Verificación:** ruff, mypy, pytest 682, vitest 51, visual 146, e2e 18, cargo test.

## Migración a QQQ, tanda 1 (fases 0–5, 2026-09-27)

Hyverion deja cripto/Binance y pasa a operar **solo QQQ**. Plan y auditoría en `docs/migration/`.

- **Fase 0.** Auditoría de módulos (KEEP/GENERALIZE/REPLACE/DELETE), arquitectura objetivo, plan en tres tandas, y respaldo de la base y la configuración en `backups/pre-qqq/`.
- **Fase 1.**
  - `core/instruments.py`: lista blanca `{QQQ}`, comprobada en `RiskEngine` y en `ExecutionEngine`.
  - `broker/`, con `BrokerAdapter` y sus capacidades declaradas.
  - `market_data/`, con linaje en cada dato.
  - Configuración v2 con `config/migrate.py`, que no cambia ningún valor de riesgo.
  - Se borran Binance y el agente de derivados; los pesos pasan a `v2`.
- **Fase 2.**
  - `market/clock.py`: calendario XNYS con `exchange_calendars` y America/New_York.
  - Día de PnL en hora de Nueva York.
  - Nuevas puertas en `RiskEngine`: sesión, apertura, corte de fin de día y `max_trades_per_day=3`.
  - Cierre antes del final del día.
  - El motor duerme con el mercado cerrado.
  - `market status` y `GET /api/v1/market/session`.
- **Fase 3.**
  - `market_data/alpaca.py`, verificado con la documentación oficial.
  - VWAP de sesión, ATR, RSI, EMAs y rango de apertura.
  - `simulation/data_audit.py`.
  - Historia medida en sesiones regulares.
- **Fase 4.**
  - `broker/alpaca.py` (solo el host paper): stop nativo por entrada y consulta por `client_order_id` antes de reintentar.
  - Conciliación al arrancar y cada 10 minutos, que bloquea entradas si no cuadra.
  - `broker status` y `broker reconcile`, `GET /api/v1/broker/status` y `POST /api/v1/broker/reconcile`.
  - `doctor` con el formato de la §96.
  - Guía `spec/guides/broker-paper.md`, con la decisión sobre IBKR.
- **Fase 5.**
  - Estrategias `trend_pullback`, `opening_range_breakout` y `mean_reversion`, con especificación en `spec/strategies/`.
  - Costes en tres escenarios.
  - Replay de sesión con walk-forward y veredicto de costes.
  - Comando `replay`.
  - Motivos de NO_TRADE.

**Pendiente del dueño:** las claves de Alpaca Paper, para la conexión real y la línea base de 20 sesiones.

## App de escritorio Tauri y consolidación del backend (2026-09-25)

- La app de escritorio es **Tauri 2 + React 19/TypeScript** en `app/` (única app de escritorio).
  Desarrollo: `cd app && pnpm install && pnpm tauri dev`. Release: `cd app && pnpm build:app`
  (sidecar PyInstaller `packaging/hyverion_core.spec` + `packaging/core_entry.py` en
  `app/src-tauri/binaries/hyverion-core-<triple>`, luego
  `tauri build --config src-tauri/tauri.release.conf.json`).
- Build de release **verificado**: `app/src-tauri/target/release/bundle/macos/Hyverion Quant AI.app`
  (~104 MB) se abre, el sidecar del core arranca y al salir no quedan procesos.
- `trading-bot desktop` abre la app instalada (`/Applications`, `~/Applications`, bundle de release
  del repositorio) o indica cómo compilarla. `terminal.py` (Rich) sigue para SSH/VPS.
- Eliminada la app PySide6: `src/trading_bot/desktop/`, `tests/unit/test_desktop.py`,
  `packaging/desktop_entry.py`, `packaging/hyverion_quant_ai.spec`, `scripts/render_native_ui.py`,
  `scripts/build_brand_assets.py`; PySide6 y pytest-qt salen de `pyproject.toml` (desaparece el
  extra `desktop`; `desktop-build` se mantiene para el sidecar).
- Iconos de marca: `cd app && pnpm icons` (`app/scripts/build-icons.mjs`). Regresión visual:
  Playwright en `app/tests/visual` (`pnpm test:visual`).
- Consolidación del backend completa. Gate: 341 tests
  Python pasan / 6 omitidos; mypy limpio en 117 archivos.
- Pendiente: prueba manual del dueño de la ventana nativa (tema claro ya tiene referencias visuales).

## Resumed 2026-09-23

The owner resumed Hyverion on 2026-09-23 (branch `feat/operation-lifecycle`). The
2026-09-15 pause checkpoint is kept in the internal notes. Resume order: Stage A
operation lifecycle (A1–A4 done), then Memory Phase 0/1, then the authenticated sandbox
when credentials exist. The three-layer memory migration has not started yet.

## Completed on resume (Stage A1–A4)

- Persisted operation lifecycle (`operations` + append-only `operation_events`, revision
  `0003`) covering `PROPOSED -> ... -> EVALUATED` and `UNKNOWN`, `SAFE_MODE`,
  `RECOVERY_REQUIRED`, `CANCEL_PENDING`. Illegal transitions fail closed and are logged.
- Orchestrator entry/exit paths walk the lifecycle; a submission timeout now parks the
  operation in `UNKNOWN` instead of aborting the cycle.
- PAPER/SHADOW closed positions reconcile against the fill log and produce a
  `trade_evaluations` row with cumulative PnL across partial exits, fees, slippage, real
  MFE/MAE and exit efficiency. Mismatch enters `SAFE_MODE`.
- Shadow replays compute MFE/MAE from the price path instead of the final PnL.
- `OperationRecoveryService` runs before every PAPER cycle and resolves each non-terminal
  operation from evidence only; unresolved operations block new entries in every mode via
  `RiskContext.unresolved_operations`.
- Fixed: `alembic upgrade head` on a fresh database failed at `0002`
  (`duplicate column name: attempted_providers`); `0002` is now idempotent.
- Exit orders that leave a resting remainder on a real venue are tracked by client order id and
  never duplicated; unproven exits escalate to `RECOVERY_REQUIRED` (independent review finding).
- Details: [`../docs/OPERATION_LIFECYCLE.md`](../docs/OPERATION_LIFECYCLE.md).

## Native operations lifecycle (A6)

- Operaciones shows every operation's state, a red banner while entries are blocked, the
  per-operation timeline and a venue-verified operator resolution action (never places orders).
- API: snapshot `operations` / `unresolved_operations`, `GET /api/v1/operations/{id}/events`,
  `POST /api/v1/operations/{id}/resolve`. Verified: 251 tests, 82.70% coverage, 33-state UI render.

## Memory (Stage B) progress

- Phase 0 assessment: [`../docs/MEMORIA.md`](../docs/MEMORIA.md).
- Phase 1: `src/trading_bot/memory/` models, ports and `SqlMemoryRepository`; additive revision
  `0004` with seven portable memory tables; time-aware (`as_of`) strategic search; versioned,
  hashed, never-deleted knowledge; agents can only propose weak memory types.
- Phase 2: working memory with a shared JSON contract — in-process (PAPER default, refused in
  production) and Redis (`MEMORY_WORKING_BACKEND=redis`); `WorkingMemoryRebuilder` restores it
  from the database. Docker `memory` profile runs PostgreSQL+pgvector and Redis on localhost only;
  migrations 0001–0004 verified on a fresh PostgreSQL. See `../docs/MEMORIA.md`.
- Phase 3: Obsidian-compatible Knowledge Vault export with drift detection
  (`../docs/MEMORIA.md`).
- Phase 4: stripped-down local embeddings (int8 ONNX MiniLM, own WordPiece tokenizer, pinned
  hashes, no hub client) and pgvector HNSW index (revision `0005`, PostgreSQL only).
- Phase 5: `MemoryRetrievalEngine` (time-aware metadata filter + semantic re-rank),
  `MemoryGateway` with per-agent budgets, sanitized untrusted capsules, usage accounting and
  PENDING proposals; opt-in via `MEMORY_ENABLED` (`../docs/MEMORIA.md`).
- Phase 6: deterministic `MemoryCurator` with evidence from real/shadow evaluations,
  persisted confidence components (revision `0006`) and a promotion ladder that never grants
  confirmed knowledge or rules automatically (`../docs/MEMORIA_CICLO.md`).
- Phase 7: deterministic `TradeReviewDistiller` (7-day review, 30-day synthesis) and the
  `memory-cycle` CLI (distill → curate → vault export).
- Phase 8: knowledge decay, contradiction detection and revalidation driven only by real
  evaluated outcomes of the operations each memory was shown for; retirement is final and
  non-destructive.
- Phase 9 (meta-memory, `memory/meta.py`): the agent pipeline records each specialist's stance
  per proposal in `decision_attributions` (revision `0007`); `MetaMemoryAnalyzer` scores agent
  reliability per market regime, a Memory Value Score per knowledge item, and the shadow
  counterfactual (loss avoided / profit forgone on risk-rejected proposals). Advisory only —
  never read by RiskEngine or sizing. Runs inside `memory-cycle`.
- Phase 10 (operations): `MemoryHealthService` (HEALTHY/DEGRADED/FAILED for working memory,
  historical DB, Timescale, pgvector, embeddings, Parquet and vault drift), native **Memoria**
  workspace (health, filtered knowledge search, detail with evidence/versions/performance/
  conflicts, agent reliability, human confirm/retire with reason), control-API routes under
  `/api/v1/memory` and the `memory` CLI (status, search, inspect, candidates, conflicts,
  promote, retire, distill, synthesize, validate, backup). See
  [`../docs/MEMORIA_OPERACION.md`](../docs/MEMORIA_OPERACION.md).
- Independent review of A6 + memory stack: one confirmed finding (embedding model reloaded
  per cycle on the event loop) fixed with a per-process cache loaded off the loop.
- Phase 11 (research): point-in-time memory history (`strategic_memory_snapshots`, revision
  `0008`), `search_strategic_as_of` / `MemoryRetrievalEngine(point_in_time=True)` and the
  leakage-free `MemoryReplayEvaluator` (`memory replay`), which scores warnings and supporting
  knowledge against real and shadow outcomes without writing usage or outcomes.
- Phase 12 (production evidence): `MemoryIndexer` fills pgvector (it was never populated
  before; semantic retrieval had no vectors), fail-closed all-or-nothing `memory restore`
  (verified SQLite → fresh PostgreSQL), `memory export-vault`, `memory rebuild-working`,
  `memory reindex`, a 600-memory load test, an end-to-end test of the prompt's success criteria
  and an import-boundary test proving risk/exchange/positions never import memory. Docs:
  the nine required memory docs, consolidated on 2026-09-26 into `MEMORIA.md`,
  `MEMORIA_CICLO.md` and `MEMORIA_OPERACION.md`.
- Independent review of Phase 12: no high-confidence defects; follow-ups applied — restore now
  rejects non-object/list JSON values, and the indexer re-embeds when the embedding model or
  version changes.
- Obsidian: notes carry `aliases`/`tags` properties and a generated links block (market, regime,
  contradicting knowledge, knowledge map). `write_indexes` regenerates `03-Markets`,
  `04-Regimes` and `00-System/Knowledge Map`. The owner section after
  `<!-- hyverion:owner-notes -->` is preserved across rewrites and moves; previously a rewrite
  would have erased the owner's own notes. The memory cycle refreshes the whole vault.
- Packaged app fix: the bundle runs with cwd `/`, so the memory vault and embedding model paths
  are now relocated under `~/Library/Application Support/Hyverion Quant AI/` like the database.
- Verified gate (after Phase 12): Ruff, mypy (127 files), 341 tests with the `memory` profile
  and model (6 service tests skip without Docker), 85.53% coverage; SQLite and PostgreSQL at revision `0008`.
- Independent review of Phases 9–10: a malformed `REDIS_URL` made the health check raise instead
  of reporting FAILED (fixed, tested); failed dashboard reads no longer release an in-flight
  decision. The shared-Docker PostgreSQL test no longer depends on top-N ranking.

## Brand (2026-09-23)

- New Hyverion Quant AI logo chosen by the owner: a clean H whose right stem is the Ethereum
  diamond, brand green `#24F79A` on a dark tile (the orange ₿ strokes were removed at the
  owner's request). Vector sources in `assets/brand/`; `cd app && pnpm icons` regenerates the app
  icon PNG, the header mark PNG and the macOS `.icns`. Replaces the orbital mark everywhere.

## Verified baseline

- La app de escritorio Tauri se abre con el icono de Hyverion (la terminal PySide6 original se eliminó el 2026-09-25).
- SQLite/WAL control plane and audit-backed control API are working.
- PAPER, SHADOW, risk ladder, deterministic execution boundary and LIVE block exist.
- Provider-neutral adapters and Keychain secret store exist.
- RSS parsing, allowlist checks, sanitization and deduplication primitives exist.
- `uv run pytest --cov=trading_bot --cov-report=term-missing -q`: 195 passed;
  coverage is 81.50%.

## Completed in this increment

- App window title is exactly `Hyverion Quant AI`.
- Removed the financial-intelligence tagline; the transparent orbital mark is shown beside the app name.
- Removed numbered page eyebrows and page descriptions.
- Added compact top navigation with primary workspaces and a `More` menu.
- Added the initial living `spec/` source of truth.
- Added a native Keychain API-key entry action in Provider Center.
- Provider keys are reported as `AVAILABLE` until a real provider healthcheck verifies access.
- Added deterministic connector scheduling with exponential backoff and pause states.
- Added official read-only Reddit OAuth and X recent-search adapters with bounded parsing.
- Added stale/asset-safe `AgentContextAssembler` for specialist contexts.
- Added deterministic context validation to the Master Orchestrator; stale or mismatched
  context is persisted as a fail-closed system event and produces no trade.
- Added official read-only models-endpoint healthchecks for OpenAI/Codex, xAI/Grok,
  Anthropic/Claude and Gemini, with timeout, auth-expired and rate-limit states.
- Added provider limit slots for context, session, four-hour and weekly usage. Values remain
  `UNKNOWN` unless explicitly configured or later supplied by a verified official quota source.
- Added a separate subscription status contract and Provider Center refresh action. Codex and
  Claude use their official local login/status commands; Grok is explicit `UNKNOWN` because its
  CLI does not expose a documented machine-readable account/quota status command.
- Added schema-constrained subscription CLI adapters for Codex, Claude Code, and Grok. API keys
  are stripped from their child-process environments, and subscription mode cannot silently fall
  back to API billing.
- Added the one-primary subscription gateway task and capability matrix. Gemini now uses the
  provider-owned Gemini CLI Google-account flow and documented headless JSON envelope; its
  response is validated fail closed, while quotas remain `UNKNOWN` without official telemetry.
  The router records ordered fallback attempts and safe failure codes, and Settings/API expose the
  primary plus fallback chain.
- Added a read-only **Probar cadena de suscripción** action in Proveedores. It runs one
  structured PAPER probe through the single `ModelRouter`, persists attempted providers and
  fallback reasons, and returns `NO_TRADE` on exhaustion without exposing model output.
- Made subscription login strict for every provider card: missing Gemini/Grok/Codex/Claude CLIs
  are explicit install/authentication blockers and never redirect subscription mode to an API
  console. The one secure Keychain vault remains reserved for explicit API mode.
- Hardened provider failover against unexpected SDK/CLI boundary exceptions and expanded the
  subscription process environment denylist for provider access tokens and Google credential
  files. The usage snapshot now preserves the attempted provider chain.
- Claude Code usage is now read through its official zero-turn `/usage` command when available;
  current-session and weekly remaining percentages plus reset labels are persisted in the provider
  snapshot. Codex and Grok remain `UNKNOWN` for quota fields without a documented machine-readable
  source.
- Added the Master Orchestrator neutral `AGENT.md` specification.
- Expanded the native Overview into a personal operating center: persisted daily PnL
  chart (real history only), six financial KPIs, RiskEngine protection details, configured
  market pulse, active positions, agent health, recent activity and actionable alerts.
- Added explicit empty states so missing market snapshots, positions, activity and alerts
  are visible instead of appearing as blank tables. The generated UTC timestamp remains only
  in the bottom validation bar.
- Replaced the provider dropdown-first workflow with visible provider cards for Claude,
  OpenAI/Codex, Grok/xAI and Gemini. Each card exposes official account login, secure API setup,
  credential check and subscription/limits actions while retaining the detailed
  context/session/4h/weekly view.
- Added a guided Settings workspace with AI account, Exchange, Sources, Risk and System tabs.
  Public drafts validate through the control API and persist atomically to `config/local.yaml`;
  secrets are stored only through the OS Keychain namespace and never enter the draft payload.
- Added local BTC and ETH SVG marks for offline-safe market, position and overview tables,
  bundled into the macOS package without remote asset dependencies.
- Added configuration-manager and control-API validation/apply contracts with backup-on-write,
  plus regression tests for unsupported exchanges, risk relationships and secret non-persistence.
- Added the typed specialist fan-out: market → technical → regime → news → social → derivatives
  → strategy → critic. Provider failures, stale context and incomplete strategy envelopes persist
  a fail-closed system event; no specialist output bypasses RiskEngine.
- Added a bounded external-intelligence collector. Reviewed RSS feeds and official X/Reddit
  connectors can now normalize, deduplicate, sanitize, persist and pass evidence to agents without
  exposing credentials or allowing arbitrary HTML scraping.
- Added an explicit two-part source enablement gate in Settings: exact feed URL plus reviewed
  source ID. The Sources workspace now reflects the configured readiness instead of showing a
  generic disabled row.
- Added deterministic shadow entry/stop/target replay with fees and slippage, plus a walk-forward
  backtest result that keeps train/validation/test boundaries and aggregates only OOS windows.
- Rejected PAPER/SHADOW proposals now automatically produce a persisted shadow replay, and the CLI
  exposes `backtest --walk-forward` for explicit OOS evidence.
- Added the native Guides workspace and durable `spec/guides/` handbook so the operating path,
  provider authentication, source review, risk, learning and recovery rules survive context loss.
- Added a durable `TradingStateRepository` for account equity, high-water mark, daily PnL, paper
  positions and fills; every market cycle marks open positions deterministically so unrealized PnL
  feeds the RiskEngine and Overview after a restart.
- Added deterministic reduce-only exits: target/stop protection runs before new entry analysis,
  `EXIT_ONLY` risk decisions are authorized by `ExecutionEngine`, and closed fills update realized
  PnL, fees and losing streak without allowing an AI provider to remove protection.
- Added typed `SourceRunRecord` outcomes and persisted source-run health for reviewed RSS/X/Reddit
  collection passes, with snapshot-level freshness, record counts and last errors.
- Added persisted rejected-proposal evaluations, walk-forward experiment metadata, agent-output
  memory summaries and a bounded metrics registry for local/VPS observability.
- Added persisted provider health/subscription check events. A fresh control API process now
  reconstructs the last sanitized provider state without exposing credentials.
- Added `spec/PLAN_COVERAGE.md`, the native terminal UI operations guide and a durable UI/plan task
  so every master-prompt requirement has a status, evidence path and explicit remaining gate.
- Added configurable external-collection intervals with bounded exponential backoff in the
  continuous PAPER loop; source errors remain visible in `source_runs` and never increase pressure.
- Added a selectable native decision trace that exposes safe links between agent runs, proposals,
  risk, provider attempts, fills and source timestamps without returning prompts or secrets.
- Added a token-protected observability export (`json` and bounded `prometheus`) plus a native
  Auditoría runtime-metrics panel. Invalid values and sensitive metric labels are dropped before
  exposure.
- Added an explicit deterministic readiness report at `/api/v1/readiness` and a native Auditoría
  panel. It proves PAPER consistency while keeping LIVE authorization gated and false.
- Added typed local reconciliation snapshots, protective-stop recovery checks and mismatch auditing. The result is visible in Risk;
  an exchange mismatch is explicitly `SAFE MODE` and cannot authorize new entries.
- Added durable UTC AI-budget accounting so recreating the provider router per PAPER cycle or
  process restart cannot reset the daily API spend guard. Subscription usage remains cost-free
  and is explicitly separated from API billing in usage/audit records.
- Budget-overrun responses now persist the API usage that was actually consumed before the
  cycle fails closed, so a billable response cannot disappear from the daily cost ledger.
- Added deterministic validation of every production signal score against the versioned weight
  set, surfaced the active strategy/weight configuration in the control API and proposal detail,
  and rejected mismatched model-supplied scores fail closed before RiskEngine.
- Added reconnecting public WebSocket ticker ingestion with bounded backoff, malformed-frame
  isolation and cancellation support. The continuous runtime still uses REST snapshots until
  replay-gap monitoring is completed.
- Added `ReplayGapMonitor` and `PublicStreamCoordinator`: `run --stream` now fans out
  public ticker frames through per-symbol duplicate, out-of-order, gap and staleness
  admission before a PAPER cycle. REST polling remains the default until local stream
  observation is accepted.
- Added cost-aware Learning metrics (expectancy, win rate, profit factor, drawdown, fees,
  slippage, cost drag and per-agent incremental value) with invalid-row accounting in the native
  Learning workspace. These metrics are descriptive and cannot auto-promote code or risk.
- Added strict strategy enablement validation so configuration cannot claim unavailable
  `breakout` or `regime_mean_reversion` plugins; only the tested `trend_momentum` plugin is
  enabled until additional strategies have replay evidence.
- Added a typed deterministic strategy-plugin contract, breakout and regime-aware
  mean-reversion implementations, a stable score-ranked ensemble and a registry. New plugins
  remain disabled by default until their replay and walk-forward evidence is reviewed.
- Added additive SQLite compatibility for older desktop control-plane files. The app can open a
  pre-migration database, add the new provider-attempt column safely and then serve the snapshot;
  destructive/schema-changing work remains versioned in Alembic.
- Added the signed Binance Spot Testnet private adapter and native read-only reconciliation action.
  The adapter maps balances, open orders and recent fills, rejects production private hosts and
  keeps submit/cancel behind an explicit protective-order capability gate. Settings stores exchange
  credentials in a dedicated Keychain namespace and enters SAFE MODE on mismatch.
- Added replay/staleness admission for the opt-in public stream runtime. `run --stream` now uses
  duplicate, ordering, gap and stale-data checks before a PAPER cycle; REST polling remains the
  reviewed default until stream observation is accepted.
- Added native Backtest actions for deterministic baseline and OOS walk-forward replay. The control
  API validates a bounded request, persists the experiment, and refreshes the Backtest workspace;
  it never creates an execution intent.

## In progress

- Official usage/quota telemetry beyond capability detection (the UI deliberately shows
  `UNKNOWN` when the provider does not expose a verified quota endpoint).
- Provider/source recovery actions in the native UI remain gated by connector
  ToS/robots/rate-limit review; safe interval scheduling and exponential
  backoff are already active in the PAPER loop.
- Operator acceptance for the selected subscription chain: Claude and Codex are connected in the
  current local account context; Grok is installed but reports `UNKNOWN` because its CLI has no
  documented machine-readable quota status; Gemini CLI is not installed yet.
- Continuous source collectors remain intentionally gated: Settings can store approved
  credentials, but a source is not enabled until its provider, ToS/robots review and collector
  schedule are explicitly configured.

## Safety gate

No real orders are enabled. No credential is required for the first run. A failed
provider, stale source, invalid schema or reconciliation mismatch must fail closed.

## Migración a QQQ, tandas 2 y 3 (fases 6–15, 2026-09-28)

- **Capital:** es el de la cuenta Alpaca Paper (sincronizado en cada conciliación). Ya no hay capital configurado y el motor no arranca sin broker conectado.
- **Fuentes gratuitas:** Fed, BLS, BEA, SEC (N-PORT y 8-K/10-Q/10-K), FRED, Finnhub, y noticias y opciones de Alpaca. Pasan por `SafeFetcher`, tienen un registro de confianza y claves en el Llavero.
- **Regla macro:** el RiskEngine bloquea entradas alrededor de los eventos de alto impacto y falla cerrado si el calendario tiene más de 7 días.
- **Nasdaq-100:** amplitud, contribución y divergencias. **Opciones:** volatilidad como sensor. **Noticias:** agrupadas por clúster.
- **Agentes:** 17 especificaciones con herramientas de solo lectura, EvidenceGraph y confluencia, crítico con inteligencia y `hyverion_strategy@0.1.0` (apagada).
- **Memoria:** análogos, memoria de eventos y fiabilidad por régimen y franja horaria.
- **Replay:** Monte Carlo, regla macro y hasta 120 sesiones. Hay además comparación del paper con la realidad, informe diario y la lista §105 (siempre `NOT_READY`).
- **UI:** Centro QQQ, Informes y Ajustes › Fuentes de datos.
- **CLI:** `macro next`, `sources status|refresh`, `report daily|live-readiness`.

## Simplificación de la app, versiones y optimizador (2026-09-27)

**App**
- 6 áreas y 11 pestañas (`app/src/shell/routes.ts`), con redirecciones desde las rutas antiguas.
- Configuración en el sitio: Modelos, Fuentes y Límites.
- Operaciones unificadas (`GET /api/v1/trades`).
- Órdenes con KPIs y filtros (`/api/v1/orders`, `/api/v1/orders/rejected`).
- Gráfica tipo TradingView (`/api/v1/market/candles`).

**Versiones**
- `component_versions` (migración 0009).
- `learning/versions.py`: lista blanca de lo aplicable.
- `learning/deployer.py`: aplicar y deshacer, solo con aprobación humana.
- Endpoints `/api/v1/components*` y `/api/v1/agents/{id}/spec`.

**Optimizador**
- `learning/optimizer_job.py` elige dentro de muestra y valida fuera de muestra con la regla
  campeón/retador. Corre una vez al día desde el mantenimiento del motor y también con
  `POST /api/v1/learning/optimizer/run`.
- El filtro por régimen es compartido entre el motor en vivo y la simulación (`strategies/regime.py`).

**Correcciones**
- El snapshot ya no repite posiciones ni velas.
- Las transiciones de propuestas usan su propia hora.
- `GET /api/v1/learning/timeline`.

## Seguridad financiera, suscripciones y permisos (2026-09-26)
- **Riesgo/ejecución:** `max_position_hold_minutes` (240 por defecto) fuerza `TIME_EXIT`; el ciclo protege
  también las posiciones cuyo símbolo salió del universo; los fallos de ciclo quedan como eventos y alertas;
  `RiskEngine` deniega entradas con precio lejos del mercado (`max_entry_deviation_bps`); `ExecutionEngine`
  exige aprobación exacta, vigente y de un solo uso. Cierre de emergencia desde la app (`POST /engine/flatten`).
- **Suscripciones:** `providers/circuit.py` (disyuntor compartido por proceso, cooldowns por código, sonda
  semiabierta), clasificación de errores de la CLI, estado visible en Inteligencia › Modelos.
  `providers/login_flow.py`: login real con progreso (`GET/POST /providers/{id}/login`), solo hosts oficiales.
- **Permisos:** `tests/unit/test_architecture_boundaries.py` hace ejecutable el límite de AGENTS.md
  (el código de IA no importa clientes del exchange; solo `ExecutionEngine` envía órdenes; cada AGENT.md
  declara sus límites).

## Control del motor desde la app (2026-09-26)
- `engine_supervisor.py`: proceso hijo `run --poll` en PAPER, candado `flock` único por directorio de
  datos (detecta motores externos), sin el token del API en su entorno, se detiene con el núcleo.
- API: `GET /api/v1/engine`, `POST /engine/start` (409 con código estable: `broker_not_connected`,
  `engine_already_running`, `live_not_allowed`, `invalid_interval`), `POST /engine/stop`; el snapshot
  incluye `engine`. Cada arranque/parada queda en `system_events`.
- UI: control en la barra de estado, tarjeta de atención en Inicio, acciones en ⌘K.

## Seguimiento de auditoría (2026-09-25)

Auditoría de seguridad del remaster: sin hallazgos críticos, altos ni medios. Los 3 hallazgos bajos
se corrigieron (lectura `O_NOFOLLOW` en la Bóveda, watchdog del núcleo por pipe además de PID,
lista del Llavero sin `exchange:api_passphrase`). Referencias visuales del tema claro añadidas.
