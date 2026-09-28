# Arquitectura de información de la app de escritorio

El título de la ventana es exactamente `Hyverion Quant AI`. Todo texto visible está en español. La
barra superior muestra el símbolo "Candle" y el nombre corto "Hyverion"; no hay eslogan ni prefijos
numéricos en las páginas.

## Dominios (barra superior)

| Atajo | Dominio | Pestañas | Ruta |
|---|---|---|---|
| ⌘1 | Inicio | — | `/inicio` |
| ⌘2 | Trading | Mercado · Posiciones · Operaciones | `/trading/*` |
| ⌘3 | Inteligencia | Agentes · Modelos · Fuentes | `/inteligencia/*` |
| ⌘4 | Riesgo | Decisión · Límites · Auditoría | `/riesgo/*` |
| ⌘5 | Laboratorio | Simulación · Aprendizaje · Memoria · Bóveda | `/laboratorio/*` |
| ⌘6 | Ajustes | Cuenta IA · Exchange · Fuentes · Riesgo · Sistema · Ayuda | `/ajustes/*` |

Inicio a Laboratorio son botones de la barra superior; Ajustes es un botón de icono a la derecha.
Las pestañas de cada dominio van en una fila de 44 px bajo la barra superior, junto a las acciones de
la página. Fuente de verdad en código: `app/src/shell/routes.ts` y `app/src/shell/TopBar.tsx`.

## Superficies globales

- **Onboarding de primer arranque** (4 pasos: Capital, Mercado, Inteligencia artificial, Límites de
  riesgo). Se muestra en lugar del espacio de trabajo mientras `GET /api/v1/setup/status` indique
  que falta configuración. Sustituye el requisito de `trading_bot setup`.
- **Paleta de comandos ⌘K:** Ir a / Acciones / Símbolos.
- **Barra de estado persistente:** API, motor, reconciliación, última actualización y modo.
- **Insignia de modo "SIMULACIÓN"** siempre visible en la barra superior.

## Reglas

- La jerarquía tiene dos niveles (dominio › pestaña); no hay migas de pan.
- Las etiquetas deben leerse completas en la ventana mínima (1024×700). Por debajo de 1100 px los
  dominios de la barra superior muestran solo el icono con tooltip.
- Los fallos de conexión se muestran como estado + causa + acción de recuperación.
- Ninguna pantalla expone el envío de órdenes ni la activación de LIVE.

## Correspondencia con la app anterior

Las 15 pantallas de la app PySide6 (eliminada el 2026-09-25) se agrupan así: Overview → Inicio; Markets, Positions,
Operations → Trading; Agents, Providers, Sources → Inteligencia; Risk, Audit → Riesgo; Paper/Shadow,
Backtest, Learning, Memory → Laboratorio; Settings, Guides → Ajustes. Detalle en
`docs/ui/SCREEN_INVENTORY.md`.
