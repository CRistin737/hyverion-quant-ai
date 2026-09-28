# Motor en segundo plano, modelos de IA y automejora

## 1. Motor en segundo plano (funciona con la app cerrada)

1. **Ajustes › Motor › Motor en segundo plano** → activar.
   También: `hyverion-core service install` (o `uv run python -m trading_bot service install`).
2. Comprobar: **Ajustes › Salud del sistema** → «Motor en segundo plano: activo»,
   o `service status` (`loaded: true` y un `pid`).
3. Registro: `~/Library/Application Support/Hyverion Quant AI/logs/engine.log`.
4. Desactivar: el mismo interruptor o `service uninstall`. Las posiciones abiertas
   conservan su stop en Alpaca.

Arranca al iniciar sesión en el Mac y se reinicia solo si falla. Encuentra
`claude`, `codex` y `grok` aunque no haya una terminal abierta. Si instalas el
servicio desde una copia del código dentro de Escritorio o Documentos, macOS puede
bloquear el acceso (privacidad): usa la app compilada, que guarda todo en
Application Support.

Si alguien deja `local.yaml` o `autonomy.yaml` con un valor inválido mientras el
motor corre, el motor sigue con la última configuración válida, **solo protege las
posiciones abiertas** (sin entradas nuevas) y lo registra como `CONFIG_INVALID`. Un solo motor a la
vez: con el servicio activo, la app lo muestra como «externo».

### Rutina diaria (hora de Nueva York)

| Hora | Rutina | Qué hace |
|---|---|---|
| 08:30 (días de mercado) | Preparación | Lee todas las fuentes y guarda el contexto del día |
| 16:15 (días de mercado) | Cierre | Informe del día y ciclo de memoria (lecciones, fiabilidad de agentes) |
| 20:00 (lunes a viernes) | Mejora nocturna | Opus propone hasta 3 ideas; autopromoción de las probadas |
| Viernes 17:00 | Revisión semanal | Resumen de la semana |

Cada rutina corre una vez al día (evento `DAILY_ROUTINE`). Si una falla, queda
registrada y el motor sigue operando.

## 2. Modelos de IA

- **Botón «Modelo»** en la barra superior (o **Inteligencia › Modelos › Modelo de cada tarea**):
  - **Análisis** (Sonnet 5 por defecto): agentes que leen y resumen.
  - **Decisión** (Opus 5.5): estrategia y crítico.
  - **Mejora** (Opus 5.5): revisión nocturna.
- El cambio se aplica en el siguiente ciclo, sin reiniciar.
- Solo la suscripción principal usa el modelo elegido; los respaldos usan el
  predeterminado de su propia app.
- Si un modelo no está disponible en tu plan, elige «Predeterminado de Claude Code».

### Límites de la suscripción

- **Inteligencia › Modelos** muestra por suscripción la barra de la **sesión de 5 h**
  (lo que queda) y la **semana** (lo usado). En Inicio aparecen como «IA · 5 horas» e «IA · semana».
- Claude: lectura oficial de `claude /usage`. Codex: bloque `rate_limits` de sus registros.
  Grok y Gemini no publican límites.
- Al llegar al 100 %, el sistema pasa a la IA de respaldo.
- **Cambiar de cuenta**: cierra la sesión del CLI oficial y abre el inicio de sesión.

## 3. Automejora

Ver **Inteligencia › Aprendizaje**.

- **Ajustes de estrategia** (stop, objetivo, tiempo, regímenes): solo se proponen si
  ganan fuera de muestra con precios reales; se aplican solos en paper tras
  **5 sesiones nuevas en sombra** sin peor resultado ni peor caída. Aparecen en
  «En uso» con «Aplicado solo tras probarse» y un botón **Deshacer**.
- **Instrucciones de un agente**: siempre esperan tu «Aprobar y aplicar».
- **Herramienta nueva**: «Aceptar» crea una ficha en
  `~/Library/Application Support/Hyverion Quant AI/requests/FR-*.md` para desarrollarla.
  Nunca se aplica sola.
- Nada toca límites de riesgo, órdenes, broker ni código. Modo `supervised` en
  `config/autonomy.yaml` desactiva la autopromoción.

## 4. Riesgo

**Riesgo › Límites**: perfil Conservador / Medio / Alto en % del patrimonio de Alpaca.
«Personalizar» permite ajustar cada valor dentro del margen de `config/autonomy.yaml`.
