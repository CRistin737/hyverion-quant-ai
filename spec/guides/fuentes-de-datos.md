# Fuentes de datos gratuitas

Hyverion opera **solo QQQ**, pero decide con más información:

- el calendario económico;
- el Nasdaq-100 completo;
- los tipos de interés y la volatilidad;
- las noticias y los documentos de las empresas.

Todas estas fuentes son **gratuitas**. Se configuran en **Noticias › Fuentes**: pegas la clave (si hace falta) y pulsas **Probar**.

Las claves y tu email se guardan en el Llavero de macOS. Nunca van a Git, a los registros ni a la base de datos. **No las pegues en el chat.**

## Qué necesitas

| Fuente | Para qué | Qué pide | Cómo conseguirlo |
|---|---|---|---|
| Alpaca (precios, noticias, opciones) | Precios de QQQ y de los 100 componentes, noticias (Benzinga) y volatilidad implícita | Las claves de Alpaca Paper | Ya las tienes (Ajustes › Cuenta de trading) |
| **Email de contacto** | La SEC y el BLS solo atienden programas que se identifican con un email | Tu email | Escríbelo en el onboarding o en Noticias › Fuentes |
| Reserva Federal | Reuniones del FOMC, ruedas de prensa, actas, discursos y comparecencias (Powell, gobernadores) | Nada | Automático |
| BLS | Fechas y horas de CPI, empleo (NFP), PPI y JOLTS | El email de contacto | Automático |
| BEA | Fechas de PIB y PCE | Nada | Automático |
| SEC EDGAR | Cartera oficial de QQQ (N-PORT) y documentos 8-K, 10-Q y 10-K de los componentes | El email de contacto | Automático |
| FRED | Tipos a 2 y 10 años, pendiente de la curva, dólar y VIX (cierre diario) | Clave gratuita | [fredaccount.stlouisfed.org/apikeys](https://fredaccount.stlouisfed.org/apikeys) › *Request API Key* |
| Finnhub | Fechas de resultados de los componentes | Clave gratuita | [finnhub.io/register](https://finnhub.io/register): la clave aparece en tu panel |

**Sin email de contacto el sistema no abre operaciones.** El calendario del BLS (CPI, empleo) no se puede leer sin él, y la regla macro falla cerrado: si el calendario tiene más de 7 días, no se abren posiciones.

## Qué hace el sistema con cada dato

| Dato | Uso |
|---|---|
| **Calendario macro** | No abre operaciones desde 15 minutos antes de un evento de alto impacto hasta 15 minutos después. Son alto impacto: decisión del FOMC y su rueda de prensa, CPI, NFP, PCE, PIB avance y discursos del presidente de la Fed. La IA no puede saltarse esta regla. |
| **Nasdaq-100** | La composición sale del informe trimestral oficial de QQQ (N-PORT). Los pesos de hoy son una **estimación**: acciones del informe por precio actual. Con ella se calculan la amplitud (cuántas suben, cuánto pesan, las que están sobre VWAP), la contribución de cada empresa y las divergencias. Por ejemplo: «QQQ sube solo por las megacaps». |
| **Noticias** | 30 copias de la misma noticia cuentan como una. Cada noticia pesa según el peso en QQQ de las empresas que nombra y pierde influencia con el tiempo. El texto externo se trata como dato, nunca como instrucción. |
| **SEC** | Detecta los 8-K, 10-Q y 10-K de las 20 empresas con más peso. Un 8-K con el ítem 2.02 es una publicación de resultados. |
| **Resultados** | Las fechas próximas suben el riesgo de contexto. No bloquean por sí solas. |
| **Volatilidad** | La realizada (de las barras de QQQ) y la implícita (opciones y VIX) se muestran por separado. No se calcula ninguna «gamma de los dealers». |

## Limitaciones

- **Composición del Nasdaq-100.** El informe N-PORT es trimestral y llega con retraso, así que un cambio en el índice entre dos informes no se ve hasta el siguiente. El historial empieza el día en que Hyverion guardó el primer informe (sesgo de supervivencia).
- **Consenso de analistas.** Ninguna fuente oficial gratuita publica el consenso de los datos macro, así que el campo `consensus` queda vacío. **Nunca se inventa.**
- **Calidad de los precios.** IEX (Alpaca gratis) solo cubre parte del volumen, y el feed de opciones es «indicativo», no el oficial OPRA.

## Cómo se lee cada fuente

- Solo por `https` y solo a los dominios de `config/sources.yaml`.
- Para las páginas web se respeta `robots.txt`. Si no se puede leer `robots.txt`, no se lee la página.
- Como mucho, una petición por segundo a cada dominio, con caché (ETag). Es muy por debajo del límite de la SEC, que es de 10 por segundo.
- Cada fuente tiene su categoría de confianza (oficial, agencia, agregador…) y la fecha en que se revisaron sus términos.
- **Invesco no permite descargar automáticamente su lista de posiciones.** Por eso se usa el N-PORT de la SEC y no se hace *scraping* de Invesco.

## Comandos

```bash
uv run python -m trading_bot macro next --days 21   # próximos eventos en hora de Nueva York y estado de la regla macro
uv run python -m trading_bot sources status         # estado de cada fuente
uv run python -m trading_bot sources refresh        # leer todas las fuentes ahora
```
