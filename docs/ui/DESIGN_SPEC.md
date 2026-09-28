# Hyverion Quant AI — Especificación de diseño (contrato visual canónico)

Versión 1.2 · 2026-09-25 · Implementación: `app/src/styles/tokens.css` y `app/src/styles/app.css`
(fuente única de verdad). Si el código y este archivo no coinciden, corregir uno de los dos en el
mismo cambio.

## 1–3. Filosofía, personalidad, tono
- **Filosofía.** Un panel de instrumentos para un sistema de trading autónomo: tranquilo mientras
  nada requiere al operador, inequívoco cuando algo sí. Modo: *Operar*. La familiaridad es una
  virtud y la herramienta desaparece dentro de la tarea.
- **Personalidad.** Precisa, tranquila, técnica, confiable. Ni "demo hacker" ni "juguete de IA".
- **Tono.** Breve, concreto, en español. Todo texto visible está en español. Los controles nombran
  su acción ("Aplicar configuración", no "Enviar"). Los errores dicen qué falló, a qué afecta y cuál
  es el siguiente paso seguro.

## Marca
- **Nombre:** Hyverion Quant AI. El nombre corto en la interfaz es "Hyverion".
- **Símbolo "Candle":** una H formada por tres velas (dos mechas de rango y una vela de señal
  central). Monocromo por diseño.
  - `assets/brand/hyverion-mark.svg`: glifo claro, para superficies oscuras.
  - `assets/brand/hyverion-mark-mono.svg`: `currentColor`.
  - `assets/brand/hyverion-icon.svg`: icono de la app para macOS. **Placa clara con el símbolo en
    carbón**, elegida por el dueño el 2026-09-25. Los iconos de Tauri se generan con `pnpm icons`.
- **Logotipo:** el símbolo más "Hyverion" en Geist 600 a −0.015em, con "Quant AI" opcional en
  `text-muted` 500. Se compone en código; no se guarda como SVG trazado.
- **Acento: monocromo, igual que el logo.** Blanco (`#F4F5F7`) en el tema oscuro y carbón
  (`#141518`) en el claro. El lima `#C6F24E` de la v1.1 se eliminó el 2026-09-25 por decisión del
  dueño: la app debe parecerse al logo. El acento **nunca** significa ganancia.
- **Reglas:** tamaño mínimo del símbolo 14 px. Espacio libre igual al ancho de la mecha en todos los
  lados. Nunca recolorear las mechas con el acento, nunca añadir brillo, nunca sobre imágenes
  cargadas.
- Exploración de marca: `docs/ui/brand/exploration.html`.

## 4. Estructura de la app
```
┌──────────────────────────── barra superior 48 ─────────────────────────────┐
│ (semáforos) H Hyverion  Inicio Trading Inteligencia Riesgo Laboratorio     │
│                                  [Buscar ⌘K]  │ ● SIMULACIÓN  ⚙ Ajustes    │
├─────────────────────────── pestañas de sección 44 ─────────────────────────┤
│ Mercado  Posiciones  Operaciones                        acciones de página │
├────────────────────────────────────────────────────────────────────────────┤
│ contenido (dueño del scroll)                                               │
├──────────────────────────── barra de estado 28 ────────────────────────────┤
│ ● Núcleo  ● Motor  Conciliación      Actualizado 14:02:11 UTC  SIMULACIÓN │
└────────────────────────────────────────────────────────────────────────────┘
```
- **Navegación superior** (estilo terminal de trading, inspirada en TradingView): se eliminó la
  barra lateral el 2026-09-25. La barra superior es también la zona de arrastre de la ventana; con
  `titleBarStyle: Overlay` deja 84 px a la izquierda para los semáforos de macOS.
- **Propiedad del scroll:** solo la región de contenido se desplaza. Barra superior, pestañas y
  barra de estado son fijas. Las tablas internas solo se desplazan con un `max-height` explícito.

## 5–7. Maquetación, tamaños de ventana, rejilla
- **Rejilla:** base de 4 px. Padding del contenido 24 px, o 16 px por debajo de 1280 de ancho. Ancho
  máximo del contenido 1600 px; por encima se centra y el ancho extra va al gráfico o la tabla.
