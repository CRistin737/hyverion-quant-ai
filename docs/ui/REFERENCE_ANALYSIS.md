# Análisis de referencias — UI PHASE 1

**Fuentes:** `references/ui/` (4 capturas, 2026-09-25). Las cuatro son **landing pages de
marketing**, no interfaces de aplicación. Por eso extraemos **lenguaje** (tipografía, disciplina de
color, forma de los controles, tono) y nunca la maquetación: una consola de trading no es una
sección hero.

| Archivo | Producto |
|---|---|
| `20260925022952.png` | Cap (cap.so) |
| `20260925023029.png` | Descript (descript.com) |
| `20260925023052.png` | Composio (composio.dev) |
| `20260925023158.png` | OpenReel Video (openreel.video) |

## Cap
- **Superficies:** ilustración clara y aireada de cielo/nubes; navegación blanca con altura generosa.
- **Tipografía:** display neogrotesca enorme con tracking cerrado; cuerpo en serif cálida.
- **Controles:**
  - Botones de 40–44 px casi píldora (radio 8–10 px).
  - Primario negro casi puro, secundario con contorno.
  - Relleno azul pálido suave en el CTA de descarga.
- **Control segmentado** (modos Instant / Studio / Screenshot): compacto, icono + etiqueta, sobre un
  carril tintado sutil.
- **Adoptar:**
  - Controles tranquilos y seguros.
  - Un control segmentado para cambiar de modo (sub-pestañas).
  - Contención, con un único primario fuerte.
- **Rechazar:** ilustración decorativa y espaciado de landing.

## Descript
- **Color:** fondo burdeos profundo, un solo CTA rojo cálido, texto crema/blanco.
- **Tipografía:** serif editorial de display en peso alto, con sans limpia para la UI. Eyebrow mono
  diminuto en mayúsculas ("AI VIDEO EDITOR").
- **Composición:** contenedor de navegación blanco flotante con radio. Un panel de chat de asistente
  muestra UI del producto dentro del marketing.
- **Adoptar:**
  - Tipografía editorial de display **solo** en onboarding / estados vacíos.
  - Eyebrows mono en mayúsculas para etiquetas de sección.
  - Un único color de acción saturado.
- **Rechazar:** serif en UI de datos; gran collage fotográfico.

## Composio
- **Color:** casi negro con textura de líneas de escaneo azul/verde. Blanco para primario, contorno
  para secundario.
- **Tipografía:**
  - Navegación y botones en mono mayúsculas, tracking ~+0.08em.
  - Display grotesca.
  - Bloque de resaltado en línea ("in minutes" sobre azul sólido) como recurso de énfasis.
- **Controles:** esquinas rectas (0–2 px), bordes de 1 px, filas finas de chips "WORKS WITH" con
  separadores.
- **Adoptar:**
  - Etiquetas mono, cabeceras de tabla y chips de estado.
  - Separadores finos (hairlines).
  - Geometría de control técnica y cuadrada.
  - La idea del bloque de resaltado para el estado activo/selección.
- **Rechazar:** la textura ruidosa de líneas de escaneo.

## OpenReel
- **Color:** negro con rejilla tenue. Acento **lima ácido** (~#C8FF3D) en CTAs, un banner superior
  fino y un bloque de palabra resaltada.
- **Tipografía:** display ultraancha y pesada (tipo Druk/Monument). Versalitas mono para navegación y
  etiquetas.
- **Controles:** barra de navegación enmarcada con borde sutil; corchetes de esquina como marco; chip
  de estrella de GitHub en forma de píldora.
- **Adoptar:**
  - Un acento de alta energía usado **con mucha moderación** (indicador en vivo, CTA principal, foco).
  - Corchetes de esquina para el crosshair/selección del gráfico.
  - Textura de rejilla tenue **solo** detrás de áreas de gráfico vacías.
- **Rechazar:** los titulares de display gritones y el brillo neón.

## Síntesis → lenguaje de producto Hyverion

| Dimensión | Decisión |
|---|---|
| Base | Neutros oscuros "carbón" (no negro puro), con equilibrio ligeramente cálido-frío. Tema claro secundario. |
| Acento | Un acento de marca (propuesta inicial: Signal lime `#C6F24E`). **Decisión final (2026-09-25):** acento monocromo como el logo, blanco `#F4F5F7` en oscuro y carbón `#141518` en claro; el lima se eliminó (ver DESIGN_SPEC v1.2). Colores semánticos `positive`/`negative` **separados** para PnL, para que la marca nunca signifique "ganancia". |
| Tipografía | Geist para UI y números (con cifras tabulares) y Geist Mono solo para IDs, símbolos y código. Display (tracking cerrado) solo en onboarding y titulares de estados vacíos. |
| Geometría | Radios pequeños (4 px controles, 6 px paneles/popovers), hairlines de 1 px, sin sombras pesadas. Las capas flotantes llevan una sola sombra suave. |
| Etiquetas | Mono en mayúsculas, 11 px, tracking +0.06em para eyebrows de sección y etiquetas de estado. |
| Densidad | Tendencia cabina: controles de 28 px compacto / 32 px por defecto, filas de tabla de 32 px. |
| Movimiento | 120 / 200 / 320 ms, ease-out; solo para estados y capas. |
| Personalidad | Precisa, tranquila, técnica. Tono "panel de instrumentos", no "demo hacker" ni "juguete de IA". |

## Principios de diseño
1. **El modo es sagrado.** SIMULACIÓN / REAL bloqueado siempre visible y sin ambigüedad.
2. **Cada pantalla responde una pregunta** (ver SCREEN_INVENTORY).
3. **Primero los números.** Cifras tabulares, alineadas a la derecha, con signo, unidad y periodo.
4. **Superficie, no cajas.** La jerarquía sale del contraste y el espaciado; los bordes solo separan.
5. **Explicar decisiones.** Toda aprobación o rechazo enlaza a su motivo (traza de riesgo).
6. **Silencio hasta que importa.** El acento y el color se reservan para cambios de estado y acciones requeridas.

## Antipatrones (prohibidos)
- Tarjetas anidadas.
- Brillo (glow).
- Degradados morados/azules "de IA".
- Glassmorphism.
- Círculos gigantes con iconos.
- Muros de KPI.
- Fondos `#000` puros.
- El color de marca usado como "éxito".
- Desbordamiento horizontal oculto.
- Emoji como iconos.
- Aspecto shadcn por defecto.
- Titulares de escala hero en pantallas de la app.
