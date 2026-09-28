# Inventario de componentes

**Decisiones:** KEEP · RESTYLE · REFACTOR · REPLACE · MERGE · REMOVE

Todos los componentes vivían en `src/trading_bot/desktop/app.py` (eliminado el 2026-09-25) salvo indicación. El frontend pasa a
Tauri + React, así que la mayoría de widgets Qt se **REPLACE** por equivalentes React. El objetivo de
migración es el comportamiento, no el código.

| Componente | Ubicación | Uso actual / problema | Decisión | Destino de migración |
|---|---|---|---|---|
| `ControlApiClient` | ~982 | Cliente httpx; respuestas `dict` sin tipos | REPLACE | `app/src/api/client.ts`, tipado; la llamada pasa por el comando Rust `api_request` (el token no llega a JavaScript) |
| 13 × `*Task` QRunnable | 1215–1418 | Duplican `ClientCallTask` | REMOVE | TanStack Query `useQuery`/`useMutation` |
| `_x` / `_x_succeeded` / `_x_failed` + `*_in_flight` | en todo el archivo | Estado de mutación hecho a mano ×12 | REMOVE | Estados de `useMutation` |
| `QTimer` de snapshot de 2 s | 1722 | Polling sin backoff | REPLACE | Escritorio: polling cada 2 s vía proxy Rust. Navegador (dev): WS `/api/v1/stream` con polling de respaldo |
| `MarketChart` | 1420 | Gráfico de líneas QPainter, colores fijos | REPLACE | `lightweight-charts` velas/línea con colores de tokens |
| `PnlChart` | 1534 | Barras/línea QPainter | REPLACE | Componente SVG pequeño de área/línea |
| Pestañas `navButton` + menú "More" | 1756–1800 | 5 visibles, 10 ocultas | REPLACE | Barra lateral (6 dominios) + pestañas en página |
| Widgets de fila de métricas | Overview | Cajas KPI sin comparación | REFACTOR | `Metric` (valor, etiqueta, periodo, comparación) solo donde responde una pregunta |
| Marcos de panel (QFrame con borde) | en todas partes | Todo encajonado | REMOVE | Jerarquía de superficies + encabezados `Section` |
| Usos de `QTableWidget` | ~20 | Sin orden, columnas recortadas | REPLACE | `DataTable` (TanStack Table), cabecera fija, números alineados a la derecha |
| Tarjetas de proveedor | 2280–2430 | Concepto correcto | RESTYLE | Lista `ProviderRow` |
| Formulario heredado oculto de proveedores | ~2433 | Duplica las tarjetas | REMOVE | — |
| Pestañas de Settings (QTabWidget) | 2802 | 5 pestañas de formularios densos | REFACTOR | Navegación por secciones + filas |
| Botones de llavero | Settings/Providers | Escritura directa en `KeyringSecretStore` | REPLACE | Comandos Tauri `secret_set`/`secret_status`/`secret_delete` (mismo servicio de llavero `hyverion-quant-ai`) |
| `QTextBrowser` de Guides | 3012 | Manual largo | MERGE | Ajustes › Ayuda + popovers `HelpHint` en contexto |
| Diccionarios de traducción | 88–798 | 700 líneas EN→ES en código | REPLACE | `app/src/i18n/labels.ts` (textos visibles en español) |
| `_stylesheet()` | 5249 | 49+ colores | REPLACE | `app/src/styles/tokens.css` |
| Panel Rich de `terminal.py` | `src/trading_bot/terminal.py` | Construcción duplicada del snapshot | KEEP + REFACTOR | Renderizar la salida de `build_snapshot()` |
| Alias `dashboard/` | `src/trading_bot/dashboard/` | Stub de compatibilidad de 14 líneas | REMOVE | Eliminado; usar `create_control_api` |

## Primitivas nuevas (UI PHASE 3/5)

Viven en `app/src/ui/` (`button.tsx`, `form.tsx`, `overlay.tsx`, `nav.tsx`, `data.tsx`,
`feedback.tsx`, `toast.tsx`, `charts.tsx`, `graph.tsx`, `markdown.tsx`).

- **Entradas y acciones:** Button (primary / secondary / ghost / destructive), IconButton, Input,
  NumberInput (cadena Decimal), Textarea, Select, Combobox, Checkbox, Radio, Switch.
- **Capas:** Tooltip, Popover, DropdownMenu, Dialog, Sheet.
- **Estructura:** Tabs, SegmentedControl, Kbd, CommandPalette.
- **Estado y feedback:** Badge, StatusDot, Skeleton, EmptyState, ErrorState, Toast.
- **Datos:** DataTable, Metric, Section, SettingsRow.
- **Dominio:** formateadores Money / Pct / Qty (`app/src/lib/format.ts`), `ModeBadge`
  (SIMULACIÓN / REAL BLOQUEADO), visor Markdown de la Bóveda.
