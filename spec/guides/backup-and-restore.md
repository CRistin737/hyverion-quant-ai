# Backup and restore del control plane

## Backup local

El control plane local usa SQLite/WAL. Crea una copia consistente mientras la
app sigue en PAPER con:

```bash
uv run python -m trading_bot backup
```

La copia se guarda en `backups/` con timestamp UTC. Para elegir una ruta:

```bash
uv run python -m trading_bot backup --destination /ruta/segura/hyverion.db
```

El comando usa SQLite Online Backup, valida `PRAGMA integrity_check` y solo
devuelve `BACKUP_VERIFIED` después de leer correctamente la copia. No elimina,
reemplaza ni imprime secretos.

## Restauración local controlada

1. Detén la app nativa y el control API.
2. Conserva el archivo actual con un nombre de rollback fuera de `backups/`.
3. Verifica la copia elegida con `sqlite3 <copia> 'PRAGMA integrity_check;'`.
4. Sustituye el archivo de datos usando una operación atómica del sistema de
   archivos, ajustando la ruta al perfil de macOS o al volumen Docker.
5. Ejecuta `uv run python -m trading_bot doctor` antes de abrir la app.
6. Revisa `/health/ready`, reconciliación y `protection_recovery`; cualquier
   diferencia entra en SAFE MODE.

No se ofrece restauración automática porque una copia equivocada puede borrar
estado operativo. PostgreSQL/Timescale, secret-manager y backup remoto siguen
siendo una aceptación separada del perfil VPS.
