# Hyverion Quant AI en macOS

La app de macOS es el shell Tauri 2 de `app/` (bundle id `ai.hyverion.quant`, macOS 12 o superior).
Arranca el core Python como proceso hijo y muestra la interfaz React. Detalles de arquitectura y
seguridad en `docs/DESKTOP_TERMINAL.md`.

## Identidad

- **Símbolo "Candle":** una H formada por tres velas, monocromo.
- **Icono de la app:** placa clara con el símbolo en carbón (decisión del dueño, 2026-09-25).
- Fuentes vectoriales: `assets/brand/hyverion-icon.svg` (icono con placa),
  `assets/brand/hyverion-mark.svg` (glifo claro para fondos oscuros) y
  `assets/brand/hyverion-mark-mono.svg` (`currentColor`).

Todos los recursos rasterizados de marca se regeneran con un solo comando:

```bash
cd app
pnpm icons
```

El script Node `app/scripts/build-icons.mjs` (con `@resvg/resvg-js`) renderiza `assets/brand/*.svg` a
`assets/hyverion-quant-ai-icon.png`, `assets/hyverion-quant-ai-mark.png`,
`assets/hyverion-quant-ai-icon.icns` (vía `iconutil`) y el juego de iconos Tauri de
`app/src-tauri/icons/`.

## Ejecutar durante desarrollo

```bash
uv sync --extra dev
cd app
pnpm install
pnpm tauri dev
```

La ventana usa la barra de título superpuesta (`titleBarStyle: Overlay`): los semáforos de macOS se
dibujan sobre el extremo izquierdo de la barra superior (84 px reservados). El sistema sigue en modo `PAPER` (`SIMULACIÓN`).

## Crear la app `.app` (verificado)

La versión distribuible empaqueta el core como sidecar PyInstaller llamado `hyverion-core`, que el
shell busca junto a su ejecutable. Un solo comando hace todo:

```bash
cd app
pnpm build:app
```

1. `scripts/build-core.sh` compila el sidecar con PyInstaller (`packaging/hyverion_core.spec` +
   `packaging/core_entry.py`, extra `desktop-build`) y lo copia a
   `app/src-tauri/binaries/hyverion-core-<triple>` (por ejemplo
   `hyverion-core-aarch64-apple-darwin`).
2. `tauri build --config src-tauri/tauri.release.conf.json` genera el bundle en
   `app/src-tauri/target/release/bundle/macos/Hyverion Quant AI.app` (~104 MB).

**Estado:** build de release verificado el 2026-09-25: la app se abre, el sidecar del core arranca y
al salir no quedan procesos huérfanos.

`uv run python -m trading_bot desktop` abre la app instalada: busca en `/Applications`,
`~/Applications` y después en la ruta del bundle de release del repositorio; si no la encuentra,
indica cómo compilarla.

No se incluye ninguna credencial en el bundle. LIVE continúa bloqueado por las reglas de seguridad
del proyecto.

## Secretos

Las claves de exchange, fuentes y proveedores de IA se guardan en el Llavero de macOS (servicio
`hyverion-quant-ai`) directamente desde el shell Rust. Nunca se escriben en disco ni viajan por HTTP.
Se pueden revisar en la app Acceso a Llaveros.

## Datos persistentes

La app empaquetada no escribe dentro del bundle. Su configuración, SQLite y memoria viven en:

```text
~/Library/Application Support/Hyverion Quant AI/config/
~/Library/Application Support/Hyverion Quant AI/data/trading_bot.db
~/Library/Application Support/Hyverion Quant AI/parquet/
~/Library/Application Support/Hyverion Quant AI/knowledge/          # Bóveda de conocimiento
~/Library/Application Support/Hyverion Quant AI/models/all-MiniLM-L6-v2/
```

El modelo de embeddings se instala con
`uv run python scripts/fetch_embedding_model.py --target "$HOME/Library/Application Support/Hyverion Quant AI/models/all-MiniLM-L6-v2"`
(o copiando `data/models/all-MiniLM-L6-v2`; sus hashes se verifican al cargar).

La bóveda se lee y se anota dentro de la app (Aprendizaje › Memoria › Notas y grafo). Obsidian es opcional: la
carpeta `knowledge/` sigue siendo Markdown exportado y se puede abrir con él.

La CLI ejecutada desde el checkout usa `config/local.yaml` y `data/trading_bot.db` del repositorio.
Esta separación evita que una prueba o un cambio de código sobrescriba el estado de la instalación.
No mezcles bases de ambos perfiles manualmente.