- **Puntos de corte** (app de escritorio, ventana mínima 1024×700):
  - `< 1100`: los dominios de la barra superior muestran solo el icono (con tooltip).
  - `< 1280` (`xl`): el cuadro de búsqueda se reduce al icono con `⌘K`.
  - `md` 1024: los paneles inspectores pasan a sheets.
- **Teléfono y tablet** quedan fuera de alcance. Está documentado, y el diseño no debe romperse al
  redimensionar.

## 8–11. Barra superior, pestañas, navegación secundaria, encabezado
- **Barra superior.** Símbolo + "Hyverion" (enlace a Inicio), los cinco dominios de trabajo como
  botones de 32 px (icono Lucide 15 px + etiqueta 13 px), búsqueda ⌘K, `ModeBadge` y Ajustes como
  botón de icono. El dominio activo usa `surface-3` con borde interior `border-default`. Trading
  muestra un contador en `negative` cuando hay operaciones sin resolver.
- **Pestañas de sección.** Fila de 44 px con pestañas subrayadas y las acciones de la página a la
  derecha. El título del dominio no se repite visualmente (ya lo marca la barra superior); queda como
  `h1` accesible (`sr-only`). Inicio no tiene pestañas.
- **Navegación secundaria dentro de un panel:** control segmentado.
- **Sin migas de pan, sin titular gigante ni eyebrow.**
- **Atajos:** ⌘1–⌘6 dominios, ⌘K paleta.

## 12. Tipografía
Fuentes: Geist (UI y **números**) y Geist Mono (solo IDs, símbolos/tickers y código), ambas OFL e
incluidas vía `@fontsource-variable`.
- **Los números usan Geist con cifras tabulares** (clase `.num`: `font-variant-numeric:
  tabular-nums`), para alinear columnas sin el ancho de una mono.
- La mono (clase `.mono`) es solo para datos de identificación, nunca decorativa.

| Rol | Tamaño/Línea | Peso | Tracking | Uso |
|---|---|---|---|---|
| Display | 32/36 | 600 | −0.025em | Solo titulares de onboarding y estados vacíos |
| Page Title | 16/24 | 600 | −0.01em | Cabecera |
| Section Title | 14/20 | 600 | −0.005em | Encabezado de sección dentro de una página |
| Panel Title | 13/18 | 600 | 0 | Encabezado de panel, sheet o diálogo |
| Body | 13/20 | 400 | 0 | Texto por defecto |
| Secondary Body | 12.5/18 | 400 | 0 | Descripciones (`text-secondary`) |
| Label | 12/16 | 500 | 0 | Etiquetas de formulario y métricas (`text-muted`) |
| Caption | 11.5/16 | 400 | 0 | Marcas de tiempo, ayuda |
| Navigation | 13/18 | 500 | 0 | Barra superior y pestañas |
| Button | 13/16 | 500 | 0 | Botones |
| Table Header | 11.5/16 | 500 | +0.02em | `text-muted`, mayúscula inicial |
| Table Cell | 13/18 | 400 | 0 | Números en Geist tabular; tickers e IDs en mono |
| Numeric/KPI | 22/28 Geist tabular | 500 | −0.01em | Valores de métricas |

## 13. Color
Tokens definidos en `tokens.css`. Oscuro es el tema por defecto. Claro es secundario y usa los mismos
nombres semánticos (`data-theme`, preferencia `hyverion.theme`).

