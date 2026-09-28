# Broker paper: Alpaca Paper y conciliación

Hyverion opera **solo QQQ**, **solo en simulación (paper)** y **solo en el horario regular** de la bolsa de Nueva York. Puede usar dos brokers:

| Broker | Qué es | Cuándo usarlo |
|---|---|---|
| **Simulador interno** (por defecto) | Las órdenes se simulan dentro de la app. No necesita cuenta. | Para desarrollo y demostraciones. |
| **Alpaca Paper** | Una cuenta paper de Alpaca: dinero ficticio en un broker real. | Para probar el flujo completo contra un broker de verdad. |

El modo real (LIVE) no existe en esta versión. No hay adaptador de live, no hay variable de entorno para claves de live y ningún nombre del Llavero puede guardarlas.

## Cómo conectar Alpaca Paper

**No pegues las claves en el chat, en issues, en Git ni en los registros.**

1. Crea o abre tu cuenta en [app.alpaca.markets](https://app.alpaca.markets/). Es la **Trading API** (para operar tu propia cuenta), no la Broker API, que es para empresas.
2. Arriba a la izquierda, cambia a **Paper Trading**.
3. En el panel de la cuenta paper, en **API Keys**, pulsa **Generate New Keys**. Copia el **API Key ID** y el **Secret Key**. El secreto solo se muestra una vez.
4. En Hyverion abre **Ajustes › Cuenta de trading**:
   - pega las dos claves en **Claves de Alpaca Paper**; se guardan en el Llavero de macOS con los nombres `broker:alpaca_paper:key_id` y `broker:alpaca_paper:secret_key`;
   - en **Broker**, elige **Alpaca Paper** y pulsa **Validar y aplicar**.

   En un servidor sin app, usa en cambio las variables `ALPACA_PAPER_KEY_ID` y `ALPACA_PAPER_SECRET_KEY` en `.env`.
5. Comprueba la conexión:

   ```bash
   uv run python -m trading_bot broker status     # debe decir "environment": "paper" y "connected": true
   uv run python -m trading_bot broker reconcile  # debe terminar en "status": "OK"
   uv run python -m trading_bot doctor            # Broker CONNECTED · Environment PAPER · Live DISABLED
   ```

### Cómo sabes que es PAPER

- El adaptador solo acepta el host `paper-api.alpaca.markets`. Si se apunta a cualquier otro, falla con `broker_live_host_forbidden` antes de enviar nada.
- Rechaza cualquier orden que no sea de modo PAPER (`broker_paper_only`) y cualquier venta en corto (`broker_shorting_disabled`).
- `broker status` muestra `"environment": "paper"` y la cuenta enmascarada (por ejemplo `PA…9F2K`).

Las mismas claves paper dan acceso a los precios de QQQ (feed **IEX**, gratuito y en tiempo real). IEX cubre solo una parte del volumen de todas las bolsas: cada cotización lleva `provider=alpaca` y `feed=iex`.

## Cómo opera

- **Entrada:** orden limitada de día (admite fracciones de acción). Si no se llena en unos segundos, el resto se cancela.
- **Protección nativa:** en cuanto hay acciones compradas, Hyverion coloca en Alpaca un **stop de venta** propio (`<id de la orden>-stop`). La protección no depende de que el motor ni ninguna IA estén activos. Si el stop no se puede colocar, la posición se cierra de inmediato (fallo cerrado).
- **Salida:** primero cancela ese stop y después vende a mercado. Así nunca se vende dos veces.
- **Fin del día:** las posiciones se cierran antes del cierre (10 minutos por defecto). Hyverion no deja posiciones abiertas de un día para otro.
- **Tiempo de espera:** si Alpaca no responde al enviar una orden, el resultado se considera **desconocido**. Antes de reintentar se consulta la orden por su `client_order_id`, nunca se reenvía a ciegas.
- **Tamaño:** se calcula por riesgo y se redondea **hacia abajo** al paso que admite el broker. Si queda en 0, no se opera (`QUANTITY_BELOW_MINIMUM`).

## Conciliación y modo seguro

Al arrancar el motor, y después cada 10 minutos, Hyverion compara lo que tiene Alpaca con su registro local:

- posiciones;
- órdenes abiertas (sin contar sus propios stops protectores);
- ejecuciones recientes.

El saldo no se compara, pero en cada conciliación se **sincroniza el patrimonio**: la cuenta paper de Alpaca *es* el capital de Hyverion (no hay capital configurado). Si la última sincronización tiene más de 15 minutos, no se abren entradas (`broker_equity_unavailable`). Los topes en dólares de `config/risk.yaml` (10 USD por operación, 25 al día, 75 a la semana) siguen mandando aunque la cuenta tenga 100 000 USD.

Si algo no cuadra, o si Alpaca no responde, se registra `RECONCILIATION_MISMATCH` o `RECONCILIATION_ERROR`. **No se abren operaciones nuevas** hasta que una conciliación salga bien. Nunca se crean ni se cancelan órdenes para «arreglar» una diferencia.

> Usa una cuenta paper **dedicada** a Hyverion. Una posición que abras a mano en esa cuenta aparecerá como diferencia y bloqueará las entradas.

## Limitaciones del paper (§139)

El paper es una simulación y no garantiza resultados en real:

- no modela el impacto de tus órdenes en el mercado ni tu lugar en la cola;
- el deslizamiento y las ejecuciones parciales pueden diferir de los reales;
- los datos IEX no son los consolidados (SIP);
- con menos de 25 000 USD en una cuenta real aplicaría la regla de «pattern day trader» (EE. UU.). La cuenta paper puede no reflejarla igual.

Por eso la evaluación de estrategias usa un modelo de costes propio (escenarios optimista, base y estrés) y no solo el resultado del broker.

## Interactive Brokers (IBKR): decisión

Verificado en la documentación oficial de IBKR (2026-09-27):

- IBKR **solo da acceso a la cuenta paper después de abrir una cuenta real aprobada y con fondos**.
- Las cuentas de prueba gratuitas (*trial*) **no** pueden usar ninguna de sus API.

No hay atajo para evitarlo. Por eso IBKR Paper queda preparado solo a nivel de diseño (la interfaz `BrokerAdapter` con capacidades declaradas) y su adaptador se hará cuando exista esa cuenta.

Método recomendado: **TWS API vía IB Gateway**.

| Criterio | TWS API vía IB Gateway | Web API |
|---|---|---|
| Uso | Local o en un VPS, con reconexión y streaming | — |
| Sesión | Estable | Exige reautenticación periódica en navegador |
| Stops nativos | Sí (OCA/bracket) | — |

Paper y live seguirán siendo perfiles distintos, con puertos y credenciales separados.

Fuentes:
- [About Paper Trading Accounts](https://www.ibkrguides.com/clientportal/aboutpapertradingaccounts.htm)
- [TWS API: Paper Trading](https://www.interactivebrokers.com/docs/tws-api/doc/notes-limitations/limitations/paper-trading)
- [Web API: Using a Paper Account](https://www.interactivebrokers.com/docs/web-api/authentication/paper)

## Elegibilidad por país (§142)

Hyverion no asume que el modo real de un broker esté disponible en tu país. Antes de cualquier modo real hay que verificar la elegibilidad en los términos oficiales vigentes del broker. El desarrollo en paper puede continuar mientras tanto.
