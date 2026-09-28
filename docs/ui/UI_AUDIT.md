# Auditoría de la UI — UI PHASE 0

Fecha: 2026-09-25 · Rama al auditar: `feat/operation-lifecycle` · Estado: COMPLETE

> Esta auditoría describe la app **anterior** (PySide6). La nueva app Tauri 2 + React vive en
> `app/`; ver `UI_CHANGELOG.md`.

## 1. Tecnología actual

| Aspecto | Estado actual |
|---|---|
| Toolkit | PySide6 Widgets (`QMainWindow`, `QStackedWidget`, `QTableWidget`, `QTabWidget`, `QTextBrowser`). Sin QML ni WebEngine. |
| Código | `src/trading_bot/desktop/app.py`: **5.398 líneas en un solo archivo**. `__init__.py` reexporta `launch_desktop`. |
| Router | Índice de `QStackedWidget` cambiado por `QPushButton` `navButton` marcables. Sin enlaces profundos. |
| Estado | Atributos de instancia ad hoc y banderas `*_in_flight`. Todo el snapshot se vuelve a pintar cada 2 s. |
| Datos | `ControlApiClient` (httpx, línea ~982) → FastAPI local `control_api.py`. La UI consulta `GET /api/v1/snapshot` con un `QTimer` de 2 s. `WS /api/v1/stream` existe pero no se usa. |
| Asincronía | Unas 13 clases `QRunnable` casi idénticas (líneas 1215–1418). El genérico `ClientCallTask` ya las cubre todas. |
| Estilos | Una sola cadena QSS global, `_stylesheet()` (líneas 5249–5322). 32 hex distintos más unos 17 dentro de `paintEvent` de gráficos. Sin tokens. |
| Fuentes | 'Avenir Next'/'Helvetica Neue' para texto, Menlo para números, 8–25 px fijos en código. |
| Gráficos | `MarketChart` (línea 1420) y `PnlChart` (línea 1534) pintados a mano con QPainter. |
| i18n | Diccionarios `TRANSLATIONS_ES` + `_ADDITIONAL_TRANSLATIONS_ES` (~700 líneas), ES por defecto. Algunos textos solo existen en español fijo en código. |
| Iconos | Sin sistema de iconos. Solo botones de texto; PNG del símbolo. |
| Formularios / validación | Widgets manuales. La validación ocurre en el servidor (`/config/validate`). |
| Tests | `tests/unit/test_desktop.py` (pytest-qt, 9 tests). `scripts/render_native_ui.py` genera PNG offscreen. `desktop/*` está excluido de cobertura. |
| Empaquetado | PyInstaller `packaging/hyverion_quant_ai.spec` → `dist/Hyverion Quant AI.app`, bundle id `ai.hyverion.quant`. |

## 2. Estructura de la aplicación

- **Barra superior:** símbolo de 30 px, título, 5 botones de navegación principal (Overview, Markets,
  Agents, Risk, Providers), un menú "More" con otras 10 páginas y una lectura de capital.
- **Pie:** estado de conexión, modo (`SIMULACIÓN`), hora de la última actualización.
- **Falta:** barra lateral, migas de pan, búsqueda global, paleta de comandos, perfil/espacio de
  trabajo, notificaciones. Existen atajos de teclado (`_install_shortcuts`, línea 3519).
- `spec/ui/terminal-information-architecture.md` prohibía el logo dentro de la ventana, pero el
  código lo mostraba. Su lista de "More" estaba desactualizada: omitía Operations, Memory y Guides.

## 3. Arquitectura de la UI

No hay una capa de UI compartida. Cada constructor de página (líneas 1899–3012) compone widgets Qt
directamente. `_page()` descarta sus argumentos `eyebrow`/`description`. Los métodos de render y de
manejo de eventos (~2.100 líneas) mezclan formato, traducción y mutación de widgets.

## 4. Pantallas existentes

Hay 15 pantallas: ver `SCREEN_INVENTORY.md`.

## 5. Componentes existentes

Ver `COMPONENT_INVENTORY.md`.

## 6. Problemas visuales