| Token | Oscuro | Claro |
|---|---|---|
| background | `#0E0F11` | `#F6F6F4` |
| surface-1 (barra superior, paneles) | `#131417` | `#FFFFFF` |
| surface-2 (elevado, inputs) | `#191B1F` | `#F0F0ED` |
| surface-3 (seleccionado) | `#22252A` | `#E6E6E2` |
| surface-hover | `#1C1E23` | `#EDEDEA` |
| surface-active | `#262930` | `#E1E1DC` |
| surface-floating | `#1B1D21` | `#FFFFFF` |
| border-subtle | `#1E2025` | `#E9E9E5` |
| border-default | `#2A2D34` | `#DADAD5` |
| border-strong | `#3A3E46` | `#C3C3BD` |
| text-primary | `#ECEEF1` | `#141518` |
| text-secondary | `#A7ACB5` | `#4A4F57` |
| text-muted | `#878D98` | `#5B606A` |
| text-disabled | `#50555E` | `#A5A8AE` |
| accent | `#F4F5F7` | `#141518` |
| accent-hover | `#FFFFFF` | `#000000` |
| accent-pressed | `#D9DCE1` | `#2A2D34` |
| on-accent | `#0E0F11` | `#FFFFFF` |
| accent-text | `#FFFFFF` | `#141518` |
| positive (PnL+) | `#36D39A` | `#07734F` |
| negative (PnL−) | `#F0616D` | `#B3263A` |
| warning | `#F2B544` | `#7F5000` |
| danger | `#F0616D` | `#B3263A` |
| info | `#6BB6F2` | `#18589A` |
| focus-ring | `#F4F5F7` | `#141518` |

**Reglas:**
- El acento solo marca la acción principal, la selección actual, el punto "en vivo", el `ModeBadge`
  y el anillo de foco. **Nunca** se usa para ganancia.
- PnL usa exclusivamente positive `#36D39A` / negative `#F0616D` (oscuro), solo para valores con
  signo y resultados.
- Los colores de estado siempre van con icono o texto, nunca solo color.
- **Contraste AA garantizado por test:** `app/src/styles/tokens.test.ts` exige ≥ 4.5:1 para todo
  token de texto sobre `background` y `surface-1/2/3`, en ambos temas.

## 14–17. Superficies, bordes, radios, sombras
- **Superficies.** Jerarquía `background` → `surface-1` → `surface-2`. Las secciones se apoyan en el
  fondo y se separan con espacio y un hairline `border-subtle`; sin cajas.
- **Bordes:** separadores de 1 px `border-subtle`. Inputs y botones con contorno usan un inset de
  1 px `border-default`. Nunca bordes alrededor de secciones completas.
- **Radios:** `radius-xs` 3 (insignias), `radius-sm` 4 (controles), `radius-md` 6 (popovers, paneles,
  sheets), `radius-lg` 8 (diálogos). Sin píldoras salvo el punto en vivo.
- **Sombras** solo en capas flotantes (`shadow-float`: `0 8px 24px rgba(0,0,0,.36), 0 0 0 1px
  border-default`). Sin sombras en el contenido en flujo.

## 18. Espaciado
Escala: 0, 2, 4, 6, 8, 12, 16, 20, 24, 32, 40, 48, 64.
- Dentro de controles: 8–12.
- Entre campos relacionados: 12.
- Entre secciones: 32.
- El espacio sobre un título de sección es mayor que el espacio bajo él.

## 19. Iconografía
Solo Lucide, trazo 1.5. Tamaños: 14 (en línea), 16 (por defecto), 20 (estados vacíos). Iconos en
`currentColor`. Los botones solo-icono necesitan `aria-label` y tooltip.

## 20–28. Controles
- **Altura:** `sm` 28 (tablas, barras de herramientas), `md` 32 (por defecto), `lg` 40 (solo CTA de
  onboarding).
- **Botones:**
  - Primario: relleno de acento con texto `on-accent`. Máximo uno por contexto.
  - Secundario: `surface-2` con borde inset.
  - Ghost: transparente, hover `surface-hover`.
  - Destructivo: texto `danger` sobre transparente; relleno `danger` en el diálogo de confirmación.
  - Todos tienen estados hover, active, focus-visible, disabled y loading (un spinner sustituye al
    icono y el ancho no cambia).
- **IconButton:** cuadrado de 28 o 32, ghost.
- **Inputs, textareas y selects:** `surface-2` con inset de 1 px `border-default`; anillo de foco de
  2 px `focus-ring` con desplazamiento 1. El error usa un inset `danger` y un mensaje debajo. Los
  importes son cadenas Decimal, alineadas a la derecha en Geist tabular, con sufijo de unidad.
- **Combobox:** Popover de Radix más lista filtrada.
- **Checkbox, radio y switch:** 16 px, acento al marcar. El switch solo para ajustes inmediatos de
  encendido/apagado; lo que requiere validación usa checkbox + Aplicar.

