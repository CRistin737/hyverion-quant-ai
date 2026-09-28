# Registro de cambios de la UI

Cambios mayores del remaster de la interfaz (2026-09-25). Cada entrada sigue el formato
Antes / Problema / Decisión / Después / Pantallas afectadas.

## 1. Toolkit: PySide6 → Tauri 2 + React 19

- **Antes:** una app PySide6 de 5.398 líneas en un solo archivo (`src/trading_bot/desktop/app.py`),
  QSS global, gráficos pintados con QPainter.
- **Problema:** sin capa de UI compartida, estado ad hoc, imposible de testear visualmente y lento de
  evolucionar.
- **Decisión:** Tauri 2 + React 19/TypeScript en `app/` (frontend `app/src`, shell Rust
  `app/src-tauri`). El core Python sigue siendo el backend.
- **Después:** componentes reutilizables en `app/src/ui`, pantallas por dominio en `app/src/screens`,
  TanStack Query para datos, tests Vitest, Playwright y `cargo test`. La app PySide6 se eliminó
  el 2026-09-25 (junto con `scripts/render_native_ui.py`, `scripts/build_brand_assets.py` y su spec
  PyInstaller); los iconos se regeneran con `cd app && pnpm icons` y el build de release
  (`cd app && pnpm build:app`) está verificado.
- **Pantallas afectadas:** todas.

## 2. Modelo de seguridad del shell

- **Antes:** la UI Qt hablaba HTTP con el core y el token de control era opcional.
- **Problema:** un webview con acceso a la red y al token ampliaría la superficie de ataque.
- **Decisión:** el shell Rust arranca `trading_bot api` en un puerto loopback aleatorio con un token
  de 256 bits por arranque (`CONTROL_API_TOKEN`) y `HYVERION_SHELL_PID`. El webview nunca usa la red:
  toda llamada pasa por el comando Rust `api_request` (solo GET/POST, rutas `/api/v1/` y `/health/`).
  Los secretos se escriben en el Llavero de macOS desde Rust.
- **Después:** el token nunca llega a JavaScript; la autenticación del API es obligatoria y CORS no
  admite orígenes de navegador por defecto.
- **Pantallas afectadas:** arranque, Ajustes (Cuenta IA, Exchange, Fuentes), Onboarding.

## 3. Arquitectura de información: 15 pantallas → 6 dominios

- **Antes:** 5 pestañas visibles y 10 ocultas bajo "More".
- **Problema:** mala descubribilidad y conceptos relacionados repartidos.
- **Decisión:** Inicio; Trading; Inteligencia; Riesgo; Laboratorio; Ajustes, con pestañas internas.
- **Después:** barra lateral plegable, atajos ⌘1–⌘6, paleta ⌘K. (La barra lateral se sustituyó por
  navegación superior; ver entrada 9.)
- **Pantallas afectadas:** todas (ver `SCREEN_INVENTORY.md`).

## 4. Onboarding de primer arranque

- **Antes:** la app abría un Overview vacío; la configuración exigía `trading_bot setup` en la CLI.
- **Problema:** el producto no era usable sin terminal.
- **Decisión:** asistente de 4 pasos (Capital, Mercado, Inteligencia artificial, Límites de riesgo)
  basado en `GET /api/v1/setup/status` y validar → aplicar.
- **Después:** "Iniciar en modo simulación" deja la app lista en PAPER.
- **Pantallas afectadas:** Onboarding, Inicio.

## 5. Identidad visual

- **Antes:** verde `#24F79A` para marca y para ganancia, fondo `#000`, logo H con diamante de
  Ethereum.
- **Problema:** la marca y la semántica de PnL chocaban; aspecto de "demo hacker".
- **Decisión:** dirección técnica oscura (claro secundario); logo "Candle" (H de tres velas); icono
  con placa clara y símbolo carbón; acento Signal lime `#C6F24E` nunca para ganancia; PnL
  `#36D39A` / `#F0616D`; Geist + Geist Mono. (El lima se sustituyó por un acento monocromo; ver
  entrada 9.)
- **Después:** tokens en `app/src/styles/tokens.css`; números en Geist con cifras tabulares.
- **Pantallas afectadas:** todas; iconos de la app y del Dock.

