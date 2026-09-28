# Retención y archivo del control plane

Hyverion conserva las proyecciones financieras y de ejecución necesarias para
reconstruir una decisión. Las tablas de alto volumen se archivan y purgan con
una política explícita; nunca se purgan automáticamente órdenes, fills,
posiciones, cuentas ni `daily_pnl`.

## Exportar antes de purgar

```bash
uv run python -m trading_bot retention --export backups/audit-2026-09-15.jsonl
```

El comando crea el JSONL y un manifiesto contiguo con conteos y SHA-256. No
sobrescribe archivos existentes. El payload ya debe haber pasado por los
límites de sanitización de cada fuente; el archivo debe tratarse como un
artefacto sensible y almacenarse fuera de Git.

## Revisar la purga

```bash
uv run python -m trading_bot retention --purge
```

La salida es `RETENTION_DRY_RUN` y solo muestra cuántas filas serían elegibles.
Para aplicar una purga revisada de forma explícita:

```bash
uv run python -m trading_bot retention --purge --apply
```

La política inicial conserva snapshots 14 días, candles 90 días, features y
social 30 días, noticias/source runs 90/180 días y eventos, alertas, señales y
revisiones el tiempo suficiente para auditoría. Los valores están versionados
en `DEFAULT_RETENTION_DAYS` y deben cambiarse con pruebas y evidencia de
restauración.

## Restauración y VPS

Antes de usar una exportación como evidencia operativa, verifica el manifiesto
SHA-256 y conserva también una copia SQLite creada con:

```bash
uv run python -m trading_bot backup
```

La retención remota, PostgreSQL/Timescale, cifrado de archivos y un ensayo de
restauración en un VPS siguen siendo gates de producción. La aplicación local
no sube datos a un proveedor externo por defecto.