## 29–32. Tablas, listas, paneles, tarjetas
- **Tablas:**
  - Altura de fila 32 (`sm` 28).
  - Cabecera fija sobre `surface-1`, mayúscula inicial, `text-muted`.
  - Texto a la izquierda; números y acciones a la derecha.
  - Hover `surface-hover`; filas seleccionadas `surface-3`.
  - Las cabeceras ordenables muestran un chevrón.
  - Las columnas de baja prioridad se ocultan por debajo de `lg`.
- **Listas:** identidad, subtítulo, meta, estado y acción por fila. Para agentes, proveedores,
  fuentes, alertas y línea de tiempo.
- **Paneles:** `surface-1` y `radius-md`, solo para agrupaciones independientes (gráfico, inspector).
- **Tarjetas:** evitarlas por defecto y nunca anidarlas.

## 33–35. Diálogos, sheets, cajones
- **Diálogos** de 440 o 560 de ancho, para tareas cortas y enfocadas: confirmar una acción
  destructiva, dar el motivo de una resolución.
- **Sheets** desde la derecha, 480 de ancho, para inspeccionar detalles (ciclo de vida de una
  operación, un elemento de memoria, una traza de decisión).
- Nunca páginas completas dentro de modales.

## 36–38. Tooltips, popovers, desplegables
- **Tooltips:** retardo de 400 ms, texto de 12 px, `surface-floating`.
- **Popovers y desplegables:** `shadow-float` y `radius-md`, elementos de 28 px, navegables con
  teclado.

## 39–42. Pestañas, búsqueda, filtros, fechas
- **Pestañas:** subrayado de 2 px en la activa (`text-primary`); inactivas en `text-muted`.
- **Búsqueda:** ⌘K es global. La búsqueda local en tablas usa un input de 28 px, `/` para enfocar y
  botón de limpiar.
- **Filtros:** barra compacta con popovers, insignia de filtros activos y "Limpiar filtros".
- **Fechas:** todo se muestra en UTC por defecto con sufijo "UTC". El tiempo relativo ("hace 3 min")
  aparece en captions, con la hora absoluta en un tooltip.

## 43. Gráficos
- **Mercado:** `lightweight-charts`, velas más volumen. Velas alcistas y bajistas en positive y
  negative, crosshair `text-muted`, rejilla `border-subtle`.
- **PnL:** línea de área con la línea base cero destacada y relleno positive o negative al 12 % de
  alfa.
- Sin leyendas con una sola serie. Sin 3D ni paletas arcoíris.

## 44. Formularios
Organizados por tarea, en secciones con título y descripción de una línea, y luego filas. El flujo
es siempre **Validar → Aplicar**. La respuesta de validación del servidor se asigna a errores en
línea por campo. Acciones fijas al pie cuando el formulario tiene cambios.

## 45. Estado
- `StatusDot` de 6 px, siempre con etiqueta.
- `Badge` de 20 px de alto, `radius-xs`, texto de 11.5 px, estilo neutro con borde inset por defecto
  y variantes de tono.
- **Insignia de modo:**
  - SIMULACIÓN (PAPER): contorno de acento y punto en vivo.
  - REAL BLOQUEADO: contorno `danger`.

## 46–48. Vacío, carga, error
- **Estados vacíos:** qué está vacío, por qué importa, qué hacer y una acción principal. Pueden usar
  el rol Display en onboarding y primer arranque.
- **Carga:** filas esqueleto con el tamaño final; sin spinners a pantalla completa tras el arranque.
- **Errores:** qué falló, qué se ve afectado, reintentar y un detalle técnico plegable
  (`error_code`, petición).
- **API desconectada:** la barra de estado pasa a color `warning`, aparece un banner sobre el
  contenido y los datos siguen visibles como obsoletos con su marca de tiempo.

## 49–50. Toasts, paleta de comandos
- **Toasts** abajo a la derecha durante ~4 s, solo para confirmaciones que cruzan contextos (copia
  creada, configuración aplicada).
- **Paleta ⌘K:** 560 de ancho, agrupada en Ir a / Acciones / Símbolos, con atajos mostrados como
  `Kbd`.

## 51–53. Escritorio, tablet, móvil
- El único objetivo es escritorio, de 1024×700 a 2560×1440.
- A 2560 de ancho el contenido se limita a 1600 y se centra, salvo en Trading › Mercado, donde el
  gráfico se expande.
