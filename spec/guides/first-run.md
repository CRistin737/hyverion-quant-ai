# First local run

## 1. Install

```bash
uv sync --extra dev
cp .env.example .env
```

No secret is needed for tests, public market fixtures, backtests or PAPER.

## 2. Configure the $100 PAPER account

```bash
uv run python -m trading_bot setup
```

Choose the paper broker (`simulator` or `alpaca` = Alpaca Paper). The instrument is fixed to
`QQQ`, long-only, regular session. The wizard shows the return percentage implied by USD 10 and
USD 50; it must never relax risk to chase either number.

## 3. Verify and simulate

```bash
uv run python -m trading_bot doctor
uv run python -m trading_bot paper --fixture --capital 100
uv run python -m trading_bot shadow --fixture
uv run python -m trading_bot market status
```

## 4. Abrir la app de escritorio

```bash
cd app && pnpm install && pnpm tauri dev
```

Con la app ya compilada e instalada (`cd app && pnpm build:app`), basta con
`uv run python -m trading_bot desktop`, que abre la app o indica cómo compilarla. La app arranca su
propio core con la API de control en loopback, y la barra de estado inferior muestra la conexión,
el estado de simulación y la hora UTC de actualización.

Si la app ya estaba abierta antes de un cambio de código o configuración, ciérrala y vuelve a
abrirla para que el core use el nuevo contrato de snapshot. La app nunca recarga en caliente lógica
ejecutable de un proceso antiguo.

To observe the opt-in WebSocket path, run `uv run python -m trading_bot run --stream`
in a separate terminal. Duplicate, stale, out-of-order or gapped frames are
audited and skipped; REST polling remains the default until this path has been
observed locally.

## 5. Expected safe state

`PAPER`, `LIVE disabled`, the Alpaca Paper account's equity (no configured capital), and no live credential are
the correct first-run result. Empty positions and `UNKNOWN` provider quotas are
valid states, not failures.

The repository CLI and the frozen macOS bundle intentionally use separate
SQLite locations: the checkout uses `data/trading_bot.db`; the bundle uses
`~/Library/Application Support/Hyverion Quant AI/data/trading_bot.db`. Read the
bundle state through its loopback API instead of copying databases between
profiles.