## 6. Bóveda en la app

- **Antes:** la bóveda de conocimiento solo se podía leer con Obsidian.
- **Problema:** dependencia de una app externa para una función central.
- **Decisión:** pestaña Laboratorio › Bóveda con lectura de notas y edición **solo de la sección del
  dueño** (`POST /api/v1/memory/vault/note/owner`). Obsidian queda opcional.
- **Después:** navegación y lectura dentro de la app; exportación Markdown intacta.
- **Pantallas afectadas:** Laboratorio › Bóveda, Laboratorio › Memoria.

## 7. Modo siempre visible

- **Antes:** el modo solo aparecía en el pie.
- **Problema:** el modo debe ser inconfundible.
- **Decisión:** `ModeBadge` "SIMULACIÓN" en la cabecera y en la barra de estado persistente.
- **Después:** test visual que exige la insignia en cada dominio.
- **Pantallas afectadas:** todas.

## 8. Límites de riesgo visibles en solo lectura

- **Antes:** los límites solo se veían en el formulario de Settings.
- **Problema:** el operador no podía consultar los límites vigentes sin entrar a editar.
- **Decisión:** `config/public` incluye `risk` en solo lectura; Riesgo › Límites los muestra y la
  edición sigue en Ajustes › Riesgo con validar → aplicar.
- **Después:** consulta sin riesgo de edición accidental.
- **Pantallas afectadas:** Riesgo › Límites, Ajustes › Riesgo.

## 9. Paleta monocroma, navegación superior e Inicio visual

- **Antes:** acento Signal lime `#C6F24E`; barra lateral plegable de 224/76 px con los 6 dominios y
  atajo ⌘\ para plegarla; cabecera con título de página; Inicio como lista de métricas y tablas;
  pruebas visuales solo en tema oscuro.
- **Problema:** el lima no se parecía al logo monocromo; la barra lateral restaba ancho útil a
  gráficos y tablas; Inicio no respondía de un vistazo "¿qué importa ahora?"; el tema claro no tenía
  contraste AA garantizado ni capturas de referencia.
- **Decisión (dueño, 2026-09-25):**
  - Acento monocromo como el logo: blanco `#F4F5F7` en oscuro y carbón `#141518` en claro. El lima
    se eliminó en todas partes; PnL sigue usando `positive`/`negative`.
  - Tokens del tema claro ajustados para AA: `text-muted` `#5B606A`, `positive` `#07734F`,
    `negative`/`danger` `#B3263A`, `warning` `#7F5000`, `info` `#18589A`; en oscuro, `text-muted`
    `#878D98`. `app/src/styles/tokens.test.ts` exige ≥4.5:1 en ambos temas.
  - Navegación superior estilo TradingView (`app/src/shell/TopBar.tsx`; `Sidebar.tsx` eliminado):
    logo, 5 dominios (Inicio, Trading, Inteligencia, Riesgo, Laboratorio), búsqueda ⌘K, insignia
    SIMULACIÓN y Ajustes como icono. Debajo, fila de 44 px con pestañas de sección y acciones de
    página; el `h1` queda `sr-only`. Atajos ⌘1–⌘6 y ⌘K; el atajo ⌘\ ya no existe.
  - Inicio con composición visual propia (DESIGN_SPEC "Inicio (composición, v1.2)"): tarjetas de
    atención, héroe de patrimonio con curva animada y periodos 7 D / 30 D / Todo, medidores radiales
    de protección, flujo animado del pipeline de decisión, tarjetas de mercado con minigráficos y
    posiciones + línea de tiempo de actividad. Componentes en `app/src/ui/viz.tsx`; animaciones
    desactivadas con `prefers-reduced-motion`.
- **Después:** DESIGN_SPEC v1.2; la app se ve monocroma como el logo; el contenido gana todo el ancho
  de la ventana; las pruebas visuales añaden el proyecto `1440x900-light` (cinco proyectos en total).
- **Pantallas afectadas:** todas (barra superior, pestañas y acento); Inicio rediseñado; iconos de la
  app sin cambios.

## 10. Simplificación: 6 áreas, 11 pestañas y cada cosa donde se ve (2026-09-27)