- Tablet y móvil no están soportados (fuera de alcance por diseño).

## 54. Movimiento
- **Duraciones:** `fast` 120 ms (hover y pulsación), `normal` 200 ms (popovers y pestañas), `slow`
  320 ms (sheets).
- **Curva:** `cubic-bezier(.2,.8,.2,1)`.
- `prefers-reduced-motion` desactiva todas las transformaciones y deja cambios de opacidad de
  120 ms o menos.
- Sin coreografía de carga de página.

## 55. Accesibilidad
- **Contraste:** AA para texto; todos los pares secondary y muted verificados a ≥4.5:1 sobre
  `background` y `surface-1`.
- **Foco:** anillo `focus-visible` visible en todas partes con `focus-ring`.
- **Teclado:** recorridos completos por teclado; atajos ⌘1–⌘6 para los dominios y ⌘K.
- **Semántica:** semántica de Radix para capas; landmarks `nav`, `main` y `status`; la barra de
  estado usa `aria-live="polite"` solo para cambios de modo y conexión.
- **Barras de desplazamiento, selección de texto y cursor** tematizados con los tokens.

## 56. Visualización de datos y números
- Formateadores centrales en `app/src/lib/format.ts`: `money`, `pct`, `qty`, `bps`, `dateUTC` y
  `relative` (probados con Vitest en `format.test.ts`).
- El dinero llega como **cadenas** Decimal y nunca se convierte a float para aritmética; el formato
  de visualización trabaja sobre la cadena.
- Los valores con signo siempre muestran `+` o `−` (U+2212).

## Z-index
`base 0 · sticky 10 · barra superior 20 · dropdown 40 · popover 50 · sheet 60 · modal 70 · toast 80 · palette 90`.

## Reglas propias del producto
- El modo (SIMULACIÓN / REAL BLOQUEADO) está siempre visible en la cabecera y en la barra de estado.
- La UI nunca expone un control para enviar órdenes. La ejecución pertenece al motor.
- Todo rechazo o aprobación de riesgo enlaza con su traza.
- Los agentes deterministas se etiquetan "Determinista"; los de IA muestran proveedor y modelo.
- Los secretos nunca se muestran tras guardarse; solo "Guardado en Llavero" y una acción Reemplazar.

## Inicio (composición, v1.2)
Inicio responde "¿qué importa ahora?" con una composición visual propia, en este orden:
1. **Atención:** tarjetas horizontales (máx. 3) para operaciones sin resolver, stops sin verificar,
   alertas y fallos de preparación. Si no hay nada: una línea tranquila con check.
2. **Héroe de patrimonio:** patrimonio marcado a 44 px con conteo animado, resultado del día con
   porcentaje, curva de patrimonio (área monocroma con trazado animado, cursor con valor y fecha,
   periodos 7 D / 30 D / Todo) y efectivo, pico y comisiones.
3. **Protección:** tres medidores radiales animados (drawdown vs límite, pérdida diaria vs tope,
   presupuesto de IA) que escalan a `warning` ≥ 50 % y `negative` ≥ 80 %; nivel de la escalera,
   ganancia protegida y stops verificados.
4. **Última decisión:** el pipeline Datos → Agentes → Propuesta → Crítico → Riesgo → Ejecución como
   flujo de 6 nodos con estados (hecho / en curso / bloqueado / sin actividad), pulso que recorre las
   conexiones activas y la justificación "por qué ahora" de la propuesta.
5. **Mercados:** tarjetas con precio, variación y minigráfico de cierres reales de velas.
6. **Posiciones abiertas** y **actividad reciente** (línea de tiempo).

Componentes en `app/src/ui/viz.tsx` (`AnimatedNumber`, `EquityArea`, `RadialGauge`, `Sparkline`,
`PipelineFlow`). Las animaciones (`rise`, `viz-reveal`, `viz-sweep`, `viz-flow`, `viz-pulse`) viven en
`app.css`, usan colores del tema y se desactivan con `prefers-reduced-motion`. Las cifras mostradas
siempre salen de los formateadores Decimal exactos; los floats solo se usan para geometría.
