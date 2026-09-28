# Hyverion Quant AI — visión de plataforma autónoma

**Estado:** visión aprobada para evolución incremental  
**Fecha:** 2026-09-15  
**Alcance actual:** PAPER/SHADOW; LIVE continúa deshabilitado

## Objetivo del producto

Hyverion deja de concebirse como un bot que genera señales y pasa a ser una
plataforma financiera cuantitativa extensible: un cerebro de operaciones capaz de
observar, razonar, recordar, evaluar, aprender y mejorar su propio software bajo
controles verificables.

La autonomía no elimina las fronteras de seguridad. La arquitectura conserva dos
autoridades independientes:

```text
DATA -> AGENTS -> TRADE PROPOSAL -> CRITIC -> RISK ENGINE -> EXECUTION ENGINE -> EXCHANGE
```

- Los agentes analizan y proponen; nunca controlan directamente el dinero.
- `RiskEngine` conserva autoridad matemática final y no acepta instrucciones LLM.
- `ExecutionEngine` sigue siendo la única frontera con endpoints privados.
- El agente de desarrollo no puede importar clientes privados del exchange,
  modificar balances, enviar órdenes ni debilitar reglas de riesgo.
- LIVE no se activa como consecuencia de una mejora automática.

## Autodesarrollo gobernado

Hyverion incorporará un único `Development Supervisor`, coordinado por el Master
Orchestrator pero aislado del plano financiero. Su trabajo será detectar problemas
y oportunidades de mejora, y preparar cambios de software completos.

Puede proponer:

- correcciones de errores;
- nuevas fuentes, estrategias o capacidades;
- mejoras de rendimiento, observabilidad, recuperación y seguridad;
- tests y documentación;
- mejoras de UI/UX;
- refactors con evidencia de necesidad;
- retiro de componentes que no demuestren valor.

No puede:

- editar directamente el checkout activo;
- autoaprobar o desplegar su propio cambio;
- escribir en producción ni modificar datos financieros;
- acceder a secretos, credenciales de retiro o clientes privados del exchange;
- cambiar límites de riesgo para perseguir objetivos de PnL;
- ocultar un fallo de pruebas, auditoría o replay;
- hacer `push`, publicar releases o habilitar LIVE automáticamente.

### Flujo de cambio obligatorio

```text
OBSERVATION
  -> DEVELOPMENT PROPOSAL
  -> THREAT / IMPACT ANALYSIS
  -> ISOLATED WORKTREE OR SANDBOX
  -> IMPLEMENTATION
  -> STATIC CHECKS + TESTS + REPLAY + SECURITY REVIEW
  -> CHAMPION / CHALLENGER EVALUATION
  -> HUMAN REVIEW
  -> APPROVED DEPLOYMENT
  -> MONITORING
  -> ROLLBACK OR PROMOTION
```

La autonomía futura podrá ejecutar pasos reversibles dentro de un entorno aislado,
pero la promoción a producción y cualquier efecto sobre LIVE permanecerán tras una
política explícita. Inicialmente `AUTO_PROMOTE_AGENT_CHANGES=false`.

### DevelopmentProposal

Cada propuesta de software debe registrar como mínimo:

- `id`, `title`, `problem`, `proposed_change`;
- evidencia y fuentes que originaron la propuesta;
- módulos, archivos y contratos afectados;
- permisos/herramientas solicitados;
- impacto financiero, de seguridad, datos y privacidad;
- plan de implementación y migración;
- tests, replay y criterios de aceptación;
- riesgo de regresión y plan de rollback;
- proveedor/modelo/agentes participantes;
- estado, timestamps, commits y resultados de validación.

Estados iniciales:

`PROPOSED -> TRIAGED -> APPROVED_FOR_SANDBOX -> IMPLEMENTING -> TESTING ->
READY_FOR_REVIEW -> APPROVED -> DEPLOYED -> MONITORING -> COMPLETED`

Rutas de salida: `REJECTED`, `FAILED`, `ROLLED_BACK`, `RETIRED`.

## Fronteras de acceso por agente

Los agentes nunca reciben acceso general a scripts, filesystem, memoria o datos.
El Master Orchestrator construye un contexto mínimo mediante contratos tipados y
un manifiesto de capacidades por agente.

| Agente | Puede leer | Puede producir | Acceso prohibido |
|---|---|---|---|
| Market | snapshots, OHLCV, trades, order book, frescura | `MarketAssessment` | secretos, órdenes, filesystem |
| Technical | features numéricas y series autorizadas | `TechnicalAssessment` | clientes privados, noticias sin sanitizar |
| Regime | features, volatilidad y contexto de evento | `RegimeAssessment` | ejecución y cambios de configuración |
| News | contenido público sanitizado y metadatos | `NewsAssessment` | shell, credenciales, instrucciones externas |
| Social | contenido público legal y sanitizado | `SocialAssessment` | cuentas privadas, ejecución, filesystem |
| Derivatives | funding/OI/liquidaciones confiables | `DerivativesAssessment` | invención de datos, leverage automático |
| Strategy | cápsula aprobada de assessments y memoria | `TradeProposal` / `NO_TRADE` | órdenes y clientes del exchange |
| Critic | propuesta, evidencia y contradicciones | `CriticAssessment` | aprobar riesgo o ejecutar |
| Position Manager | estado reconciliado, tesis y mercado | `PositionDecision` | retirar protección o enviar órdenes |
| Session Guardian | PnL, exposición, calidad y presupuesto | `SessionDecision` | reactivar una sesión bloqueada |
| Optimizer | resultados agregados y evaluaciones | `ChangeProposal` | editar prompts/código en producción |
| Development Supervisor | repositorio sanitizado y propuesta aprobada | patch aislado + evidencia | exchange privado, secrets, LIVE, autoaprobación |

