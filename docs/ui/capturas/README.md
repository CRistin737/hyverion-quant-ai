# Capturas revisadas de la app

Capturas de la app en modo demostración (datos ficticios, reloj congelado), a 1440×900. Salen de las
referencias visuales verificadas en `app/tests/visual/__screenshots__/`, así que coinciden con la UI
actual. Para regenerarlas, primero pasa `pnpm test:visual` y después ejecuta
`scripts/update_doc_screenshots.sh`.

| Pantalla | Captura |
|---|---|
| Inicio (velas de la última hora en Mercados) | ![Inicio](inicio.png) |
| Inicio (tema claro) | ![Inicio, tema claro](inicio-claro.png) |
| Configuración inicial | ![Configuración inicial](onboarding.png) |
| Trading › Mercado (tipos de gráfica, temporalidades, entrada, stop y compras/ventas) | ![Mercado](trading-mercado.png) |
| Trading › Operaciones (posiciones y operaciones en una sola vista) | ![Operaciones](trading-operaciones.png) |
| Inteligencia › Agentes | ![Agentes](inteligencia-agentes.png) |
| Inteligencia › Agentes › historial de versiones (qué cambió, por qué y cómo le fue) | ![Historial](inteligencia-historial.png) |
| Inteligencia › Modelos (suscripción, respaldo y presupuesto editables) | ![Modelos](inteligencia-modelos.png) |
| Riesgo › Decisiones | ![Decisiones](riesgo-decisiones.png) |
| Riesgo › Límites (editables con ejemplos en dólares) | ![Límites](riesgo-limites.png) |
| Ajustes (una sola página) | ![Ajustes](ajustes.png) |

## Versiones de las CLIs oficiales verificadas (2026-09-26)

| Proveedor | CLI | Versión |
|---|---|---|
| Anthropic | `claude` | 2.1.283 (Claude Code) |
| OpenAI | `codex` | codex-cli 0.157.0 |
| xAI | `grok` | 1.0.30 (stable) |
| Google | `gemini` | no instalada en el equipo de verificación |

Los comandos de inicio de sesión (`claude auth login`, `codex login`, `grok login`, `gemini`) y el formato
de salida JSON dependen de estas versiones. Si una CLI cambia de versión mayor, vuelve a verificar
`providers/subscription_cli.py` y `providers/login_flow.py`.

![ajustes-salud](ajustes-salud.png)
![inteligencia-aprendizaje](inteligencia-aprendizaje.png)
![inteligencia-memoria](inteligencia-memoria.png)
![noticias-calendario](noticias-calendario.png)
![noticias-fuentes](noticias-fuentes.png)
![noticias-noticias](noticias-noticias.png)
![trading-informe](trading-informe.png)