**Antes**
- 20 pestañas.
- Posiciones y Operaciones mostraban casi lo mismo, y la tabla de órdenes estaba duplicada.
- Los límites se veían en Riesgo pero se editaban en Ajustes.
- Había dos pestañas «Fuentes».
- Cuenta IA estaba en Ajustes y Modelos en Inteligencia.
- «Probar estrategias» y Bóveda eran pestañas propias.
- Inicio mostraba una línea verde difícil de leer.
- No había historial de versiones de los agentes, ni una forma clara de aceptar cambios.

**Problema.** El dueño no distinguía secciones, tenía que saltar entre pantallas para configurar y no
sabía dónde se aceptan las mejoras.

**Decisión (dueño, 2026-09-27)**

| Área | Pestañas | Cambio |
|---|---|---|
| Trading | Mercado · Operaciones · Órdenes | Operaciones une Posiciones y Operaciones; Órdenes gana KPIs, filtros y entradas frenadas por riesgo |
| Inteligencia | Agentes · Modelos · Fuentes | Agentes gana estrategias e historial de versiones; Modelos y Fuentes se editan en el sitio |
| Riesgo | Decisiones · Límites · Salud del sistema | Límites editables con ejemplos en dólares; Salud incluye la verificación de testnet |
| Aprendizaje (antes Laboratorio) | Cambios · Memoria | Bandeja de cambios con aprobar y aplicar, deshacer y buscar mejoras; Bóveda pasa a ser una vista de Memoria |
| Ajustes | una página | Cuenta de trading, apariencia y datos, ayuda |

- «Probar estrategias» sale de la app. Su motor lo usa el optimizador y sus pruebas aparecen como
  evidencia de cada cambio.
- Las direcciones antiguas redirigen a las nuevas.

**Gráficas.**
- `PriceChart` (`app/src/ui/charts.tsx`) ofrece velas, barras, Heikin Ashi, línea y área, de 1m a 1D,
  con leyenda OHLC, líneas de entrada, stop y objetivo, y marcadores de compra y venta.
- Inicio muestra velas de la última hora con un resumen en texto: «▲ +0,42 % en 1 h · máx · mín».

**Textos y movimiento.**
- Una frase de propósito por pestaña.
- `InfoHint` con un glosario en español.
- Entrada escalonada y destello al cambiar un valor, las dos desactivadas con `prefers-reduced-motion`.
- Una sola insignia SIMULACIÓN.

**Después.**
- 11 flujos e2e: aprobar y deshacer, fuentes, límites, gráfica, operaciones, órdenes y redirecciones.
- 15 rutas en 6 tamaños.
- Vitest para gráficas, KPIs y rutas.
- Contrato de tipos TS/núcleo ampliado.

**Pantallas afectadas:** todas. Las capturas están en `docs/ui/capturas/`.

## 11. Migración a QQQ: de cripto a un único instrumento (2026-09-27)

- **Antes:** la app mostraba monedas cripto (BTC/USDT, ETH/USDT), un exchange Binance en testnet y una sección de derivados.
- **Problema:** el sistema pasa a operar solo QQQ, con un broker intercambiable y el horario de la bolsa de Nueva York.
- **Decisión:** eliminar de la interfaz toda referencia a cripto y mostrar el instrumento, el broker y la sesión de mercado.
- **Después:**
  - Onboarding: el paso «Instrumento y broker» deja QQQ fijo («opera solo QQQ; el resto son sensores») y permite elegir entre simulador y Alpaca Paper.
  - Ajustes › Cuenta de trading: el broker, su estado (cuenta enmascarada y poder de compra) y las claves de Alpaca Paper en el Llavero.
  - Riesgo › Salud: «Cuenta paper del broker» sustituye a la verificación de testnet.
  - Barra de estado: la sesión en hora de Nueva York, por ejemplo «Mercado abierto · cierra 16:00 ET» o «Mercado cerrado · abre lun 09:30 ET».
  - Trading › Mercado: la lista se llama «Instrumentos» y marca SPY como «sensor»; el volumen es el de la sesión, en acciones.
  - Fuentes: el mercado muestra el proveedor y el feed (Alpaca IEX); sale la sección de derivados y las noticias cripto.
  - Datos de demostración: QQQ y SPY con precios realistas.