Cada capability se concede por tarea, se audita y expira. Los outputs no tipados o
las peticiones de herramientas fuera del manifiesto fallan de forma cerrada.

## Ciclo de vida completo de una operación

Ninguna operación abierta puede quedar olvidada aunque fallen la IA, la aplicación
o la red. El estado canónico será reconciliable y conducido por eventos.

```text
PROPOSED -> CRITIC_REVIEWED -> RISK_APPROVED -> EXECUTION_PENDING
 -> SUBMITTED -> ACKNOWLEDGED -> PARTIALLY_FILLED -> OPEN
 -> EXIT_PENDING -> CLOSING -> CLOSED -> RECONCILED -> EVALUATED
```

Estados de excepción: `REJECTED`, `CANCEL_PENDING`, `CANCELED`, `UNKNOWN`,
`SAFE_MODE`, `RECOVERY_REQUIRED`.

Invariantes:

- Un timeout nunca se interpreta como orden fallida sin consultar el exchange.
- `client_order_id` e idempotency key impiden órdenes duplicadas.
- Una posición abierta se sigue determinísticamente hasta `CLOSED + RECONCILED`.
- Stops, límites, protección de beneficio y emergency exits no dependen del LLM.
- Al reiniciar se reconcilian balances, órdenes, posiciones y fills recientes.
- Cualquier discrepancia activa SAFE MODE y bloquea nuevas entradas.
- Después del cierre se calculan PnL neto, fees, slippage, MFE, MAE y eficiencia de
  salida antes de alimentar evaluación, shadow y memoria.

## Memoria como propiedad de Hyverion

El conocimiento pertenece a Hyverion, no a Claude, Codex, Grok ni Gemini. La
arquitectura futura se divide en:

1. Working memory: Redis reconstruible para el estado de baja latencia.
2. Historical memory: PostgreSQL/Timescale como fuente de verdad, Parquet/DuckDB
   para analítica y pgvector para recuperación semántica.
3. Strategic memory: Knowledge Vault Markdown compatible con Obsidian, versionado
   y auditable.

La implementación de la memoria se documenta en
[`docs/MEMORIA.md`](../docs/MEMORIA.md).

## Dirección de UI/UX

La interfaz nativa actual permanece como base. La siguiente evolución debe aplicar
el patrón del Centro de proveedores al resto del terminal:

- una acción primaria clara por entidad;
- estado legible sin abrir formularios;
- configuración progresiva, no grupos de botones equivalentes;
- detalle bajo demanda y guías contextuales;
- loading, empty, degraded, failed y healthy visibles;
- tablas compactas con drill-down para operaciones, fuentes, agentes y memorias;
- lenguaje natural en español, sin términos internos innecesarios;
- densidad informativa de terminal profesional sin sacrificar jerarquía;
- riesgos, discrepancias y datos obsoletos visibles antes que información decorativa.

La futura sección **Memoria** mostrará salud de las tres capas, candidatos,
conocimiento activo/retirado, conflictos, confiabilidad por agente, usos y evidencia.

## Secuencia de evolución

### Etapa A — Consolidación y observabilidad

- completar reconciliación sandbox y ciclo de vida de operaciones;
- fortalecer fuentes, streaming, gaps y alertas;
- mejorar progresivamente UI/UX y trazas de decisión;
- ensayar PostgreSQL, backups y restauración.

### Etapa B — Memoria de tres capas

- interfaces y migraciones no destructivas;
- Redis e reconstrucción;
- PostgreSQL/Timescale/pgvector y archivos Parquet;
- Knowledge Vault y versionado;
- Memory Gateway, retrieval, capsules y curator;
- decay, contradicciones, meta-memory y evaluación contrafactual.

### Etapa C — Development Supervisor

- propuestas tipadas y permisos mínimos;
- ejecución en worktree/contenedor aislado;
- gates de código, tests, seguridad, replay y migraciones;
- UI de revisión, diff, evidencia y rollback;
- métricas sobre el valor real de los cambios.

### Etapa D — Autonomía empresarial controlada

- scheduling 24/7 y recuperación distribuida;
- champion/challenger operacional;
- promoción gradual solo bajo políticas explícitas;
- observabilidad, backups, disaster recovery y auditorías independientes;
- escalado horizontal de collectors, agents y memoria sin alterar las fronteras de
  riesgo y ejecución.

## Criterio de éxito

Hyverion será considerado autónomo cuando pueda detectar una situación, recuperar
estado y experiencia relevante, producir y criticar una propuesta, aplicar riesgo
determinista, seguir el ciclo de vida completo, evaluar el resultado, proponer una
mejora verificable, implementarla en aislamiento, demostrarla con evidencia y
presentarla para revisión con rollback; todo sin perder trazabilidad ni permitir
que un LLM controle capital o se conceda autoridad a sí mismo.
