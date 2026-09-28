# Provider accounts and limits

Hyverion supports two intentionally separate modes:

- **Subscription:** provider-owned local CLI login. No browser cookie, session-token
  extraction or reverse-engineered consumer authentication.
- **API:** explicit provider API key stored in OS Keychain locally or a VPS secret
  manager. The key is never written to YAML, SQLite, prompts or logs.

## Official subscription paths

| Provider | Action | Official reference |
| --- | --- | --- |
| OpenAI / Codex | `codex login` | [Codex CLI and Sign in with ChatGPT](https://help.openai.com/en/articles/11381614-api-codex-cli-and-sign-in-with-chatgpt) |
| Claude Code | `claude auth login` | [Claude Code setup](https://docs.anthropic.com/en/docs/claude-code/getting-started) |
| Grok / xAI | `grok login` (OAuth is the default; use `grok login --device-auth` for a headless device flow) | [Grok authentication](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/02-authentication.md) |
| Gemini | Gemini CLI “Sign in with Google” | [Gemini CLI authentication](https://github.com/google-gemini/gemini-cli/blob/main/docs/get-started/authentication.mdx) |

Gemini API mode remains available separately through [Google's Gemini key guide](https://ai.google.dev/gemini-api/docs/api-key).
Subscription mode uses only the provider-owned CLI and its [documented headless JSON output](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md).

Grok is invoked with the installed CLI's bounded headless surface: one prompt,
JSON/schema output, plan permission mode, read-only sandbox, no subagents, no
web search and no enabled tools. If the installed CLI rejects any of these
official flags, the provider fails closed and the router tries the next
configured subscription.

## In the app

1. Open **Proveedores**.
2. Select the card for the provider.
3. Use **Conectar cuenta** for the official CLI flow, or **Configurar API** for API mode.
4. Use **Comprobar** to perform a provider-specific healthcheck.
5. Use **Límites** to read official subscription status and usage when the provider exposes it.
6. Use **Probar cadena de suscripción** to run one bounded structured PAPER
   probe through the single `ModelRouter`. It records the attempted provider
   order and never creates a trade or an execution intent.

The app displays `UNKNOWN` whenever a context, session, four-hour or weekly quota is
not published through a verifiable machine-readable source. A local token estimate is
not a vendor quota and must not be shown as one.

## Failure behavior

Provider failures stop new AI-dependent proposals. Deterministic stops, risk rules
and existing protective exchange-native orders continue. If all providers fail, the
cycle is `NO_TRADE`.

## Login and failover (2026-09-26)
- The app is subscription-only. **Iniciar sesión** makes the core launch the provider's official CLI login
  (`claude auth login`, `codex login`, `grok login`), open the official sign-in link (only vetted hosts) and
  verify the connection when the CLI exits. Gemini needs an interactive terminal: the app shows the command.
  Progress: `GET /api/v1/providers/{id}/login`; cancel: `POST .../login/cancel`; 5-minute timeout.
- **Circuit breaker** (`providers/circuit.py`): a provider that returns auth-required, quota-exhausted,
  rate-limited, overloaded or timeout is skipped for an escalating cooldown; the next configured subscription
  answers immediately. After the cooldown one probe restores the primary automatically. If every hop is paused
  the cycle is `NO_TRADE`; deterministic exits continue.