- **Pantallas afectadas:** onboarding, Ajustes, Riesgo, Trading, Fuentes, Inicio y la barra de estado.

## 12. Capital real de Alpaca, fuentes de datos y Centro QQQ (2026-09-28)

- **Antes:**
  - La app pedía un «capital de simulación» ficticio y permitía elegir el simulador interno como broker.
  - No mostraba calendario económico, amplitud del índice ni informes de la campaña.
- **Problema:** el patrimonio real de la simulación es el de la cuenta Alpaca Paper. Y para decidir hacen falta varias fuentes a la vez (Fed, BLS, BEA, SEC, noticias, volatilidad), configuradas tan fácil como Alpaca.
- **Decisión:**
  - El broker es el capital y el simulador queda solo como herramienta interna.
  - Las fuentes gratuitas se configuran en Ajustes con el patrón «pega la clave y pulsa Probar».
  - Todo lo que miran los sensores se reúne en un «Centro QQQ».
- **Después:**
  - **Onboarding:** el primer paso es conectar Alpaca Paper (claves y **Probar conexión**, obligatorio) y dar un email de contacto, que exigen la SEC y el BLS. Desaparece el paso de capital.
  - **Inicio:** «Patrimonio · Alpaca Paper PA…», con efectivo, poder de compra y la hora de la última sincronización. Sin broker, lleva a Ajustes.
  - **Ajustes:**
    - *Cuenta de trading*: fuera el capital y el simulador.
    - *Fuentes de datos*, nueva: una tarjeta por fuente con estado, clave, **Probar** y enlace para conseguir la clave gratis.
  - **Inteligencia › Centro QQQ** (pestaña por defecto):
    - estado de la regla macro con cuenta atrás al próximo evento de alto impacto;
    - amplitud del Nasdaq-100 y mapa de las megacaps;
    - calendario económico al estilo de Myfxbook, en hora de Nueva York;
    - tipos y volatilidad;
    - noticias agrupadas, resultados y documentos de la SEC.
  - **Aprendizaje › Informes:** el PnL del broker en paper y el ajustado a la realidad, siempre por separado, los motivos de no operar, las operaciones sombra, el coste de IA y la lista de preparación para real, que en esta versión siempre sale «No preparado».
- **Pantallas afectadas:** onboarding, Inicio, Ajustes, Riesgo › Límites, Inteligencia (nueva pestaña) y Aprendizaje (nueva pestaña).

## 13. App simplificada para la IA autónoma (2026-09-28)

- **Antes:** 6 módulos con Aprendizaje aparte, Centro QQQ dentro de Inteligencia, Órdenes separado de Operaciones, Salud del sistema en Riesgo, presupuesto de IA en dólares y topes de riesgo en USD.
- **Problema:** demasiadas pantallas para un sistema que opera solo; la información para el dueño (noticias) mezclada con la de los agentes; el presupuesto en USD siempre marcaba 0 con suscripción.
- **Decisión:**
  - Módulo **Noticias** (Calendario, Noticias con fuente de cada dato, Fuentes con «Necesita verificación»).
  - **Inteligencia** (icono nuevo) = Agentes (con modelo y rol), Modelos (barras de 5 h y semana, cambiar de cuenta, modelo por tarea), Aprendizaje (ideas de la IA, autopromociones con deshacer), Memoria (qué ha aprendido; detalles técnicos plegados).
  - **Trading** = Mercado, Operaciones (incluye órdenes y frenadas), **Informe** (día/mes/año, KPIs, CSV y PDF).
  - **Riesgo** = Límites (perfiles en % del patrimonio) y Decisiones.
  - **Ajustes** centrado, con **Salud del sistema** como modal («Arreglar» en cada fallo) y el interruptor del motor en segundo plano.
  - Botón **Modelo** en la barra superior; Inicio muestra «IA · 5 horas» e «IA · semana»; barras de scroll ocultas; ancho máximo 1920 px.
- **Pantallas afectadas:** todas las de navegación; direcciones antiguas redirigen.