| Severidad | Problema |
|---|---|
| CRITICAL | El verde de marca `#24F79A` también significa "ganancia/ok": la marca y la semántica de PnL chocan. |
| CRITICAL | Sin sistema de tokens. 49+ colores, muchos verdes casi duplicados (`#24F79A`/`#26F794`/`#17E984`, cinco mentas pálidas). |
| HIGH | Fondo negro puro `#000000` con bordes verdes muy saturados: aspecto agresivo de "demo hacker" en lugar de software profesional. |
| HIGH | Todo bloque es un panel con borde; no hay jerarquía de superficies. |
| HIGH | Tamaños de texto de 8 a 25 px sin roles tipográficos. |
| HIGH | Las barras de desplazamiento horizontal están ocultas globalmente (`QScrollBar:horizontal{height:0}`), así que las tablas anchas se recortan en silencio. |
| MEDIUM | Gráficos pintados a mano: sin formato estándar de ejes, sin hover, sin crosshair. |
| MEDIUM | Sin iconos: las acciones solo se reconocen leyendo. |
| POLISH | Textos en idiomas mezclados, mayúsculas inconsistentes, parámetros `eyebrow`/`description` sin uso. |

## 7. Problemas de UX

- **Sin flujo de primer arranque.** La app empaquetada abre un Overview vacío. La configuración
  exige la CLI (`main.py setup`) y `run`/`paper` fallan con "run setup or pass --capital".
- **15 destinos de primer nivel** para un producto de un solo usuario. 10 están ocultos tras "More":
  mala descubribilidad.
- **Conceptos relacionados repartidos:** Markets / Positions / Operations; Agents / Providers /
  Sources; Backtest / Paper-Shadow / Learning / Memory; Risk / Audit.
- **Acciones duplicadas:** Providers tiene tarjetas nuevas y además un formulario heredado oculto con
  las mismas acciones.
- **Sin jerarquía de acción principal.** Todos los botones pesan lo mismo.
- **Estados vacíos y de error** son casi siempre tablas en blanco o cadenas de excepción en crudo.
- **El modo (PAPER) solo aparece en el pie.** Debe ser inconfundible.

## 8. Accesibilidad

- Sin estilo visible de anillo de foco; el QSS anula los valores por defecto de Qt.
- Contraste: el gris `#6E8B79` sobre `#000` queda por debajo de 4.5:1 en texto pequeño.
- Se usan textos de 8–10 px para etiquetas.
- No hay controles solo-icono, pero tampoco nombres accesibles para los gráficos pintados a mano.

## 9. Adaptación al tamaño de ventana

El tamaño mínimo es 1180×760. Por debajo de 1440 px de ancho las tablas se comprimen y el texto se
recorta porque el desplazamiento horizontal está oculto. No hay diseño compacto.

## 10. Lógica protegida (no debe cambiar)

- Los contratos de endpoints de `ControlApiClient` y la forma del snapshot: la nueva UI los consume.
- **Secretos:** la UI escribe las claves de proveedores, exchange y noticias directamente en el
  llavero del sistema mediante `KeyringSecretStore`. Nunca viajan por HTTP. La nueva UI debe
  conservar esa propiedad.
- El flujo de configuración siempre es validar → aplicar (`/config/validate`, `/config/apply`).
  Nunca escribir configuración sin validar.
- La lista permitida de inicio de sesión de proveedores (probada en `test_desktop.py`).
- La resolución por operador y las transiciones de aprendizaje son acciones auditables con motivo.
- La UI nunca envía órdenes ni toca clientes privados del exchange. PAPER es el modo por defecto.
  LIVE está bloqueado (`main.py live` termina con código 2).
- **Fuga de frontera preexistente (a corregir en la preparación del backend):**
  `control_api.py:1514` construía `BinanceSpotPrivateAdapter` para el endpoint de reconciliación
  sandbox de solo lectura. *Resuelto:* se movió a `src/trading_bot/exchange/sandbox_verification.py`.

## Decisión (resumen ADR)

El mantenedor eligió **Tauri 2 + React/TypeScript** como nuevo shell de escritorio. `control_api`
sigue siendo el backend, empaquetado como sidecar PyInstaller. Dirección visual técnica oscura
(tema claro secundario). La UI y el backend se consolidan donde sea seguro. Hay un logo nuevo; se
mantiene el nombre "Hyverion Quant AI".
