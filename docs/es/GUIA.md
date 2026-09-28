# Guía de uso de Hyverion Quant AI

Esta guía es para quien clona el repositorio y quiere tener Hyverion funcionando en su Mac. Hyverion
opera **solo QQQ**, **solo en simulación (paper)** y solo en el horario regular de la bolsa de Nueva
York. El modo real (LIVE) está bloqueado. Nada de esto es asesoramiento financiero.

## 1. Instalar los requisitos

Necesitas macOS 14 o posterior. En una terminal:

```bash
xcode-select --install                                           # herramientas de compilación de Apple
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # Rust estable (para Tauri)
brew install uv node@22                                          # uv (Python) y Node 22
uv python install 3.12                                           # Python 3.12 gestionado por uv
corepack enable                                                  # activa pnpm (versión fijada en app/package.json)
```

Si no usas Homebrew, instala [uv](https://docs.astral.sh/uv/) y [Node 22](https://nodejs.org/) con
sus instaladores oficiales.

Además necesitas:

- **Una suscripción de IA** con su CLI oficial instalada: Claude Code (`claude`) o Codex (`codex`).
  Grok y Gemini son opcionales como respaldo.
- **Una cuenta gratuita de [Alpaca](https://alpaca.markets/)** con Paper Trading.

## 2. Clonar y arrancar

```bash
git clone https://github.com/CRistin737/hyverion-quant-ai.git
cd hyverion-quant-ai
uv sync
cd app && pnpm install && pnpm tauri dev
```

La primera compilación de Tauri tarda unos minutos. La app arranca su propio núcleo de Python en tu
Mac (solo escucha en `127.0.0.1`) y no necesita navegador.

Si prefieres una app normal en el Dock, compílala con `cd app && pnpm build:app`. Los detalles están
en [`docs/MACOS_APP.md`](../MACOS_APP.md).

## 3. Primer arranque (onboarding)

La primera vez se abre **Configura Hyverion**, con tres pasos:

1. **Conecta tu cuenta paper**: las claves de Alpaca Paper (ver el punto 4) y tu email de contacto.
2. **Inteligencia artificial**: qué suscripción analiza el mercado.
3. **Perfil de riesgo**: cuánto arriesgar, en % de tu cuenta.

Al terminar llegas a **Inicio**. Todo lo que eliges aquí se puede cambiar después.

## 4. Conectar Alpaca Paper

1. Entra en [app.alpaca.markets](https://app.alpaca.markets/) y cambia a **Paper Trading** (arriba a
   la izquierda).
2. En **API Keys**, pulsa **Generate New Keys** y copia el *API Key ID* y el *Secret Key* (el secreto
   solo se muestra una vez).
3. Pega las dos claves **en la app**: en el onboarding o en **Ajustes › Cuenta de trading › Claves de
   Alpaca Paper**. Después elige **Alpaca Paper** como broker y pulsa **Validar y aplicar**.

Las claves se guardan en el **Llavero de macOS** (servicio `hyverion-quant-ai`). **Nunca las pegues
en un chat, un issue, un archivo del repositorio ni un registro.**

Usa una cuenta paper **dedicada** a Hyverion: una posición abierta a mano aparece como diferencia en
la conciliación y bloquea las entradas. El patrimonio de esa cuenta es el capital de Hyverion; no hay
capital configurado.

Guía completa: [`spec/guides/broker-paper.md`](../../spec/guides/broker-paper.md).

## 5. Conectar la suscripción de IA

Hyverion usa tu suscripción a través de la CLI oficial; no hace falta ninguna API key.

1. Abre **Inteligencia › Modelos**.
2. En la tarjeta de Claude o Codex pulsa **Iniciar sesión**: se abre el inicio de sesión oficial en
   el navegador y vuelves a la app. También puedes hacerlo en una terminal con `claude` (y `/login`)
   o `codex login`.
3. Elige la suscripción principal y, si quieres, respaldos en orden. Si todas fallan, el sistema no
   opera (`NO_TRADE`).
4. El botón **Modelo** de la barra superior elige el modelo de cada tarea: **Análisis** (Sonnet),
   **Decisión** y **Mejora** (Opus). El cambio se aplica en el siguiente ciclo.

Las barras muestran lo que queda de la sesión de 5 horas y lo usado de la semana. **Cambiar de
cuenta** cierra la sesión de la CLI y abre el inicio de sesión de nuevo. Más detalle en
[`spec/guides/motor-y-modelos.md`](../../spec/guides/motor-y-modelos.md) y
[`docs/PROVIDERS.md`](../PROVIDERS.md).

## 6. Fuentes de datos

En **Noticias › Fuentes** pegas la clave o el email y pulsas **Probar**:

- **Email de contacto** (obligatorio): la SEC EDGAR y el BLS solo atienden programas que se
  identifican con un email. **Sin él no se abren operaciones**, porque el calendario macro no se
  puede leer y la regla macro falla cerrado.
- **Clave de FRED** (gratuita, en
  [fredaccount.stlouisfed.org/apikeys](https://fredaccount.stlouisfed.org/apikeys)): tipos de interés,
  dólar y VIX.
- **Finnhub** (opcional, gratuita): fechas de resultados.
- **RSS y redes sociales** (en **Redes sociales y RSS**): solo fuentes que permitan la lectura
  automatizada y, en redes, solo con la API oficial y tu propia clave.

El email y las claves también van al Llavero. El texto de noticias y redes se trata como dato, nunca
como instrucción. Detalle de cada fuente:
[`spec/guides/fuentes-de-datos.md`](../../spec/guides/fuentes-de-datos.md).

## 7. Perfiles de riesgo

En **Riesgo › Límites** eliges el perfil, en % del patrimonio de tu cuenta Alpaca Paper:

| Perfil | Por operación | Pérdida diaria | Pérdida semanal |
|---|---:|---:|---:|
| Conservador | 0,25 % | 1 % | 2,5 % |
| Medio (por defecto) | 0,5 % | 2 % | 5 % |
| Alto | 1 % | 3 % | 8 % |

**Personalizar** ajusta cada valor, siempre dentro del margen de `config/autonomy.yaml`. Hay reglas
que nunca se relajan: el límite semanal y la caída máxima detienen las entradas, toda posición tiene
un stop nativo en el broker y nunca se usa martingala ni se aumenta el tamaño para recuperar
pérdidas. Modelo completo: [`docs/RISK_MODEL.md`](../RISK_MODEL.md).

## 8. Iniciar y detener el motor

- **Iniciar**: botón **Iniciar motor** en Inicio o en la barra de estado inferior (o ⌘K). El motor
  no arranca sin el broker conectado. Fuera del horario de mercado duerme hasta la siguiente
  preapertura.
- **Detener**: botón **Detener** en la barra de estado. Si hay posiciones abiertas, la app pide
  confirmación y ofrece **Cerrar posiciones primero**.
- **Cerrar posiciones**: cierra todo por la ruta normal de ejecución, aunque la IA no esté disponible.

Qué pasa al detener el motor:

- Cada posición conserva su **stop nativo en Alpaca**, válido hasta cancelar (GTC): sigue protegida
  con el motor parado y de un día para otro.
- Mientras está parado **no** se aplican el cierre por tiempo, el objetivo ni el cierre antes del
  final de la sesión. Si no quieres dejar posiciones de un día para otro, ciérralas antes de parar.
- Las llamadas de IA en curso se cancelan: el motor corre en su propio grupo de procesos y al pararlo
  no queda ninguna CLI de IA viva. Esas ejecuciones quedan registradas como `CANCELLED`.
- Al volver a arrancar, las ejecuciones que quedaron abiertas se cierran y Hyverion **concilia** con
  Alpaca (posiciones, órdenes y ejecuciones). Si algo no cuadra, no abre operaciones nuevas hasta
  que una conciliación salga bien.

## 9. Motor en segundo plano

Para que Hyverion opere con la app cerrada, activa **Ajustes › Motor › Motor en segundo plano** (o
`uv run python -m trading_bot service install`). Es un servicio de macOS (`launchd`) que arranca al
iniciar sesión y se reinicia solo si falla. La app lo muestra como motor «externo».

- Comprobar: **Ajustes › Salud del sistema** o `uv run python -m trading_bot service status`.
- Registro: `~/Library/Application Support/Hyverion Quant AI/logs/engine.log`.
- Desactivar: el mismo interruptor o `uv run python -m trading_bot service uninstall`.

Si lo instalas desde una copia del código dentro de Escritorio o Documentos, macOS puede bloquear el
acceso por privacidad; en ese caso usa la app compilada. Rutina diaria (hora de Nueva York):
preparación 08:30, cierre 16:15, mejora nocturna 20:00 y revisión semanal el viernes. Detalle en
[`spec/guides/motor-y-modelos.md`](../../spec/guides/motor-y-modelos.md).

## 10. Informe y exportación

**Trading › Informe** muestra el resultado de un día, un mes, un año o un rango: patrimonio al inicio
y al final, ganancia neta, acierto, profit factor, mejor y peor operación, caída máxima y comisiones.
Los botones **Excel (CSV)** y **PDF** descargan el informe y todas las operaciones cerradas.

La automejora se revisa en **Inteligencia › Aprendizaje**: los ajustes de estrategia se aplican solos
en paper solo si superan replay, prueba fuera de muestra y 5 sesiones en sombra, y siempre se pueden
**Deshacer**. Las instrucciones de agentes y las herramientas nuevas esperan tu aprobación.

## 11. Dónde están tus datos

- Claves, email de contacto: Llavero de macOS.
- Desde el repositorio (`pnpm tauri dev`): `config/local.yaml` y la carpeta `data/`, ambas ignoradas
  por Git.
- Desde la app compilada: `~/Library/Application Support/Hyverion Quant AI/`.

Más detalle en [`docs/SECURITY.md`](../SECURITY.md).

## 12. Actualizar

Con la app cerrada (y el motor detenido si no usas el servicio):

```bash
git pull
uv sync
cd app && pnpm install
```

Después vuelve a abrir la app (`pnpm tauri dev`) o recompílala (`pnpm build:app`). Si usas el motor en
segundo plano, desactívalo y actívalo de nuevo para que cargue el código nuevo. Antes de actualizar
conviene hacer una copia de seguridad verificada: `uv run python -m trading_bot backup`.

## Ayuda

- Estado del sistema: **Ajustes › Salud del sistema** o `uv run python -m trading_bot doctor`.
- Guías de operación: [`spec/guides/`](../../spec/guides/README.md).
- Arquitectura: [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md).
