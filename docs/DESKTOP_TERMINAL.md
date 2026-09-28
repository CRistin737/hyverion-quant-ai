# Hyverion Quant AI — App de escritorio

La interfaz local de Hyverion Quant AI es una app de escritorio **Tauri 2 + React 19/TypeScript**
en `app/`:

- `app/src`: frontend (pantallas, componentes, cliente del API, fixtures).
- `app/src-tauri`: shell Rust (arranque del core, proxy del API, Llavero).

El core Python (`trading_bot api`) sigue siendo el backend y el único dueño de las decisiones
financieras. La UI nunca envía órdenes; PAPER es el modo por defecto y se muestra como
`SIMULACIÓN`.

## Requisitos

- `uv` y las dependencias del core: `uv sync --extra dev`
- Node.js con `pnpm`
- Toolchain de Rust (para Tauri)

## Ejecutar en desarrollo

```bash
cd app
pnpm install
pnpm tauri dev
```

En desarrollo el shell arranca el core desde el repositorio con
`uv run python -m trading_bot api --port <aleatorio>`. Para usar un core iniciado a mano, define
`HYVERION_API_BASE` y `CONTROL_API_TOKEN` antes de `pnpm tauri dev`.

## Desarrollo en el navegador con fixtures

Para trabajar la UI sin core ni ventana nativa:

```bash
cd app
pnpm dev
```

Abre `http://localhost:1427/?fixture=demo`. Fixtures disponibles:

| Fixture | Uso |
|---|---|
| `demo` | Cuenta con datos realistas |
| `first-run` | Primer arranque: muestra el onboarding |
| `empty` | Cuenta configurada sin operaciones ni historial |
| `offline` | Core inaccesible: estado de error recuperable |

Sin `?fixture=`, el modo navegador se conecta por HTTP y WebSocket a un core real usando
`VITE_API_BASE` y `VITE_API_TOKEN`; el core solo acepta orígenes loopback declarados en
`CONTROL_API_EXTRA_ORIGINS` (por ejemplo `http://localhost:1427`). Este modo es solo para
desarrollo.

## Tests

```bash
cd app
pnpm test          # Vitest (formato de cadenas Decimal)
pnpm test:visual   # Playwright: 17 rutas + onboarding, offline, paleta y modo, en 4 tamaños
cd src-tauri
cargo test         # lista permitida de secretos, rutas del proxy y token
```

El core se verifica con `uv run ruff check .`, `uv run mypy src` y `uv run pytest`.

## Modelo de seguridad

- **Core como proceso hijo.** El shell Rust arranca `trading_bot api` en un puerto loopback
  aleatorio con un token de 256 bits generado en cada arranque (`CONTROL_API_TOKEN`).
- **Watchdog.** El shell pasa `HYVERION_SHELL_PID`; si el shell muere, el core se detiene solo. El
  core corre en su propio grupo de procesos y el grupo entero se termina al salir.
- **Sin red en el webview.** Toda llamada al API pasa por el comando Rust `api_request`: solo
  métodos GET y POST y solo rutas que empiezan por `/api/v1/` o `/health/`. El token nunca llega a
  JavaScript. La CSP del webview solo permite IPC.
- **Arranque seguro.** La UI no se monta hasta que `/health/ready` responde.
- **Datos en vivo.** En escritorio el snapshot se consulta cada 2 s a través del proxy. El WebSocket
  `/api/v1/stream` (token en el subprotocolo `hyverion.bearer.<token>`) solo se usa en el modo
  navegador de desarrollo.
- **Secretos.** Rust los escribe directamente en el Llavero de macOS (servicio `hyverion-quant-ai`).
  Solo se permiten los nombres `exchange:*`, `source:*` y
  `provider:{anthropic,openai,xai,gemini}:api_key`. La UI solo consulta si existen; los valores
  nunca viajan por HTTP ni vuelven a mostrarse.
- **API de control.** La autenticación es obligatoria (fail closed): `trading_bot api` no arranca sin
  `CONTROL_API_TOKEN`. CORS no admite orígenes de navegador por defecto.

## Datos

La ubicación de los datos no cambia: `~/Library/Application Support/Hyverion Quant AI/` para la app
empaquetada (ver `docs/MACOS_APP.md`) y `config/local.yaml` / `data/` del repositorio para la CLI.

## Modos sin interfaz

El API JSON/WebSocket funciona sin UI (el token es obligatorio):

```bash
CONTROL_API_TOKEN=<token> uv run python -m trading_bot api --host 127.0.0.1 --port 8787
```

Un bind remoto requiere además `API_ALLOW_REMOTE_BIND=true` y debe quedar detrás de una red privada.
La terminal Rich sigue disponible para recuperación por SSH:

```bash
uv run python -m trading_bot terminal
```

## Release y abrir la app

```bash
cd app
pnpm build:app     # sidecar PyInstaller + tauri build (verificado el 2026-09-25)
```

El bundle queda en `app/src-tauri/target/release/bundle/macos/Hyverion Quant AI.app`. Luego
`uv run python -m trading_bot desktop` abre la app instalada (`/Applications`, `~/Applications` o el
bundle de release del repositorio) o indica cómo compilarla. Los iconos de marca se regeneran con
`cd app && pnpm icons`. Detalles en `docs/MACOS_APP.md`.

La app PySide6 anterior (`src/trading_bot/desktop/`) se eliminó el 2026-09-25; la app Tauri es la
única app de escritorio.
