# Catálogo de módulos — Hyverion Quant AI

Este documento es el mapa operativo del sistema. Describe qué hace cada módulo,
qué datos recibe y qué debe mostrar el terminal. Los identificadores de código se
mantienen en inglés para conservar contratos estables; la interfaz nativa se
presenta en español.

## Flujo de seguridad

```text
datos públicos -> agentes -> propuesta -> crítico -> RiskEngine -> ExecutionEngine -> exchange
```

Los agentes nunca reciben clientes privados del exchange. `RiskEngine` decide de
forma determinista y `ExecutionEngine` es el único componente que podría enviar una
orden privada. PAPER/SIMULACIÓN es el modo inicial; LIVE permanece bloqueado.

## Capas y módulos Python

| Área | Módulos | Responsabilidad | Resultado observable |
|---|---|---|---|
| `core/` | `orchestrator.py`, `agent_pipeline.py`, `context.py`, `clock.py` | Coordina ciclos, valida contexto, enruta el fan-out de agentes y registra eventos UTC. | Ciclos, decisiones, fuentes y fallos trazables en Actividad/Auditoría. |
| `agents/` | `registry.py`, `runtime.py`, `critic.py` | Carga especificaciones neutrales, invoca salidas estructuradas y aplica reintentos/fail-closed. | Estado de cada agente, versión, última acción y motivo de rechazo. |
| `providers/` | interfaces, adapters Claude/OpenAI-Codex/Grok/Gemini, `router.py`, `access.py` (suscripción oficial por CLI, salud de API keys y tarjetas de estado), `auth.py`, `capabilities.py`, `subscription_cli.py` | Un proveedor principal, fallbacks ordenados y autenticación oficial por suscripción o API. | Tarjetas de proveedor, autenticación, perfil, límites verificados, coste y motivo de fallback. |
| `data/` | `sources.py` (catálogo de fuentes, registro revisado y scheduler), `intelligence.py` (noticias/RSS, social, derivados y conectores oficiales X/Reddit), `features.py`, `external_collector.py`, `stream.py` | Recolecta, normaliza, deduplica, sanitiza y controla frescura/backoff. | Fuentes activas, última ejecución, registros, error y frescura en Fuentes/Mercados. |
| `exchange/` | adapters públicos/privados, paper, execution, reconciliation, models | Abstrae exchange, simulación, idempotencia, timeouts, fills y reconciliación. | Estado de exchange, sandbox, órdenes, fills, posiciones y SAFE MODE. |
| `risk/` | `engine.py`, `positions.py`, `profit_ladder.py`, `session_guardian.py` | Límites monetarios, daily/weekly loss, ladder, giveback, streaks y bloqueo LIVE; `positions.py` gestiona exposición y propuestas de salida con protección determinista previa a la IA. `engine.py` sigue siendo el gate determinista con autoridad final. | Nivel de protección, multiplicador, suelo protegido, capacidad restante, alertas, posiciones abiertas, stop/target y decisión HOLD/EXIT. |
| `strategies/` | contrato, registry, trend/momentum, breakout, mean-reversion, ensemble, score | Plugins deterministas; solo estrategias con evidencia pueden habilitarse. | Señales, score compuesto, evidencia, contradicciones y estado habilitado. |
| `simulation/` | `backtest.py`, `shadow.py`, `costs.py` | Replay reproducible (baseline y walk-forward OOS) y simulación shadow de propuestas rechazadas o posteriores al cap sin arriesgar capital; `costs.py` concentra el cálculo de fees/slippage en bps (10/5 bps por defecto). | Experimentos, PnL neto, drawdown, entradas/salidas virtuales, supuestos de coste y comparación futura contra real. |
| `learning/` | métricas, supervisor, optimizer, champion/challenger | Evalúa resultados y propone cambios revisables; no edita producción ni auto-promueve. | Métricas, propuestas, evidencia, estado de revisión y rollback disponible. |
| `db/` | modelos, SQLAlchemy/WAL, repositorios, estado, backup | Plano de control auditable y recuperación; analítica histórica queda preparada para Parquet/DuckDB. | Auditoría, snapshots, copias verificadas y restauración segura. |
| `schemas/` | assessments, trading, learning, observability | Contratos Pydantic estrictos para toda decisión y respuesta. | Invalidaciones visibles y rechazo seguro, nunca parseo de texto libre. |
| `monitoring/` | logs, metrics, alerts, readiness, retention, plan audit | Observabilidad estructurada, readiness, export protegido y retención. | Salud, alertas, métricas, eventos, plan audit y estado de recuperación. |
| `control_api.py` | API local loopback | Expone snapshot, decisiones, settings, providers, backtest, learning y readiness para la app de escritorio. | Estado único consumido por la UI; no sirve un dashboard web público. |
| `terminal.py` | terminal Rich sin GUI | Presenta estado en SSH/VPS sin GUI. | Operación sin GUI en VPS o recuperación. |
| `main.py` | CLI | Punto de entrada para setup, doctor, plan-audit, collect, paper, shadow, backtest, run, desktop (abre la app Tauri instalada) y live bloqueado. | Comandos reproducibles y mensajes seguros. |

La interfaz de escritorio no es un módulo Python: es la app Tauri 2 + React de `app/` (frontend en
`app/src`, shell Rust en `app/src-tauri`), que consume `control_api.py` a través del proxy del shell.
Ver `docs/DESKTOP_TERMINAL.md` y `docs/MACOS_APP.md`.

## Agentes especializados

