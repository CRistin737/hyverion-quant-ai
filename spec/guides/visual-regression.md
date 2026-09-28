# Matriz de regresión visual

La app de escritorio (Tauri + React, en `app/`) tiene pruebas visuales deterministas con Playwright.
Usan datos de demostración en memoria (`?fixture=demo`) y un reloj congelado; nunca llaman a un
exchange, a un proveedor de IA ni a fuentes externas, y no requieren el núcleo Python.

Ejecución, desde `app/`:

```bash
pnpm test:visual                # compara contra las capturas de referencia
pnpm test:visual -u             # actualiza las referencias tras un cambio de diseño revisado
```

Cobertura (`app/tests/visual/routes.spec.ts`, configuración en `app/playwright.config.ts`):

- 17 vistas de los 6 dominios, en 1024×700 (mínimo soportado), 1280×800, 1440×900 y 1920×1080
  (tema oscuro), más el proyecto `1440x900-light` con el tema claro (seleccionado con la preferencia
  `hyverion.theme`, no con el esquema del sistema). Cinco proyectos en total.
- En cada vista: sin errores de consola, sin desbordamiento horizontal y captura comparada con la
  referencia (`app/tests/visual/__screenshots__/`).
- Flujos: asistente de primer uso (`?fixture=first-run`), núcleo sin conexión (`?fixture=offline`)
  con error recuperable, paleta ⌘K y distintivo de modo **SIMULACIÓN** siempre visible.

Las capturas son artefactos de revisión, no datos de producción. Tras cualquier cambio de diseño,
la puerta sigue siendo la inspección humana de la matriz antes de actualizar las referencias.

## Umbral (2026-09-26)
`maxDiffPixels: 50` (absoluto). El umbral anterior (`maxDiffPixelRatio: 0.01`, ~10.000 píxeles a 1280×800)
dejaba pasar cambios reales de texto y botones: 36 referencias habían quedado desactualizadas sin fallar.
Con fixtures deterministas y reloj congelado, 50 píxeles es estable en el mismo equipo (verificado con dos
corridas seguidas). Flujos funcionales en `tests/visual/flows.spec.ts` (solo 1440×900): motor, cierre de
emergencia, propuesta de cambio y login de suscripción.
