# Guía de la app de escritorio

Hyverion Quant AI es una app de escritorio Tauri 2 + React (`app/`). La API de control local entrega
estado JSON acotado a través del proxy del shell Rust; no es un dashboard de navegador.

Desde el 2026-09-27 la app tiene **6 áreas y 11 pestañas**. Cada cosa se configura donde se ve y cada
pestaña explica en una frase para qué sirve (`app/src/shell/routes.ts`). Las direcciones antiguas
redirigen solas: por ejemplo, `Laboratorio`, `Ajustes › Riesgo` o `Trading › Posiciones`.

## Leer la barra superior

- La insignia `SIMULACIÓN` está siempre visible. El modo real no está disponible en esta versión.
- La barra de estado muestra el motor (iniciar y detener), el cierre de emergencia, la conciliación y
  la hora de la última actualización en UTC.
- Atajos: ⌘K busca o ejecuta cualquier cosa, incluido «Buscar mejoras», y ⌘1–⌘6 va a cada área.

## Áreas

| Área | Pestañas | Qué responde | Qué se puede hacer |
|---|---|---|---|
| Inicio | — | ¿Qué importa ahora? Patrimonio, protección, última decisión, velas de la última hora de cada moneda y actividad | Solo lectura; enlaces a donde se actúa |
| Trading | Mercado · Operaciones · Órdenes | Precio (velas, barras, Heikin Ashi, línea, área; de 1m a 1D) con entrada, stop, objetivo y compras/ventas; cada operación de principio a fin; cada orden y cada entrada frenada por riesgo | Resolver una operación sin confirmar; nunca crear órdenes |
| Inteligencia | Agentes · Modelos · Fuentes | Quién analiza y con qué versión; qué suscripción de IA se usa; de dónde salen los datos | Ver el historial de versiones y deshacer; elegir la suscripción principal, el respaldo y el presupuesto; activar fuentes |
| Riesgo | Decisiones · Límites · Salud del sistema | Por qué se aprobó o frenó cada operación; cuánto puede arriesgar; si todo está listo | Editar los 6 límites ajustables (el resto es fijo); verificar la cuenta paper del broker |
| Aprendizaje | Cambios · Memoria | Qué mejoras propone el sistema y qué recuerda | Aprobar y aplicar, rechazar o deshacer; buscar mejoras; confirmar o retirar conocimiento |
| Ajustes | una página | Cuenta de trading, apariencia, copia de seguridad y ayuda | Capital, broker (simulador o Alpaca Paper), país y claves de Alpaca Paper |

## Trading › Operaciones

Una fila por operación, que une la operación, su posición y sus ejecuciones (`GET /api/v1/trades`).

- **Arriba:** abiertas ahora, resultado abierto, resultado cerrado de los últimos 7 días y acierto.
- **Abiertas:** entrada, precio actual, una barra con la posición del precio entre el stop y el
  objetivo, el resultado y si la protección está verificada.
- **Cerradas:** entrada → salida, resultado, cómo cerró (objetivo, stop, tiempo, emergencia) y la
  duración.
- **Requieren atención:** operaciones en `DESCONOCIDO`, `MODO SEGURO` o `REQUIERE RECUPERACIÓN`.
  Bloquean las entradas nuevas.

Al pulsar una fila se abre el paso a paso, con estados y ejecuciones.

### Resolver una operación sin confirmar

1. Abre **Requieren atención** y pulsa **Resolver**.
2. Verifica el estado real en el exchange (o, en PAPER, en las órdenes).
3. Elige lo que comprobaste y escribe cómo lo verificaste (3–200 caracteres).
4. Pulsa **Registrar resolución**.

El servidor valida contra la posición local. Una resolución nunca crea, cancela ni reemplaza órdenes
y queda auditada (`OPERATOR_RESOLUTION`).

## Trading › Órdenes

- **KPIs:** órdenes y porcentaje ejecutado, comisiones pagadas, entradas frenadas por riesgo y cuánto
  habrían dado si se hubieran hecho.
- **Filtros:** texto, activo, lado, estado y periodo.
- **Frenadas por riesgo** (`GET /api/v1/orders/rejected`): muestra el motivo del control de riesgo y
  el resultado simulado de cada entrada frenada.

## Inteligencia › Agentes: historial de versiones

Pulsa un agente o una estrategia para ver cada versión con:

- qué cambió y por qué;
- quién lo aprobó y con qué motivo;
- el acierto real mientras estuvo en uso, comparado con la versión anterior.

Las fuentes son `GET /api/v1/components/{id}/history` y `/api/v1/agents/{id}/spec` para el texto de
las instrucciones. La versión activa tiene **Deshacer**. Las ediciones manuales de `AGENT.md` también
quedan registradas como versiones nuevas al arrancar el núcleo.

## Aprendizaje › Cambios: aprobar, rechazar y deshacer

1. **Por revisar** muestra cada propuesta con qué cambia, por qué, la prueba antes → después con datos
   que no se usaron para elegir el ajuste, y su riesgo.
2. **Aprobar y aplicar** (con un motivo corto, ya prellenado) registra `APPROVED → DEPLOYED` y activa
   la versión nueva en el siguiente ciclo del motor.
3. **En uso** lista lo aplicado, con **Deshacer** (`ROLLED_BACK`), que vuelve a la versión anterior.
4. **Búsqueda automática de mejoras** corre una vez al día y también con **Buscar mejoras**. Solo
   propone un cambio si supera la regla campeón/retador fuera de muestra.

Solo se aplican parámetros acotados de estrategia y especificaciones de agentes LLM que no son de
seguridad. Un cambio que toque un límite de riesgo falla con `forbidden_target` antes de escribir
nada. Las propuestas manuales (**Proponer un cambio**) que no son aplicables avanzan paso a paso y
se implementan en el código.

## Inteligencia › Fuentes

- **Mercado:** siempre activa.
- **Noticias:** un interruptor general. Cada fuente de la lista revisada pide su dirección RSS exacta
  y confirmar que revisaste sus condiciones de uso.
- **Redes sociales:** ninguna, X o Reddit, siempre por la API oficial. Sus claves se guardan en el
  Llavero desde la misma pantalla.
- **Frecuencia de consulta:** la espera ante fallos nunca es menor que la frecuencia.
- **Derivados:** bloqueados hasta pasar su auditoría.

Todo se guarda con **Validar y aplicar**.

## Estados vacíos, viejos y de error

- `—` significa que no hay un valor verificado, no cero.
- Un dato de mercado o de una fuente desactualizado es motivo para no operar.
- Si el proveedor de IA falla, se detienen las entradas nuevas que dependen de la IA. Los stops, la
  conciliación y el modo seguro siguen funcionando.
- Cada pantalla de datos tiene estados de carga, vacío y error con **Reintentar**.

## Idioma y accesibilidad

La interfaz está en español. Los eventos guardados, los IDs y los códigos de auditoría son
identificadores estables en inglés.

- Las palabras técnicas (drawdown, spread, deslizamiento, R, fuera de muestra, régimen, bps) tienen
  un `?` con su explicación.
- Todo se maneja con el teclado y ningún estado depende solo del color.
- Las animaciones (entrada escalonada, destello al cambiar un valor) se desactivan con
  `prefers-reduced-motion`.

## Comprobación rápida

```bash
uv run python -m trading_bot doctor
cd app && pnpm tauri dev          # o: uv run python -m trading_bot desktop (app instalada)
cd app && pnpm test:e2e           # flujos: aprobar/deshacer, fuentes, límites, gráfica, operaciones
```