Cada `agents/*/AGENT.md` es la fuente de verdad provider-neutral y define rol,
entradas, salidas, herramientas, prohibiciones, fallos, checklist y versión.

| Agente | Qué analiza/proporciona | Vista donde se refleja |
|---|---|---|
| Market | Precio, OHLCV, spread, volumen, order book y frescura. | Mercados, Pulso del mercado, Actividad. |
| Technical / Quant | Tendencia, momentum, ATR, RSI, medias, estructura y features calculados en Python. | Mercados, señales y detalle de propuesta. |
| Regime | TRENDING/RANGING/HIGH_VOLATILITY/EVENT_DRIVEN/UNCERTAIN con evidencia. | Mercados y compatibilidad de estrategia. |
| News | RSS/API permitidas, credibilidad, duplicados, severidad y activos afectados. | Fuentes, propuesta, alertas. |
| Social | Reddit/X legales, sentimiento, spam, bots y cambios narrativos. | Fuentes, propuesta y auditoría. |
| Derivatives | Funding, OI, liquidaciones, basis y posicionamiento; `insufficient_data` si falta evidencia. | Mercados y decisión. |
| Strategy | Crea `TradeProposal` o `NO_TRADE`; nunca una orden. | Señales, Operaciones, decisión trace. |
| Critic | Intenta invalidar la tesis: liquidez, noticias, correlación, overfit y R/R. | Propuestas rechazadas y Auditoría. |
| Position Manager | Propone HOLD, MOVE_STOP, PARTIAL_CLOSE, TAKE_PROFIT, TRAIL o EMERGENCY_EXIT. | Posiciones y Riesgo. |
| Session Guardian | Decide CONTINUE, REDUCE_RISK, PAUSE o STOP_LIVE_FOR_DAY. | Resumen, Riesgo y alertas. |
| Optimizer / Learning | Compara resultados, propone `ChangeProposal` y mantiene champion/challenger. | Aprendizaje y Auditoría. |

## Pantallas de la aplicación nativa

### Resumen

Debe responder en una sola vista: cuánto capital hay, PnL realizado/no realizado,
coste de IA, nivel de protección, suelo protegido, capacidad de pérdida, calidad
del mercado, posiciones, agentes, actividad y alertas. El modo visible es
`SIMULACIÓN`, `SHADOW` o `LIVE BLOQUEADO`; la marca temporal UTC queda en la barra
inferior.

### Mercados

Watchlist de símbolos permitidos, último precio, spread, volumen, frescura, estado
del feed, régimen, score técnico y eventos detectados. No debe mostrar un dato como
operable si está stale o sin fuente verificable.

### Operaciones

Historial de órdenes/fills reales o simulados: símbolo, lado, tamaño, entrada,
salida, fees, slippage, PnL bruto/neto, decisión, riesgo y enlace de auditoría.
Esta es la pantalla para revisar operaciones realizadas; `Posiciones` solo muestra
exposición abierta.

### Agentes

Estado por agente, versión, última ejecución, latencia, proveedor usado, errores,
score, evidencia y decisiones. El detalle nunca expone prompts completos, tokens ni
secretos.

### Riesgo

Daily/weekly loss, ladder, giveback, streak, drawdown, exposición, correlación,
suelo protegido, SAFE MODE y razón exacta de cada bloqueo.

### Proveedores

Tarjetas de Claude, Codex/OpenAI, Grok/xAI y Gemini. Una sola tarjeta es principal;
las demás quedan ordenadas como fallback. Cada tarjeta muestra login oficial,
comprobación, modo suscripción/API, perfil, contexto, sesión, cuatro horas, semana,
fuente de evidencia y timestamp. `UNKNOWN` es correcto cuando el proveedor no ofrece
telemetría oficial; nunca se adivina un límite.

### Fuentes

Fuente, tipo, proveedor, URL/identificador revisado, permiso, última ejecución,
frescura, registros, deduplicación, sanitización, backoff y error. El contenido
externo se presenta como datos no confiables, nunca como instrucciones.

### Paper / Shadow

Modo actual, simulador, propuestas rechazadas simuladas, fees, slippage, fill
hipotético y comparación posterior con una operación real. No debe insinuar que una
simulación es una orden enviada.

### Backtest

Experimento, estrategia/versión, observaciones, periodo, train/validation/test,
resultado OOS, fees, slippage, drawdown, supuestos y procedencia del dataset.
Backtest positivo no autoriza LIVE.

### Aprendizaje

Expectancy, win rate, profit factor, drawdown, MFE/MAE, coste, valor incremental
por agente, propuestas de cambio y transición manual. Nunca aparece un botón de
auto-promoción sin evidencia y revisión.

### Auditoría

Readiness, métricas, eventos, trazas de decisión, proveedores, alertas, retención,
copias y plan audit. Debe poder reconstruirse por qué una propuesta fue aceptada o
rechazada.

### Configuración

Capital, símbolos, exchange/sandbox, riesgo, proveedor principal/fallbacks, claves
por Keychain, feeds RSS/X/Reddit, intervalos, presupuesto de IA, notificaciones y
copias. Los secretos se escriben una vez y no se vuelven a mostrar.

## Estado honesto actual

El plan auditado queda como `PASS` offline: los contratos PAPER/SHADOW, tests y UI
están implementados. Las filas `FOUNDATION` y `GATED` de
la matriz interna del plan siguen requiriendo pruebas
autenticadas o infraestructura externa (exchange sandbox, PostgreSQL/restore,
telemetría oficial de cuotas, alertas remotas y aceptación independiente). Ninguna
de esas filas puede cerrarse cambiando una bandera local.
