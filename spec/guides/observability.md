# Observabilidad, exportación y retención

Hyverion registra decisiones, fallos y contadores en UTC. La interfaz nativa
expone los contadores seguros en **Auditoría → Métricas del runtime**; no
expone prompts, secretos, respuestas completas de proveedores ni claves.

## Qué se registra

- ejecuciones y fallos de agentes;
- intentos y agotamiento de la cadena de proveedores;
- ciclos PAPER, rechazos de stream y fallos de ciclo;
- decisiones de riesgo y eventos de ejecución;
- ejecuciones de fuentes y estados de reconciliación;
- alertas operativas deterministas para reconciliación, protección, proveedores,
  stream y backup;
- coste de IA y tokens únicamente cuando el proveedor informa esos datos.

Los nombres y etiquetas están acotados. Una etiqueta con `secret`, `token`,
`password`, `credential` o `api_key` se descarta antes de llegar al UI o al
exportador.

## Auditoría de preparación

En **Auditoría → Auditoría de preparación** el operador puede ejecutar
`GET /api/v1/readiness`. El informe comprueba de forma determinista PAPER,
spot sin apalancamiento, base de datos, la puerta única `ModelRouter`, cuentas
de suscripción seleccionadas y recuperación de stops protectores. También
expone explícitamente las puertas todavía pendientes: mutaciones privadas del
exchange y autorización LIVE. `PAPER_READY` solo significa que el modo local
es coherente; `live_authorized` permanece siempre en `false` en esta versión.

## Exportación local/VPS

Con la API de control activa:

```bash
curl -fsS http://127.0.0.1:8787/api/v1/observability/metrics
curl -fsS 'http://127.0.0.1:8787/api/v1/observability/metrics?format=prometheus'
```

Si `CONTROL_API_TOKEN` está configurado, añade `Authorization: Bearer ...`.
El primer endpoint devuelve JSON para el terminal y el segundo usa una
exposición Prometheus acotada para un colector futuro en VPS. Un valor inválido
se omite y nunca rompe el endpoint ni habilita una operación.

## Retención

Los contadores runtime se guardan como eventos resumidos, no como ticks. La
API `GET /api/v1/observability/retention` y el botón **Revisar retención** en
Auditoría solo calculan candidatos. Para archivar y revisar una purga local:

```bash
uv run python -m trading_bot retention --export backups/audit.jsonl
uv run python -m trading_bot retention --purge
```

Solo una segunda ejecución explícita con `--purge --apply` muta las tablas
allowlisted. Nunca se tocan órdenes, fills, posiciones, cuentas ni `daily_pnl`.
La retención de producción debe exportar a un almacén externo antes de purgar
eventos locales y conservar, como mínimo, la trazabilidad de `decision_id`,
`trade_id`, `agent_run_id`, proveedor, timestamps, riesgo y ejecución. El
procedimiento de backup/restore PostgreSQL/secret-manager sigue siendo una
puerta de preparación VPS y no está aprobado por esta guía.

## Regla operativa

Una métrica describe lo ocurrido; nunca autoriza una orden. Ante falta de
telemetría, fallo del exportador o inconsistencia de base de datos, el motor
continúa en PAPER/SAFE MODE y las protecciones deterministas conservan la
autoridad.
