# AI Providers

Agents depend only on the `AIProvider.invoke` contract. Routing uses three roles configured in
`config/providers.yaml` (`ai.models`) and changeable from the app without a restart:
`analysis` (Sonnet 5 by default), `decision` (Opus 5.5: strategy and critic) and `improvement`
(Opus 5.5: nightly review). Only the primary subscription uses the chosen model; fallbacks run
their CLI default. There is no USD budget: subscription plans enforce their own 5-hour and
weekly limits (`providers/usage.py`), and a provider at its limit is skipped by the circuit
breaker. No model name is hard-coded in an agent.

Supported adapters:

- OpenAI Responses structured outputs through the optional official SDK.
- Codex CLI subscription mode through official local login and non-interactive schema output.
- Claude Code subscription mode through `claude auth login` and its documented print/JSON schema
  interface.
- Grok subscription mode through `grok login` (OAuth default) and its documented single-turn JSON output
  interface.
- Gemini subscription mode through the provider-owned Gemini CLI Google-account login and
  documented headless JSON output. The response envelope is validated against the requested
  Pydantic schema before it can reach an agent.
- Anthropic Messages structured outputs through `messages.parse`.
- xAI through its documented OpenAI-compatible endpoint.
- Gemini through the official Google GenAI Interactions API.

Relevant official references: [OpenAI Codex CLI sign-in](https://help.openai.com/en/articles/11381614-api-codex-cli-and-sign-in-with-chatgpt),
[Codex app-server authentication](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md),
[Claude Code authentication](https://docs.anthropic.com/en/docs/claude-code/getting-started),
[xAI REST authentication](https://docs.x.ai/developers/rest-api-reference/inference),
[Gemini API keys](https://ai.google.dev/gemini-api/docs/api-key), and
[Gemini OAuth](https://ai.google.dev/gemini-api/docs/oauth), plus the
[Gemini CLI authentication](https://github.com/google-gemini/gemini-cli/blob/main/docs/get-started/authentication.mdx)
and [headless JSON contract](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md).

Install API adapters with `uv sync --extra providers`. A profile must provide a model identifier
when API mode is selected. In subscription mode, an empty profile model means "use the model
selected by the provider CLI"; no model name is hard-coded into an agent.
API-billed profiles must also provide current input/output cost rates; unknown cost fails closed.
Subscription runs record usage/latency with cost `null` instead of inventing a dollar value.

The Provider Center's **Check subscription** action invokes only the provider-owned local CLI:

| Provider | Login | Headless status | Usage/quota truth |
| --- | --- | --- | --- |
| OpenAI / Codex | `codex login` | `codex login status` | Authentication is reported; context/session/4h/weekly quota stays `UNKNOWN` unless an official machine-readable source is supplied. |
| Claude | `claude auth login` | `claude auth status` + the official local `claude -p /usage --output-format json` command | Authentication, plan, current-session remaining, weekly remaining and reset labels are shown when `/usage` returns them. |
| Grok / xAI | `grok login` (OAuth default; `--device-auth` for headless setup) | `grok --version` only proves installation; bounded headless request verifies access | Status and quota stay `UNKNOWN` until xAI exposes an official machine-readable source. |
| Gemini | Gemini CLI “Sign in with Google” | `gemini --version` only proves installation; request access is verified by the bounded CLI call | Account/quota remains `UNKNOWN` unless Google exposes an official machine-readable source. |

This is intentional. The app does not scrape CLI screens, browser sessions, cookies, private
endpoints, or local credential files. A visible `UNKNOWN` is safer and more accurate than a
number that cannot be proven. API mode is a separate explicit choice; subscription mode never
silently falls back to an API key.

The Grok subscription adapter uses the provider CLI's bounded headless options:
single prompt, JSON/schema output, plan permission mode, read-only sandbox, no
subagents, disabled web search and an empty tool allowlist. A rejected command
or invalid schema is a provider failure and follows the ordered subscription
fallback chain.

There is exactly one primary provider. Fallbacks are an ordered, unique subscription chain and
are selected by the single `ModelRouter` gateway. Fallback occurs for bounded provider,
transport, timeout, rate-limit, or structured-output failures. The router records the attempted
provider IDs and safe error codes in model usage/audit metadata. If every provider fails, the
result is NO_TRADE. Deterministic position protection remains active.

In subscription mode, the native Provider Center launches only the provider-owned CLI. If that
CLI is missing, the card shows an install/authentication blocker; it never redirects to an API
console or silently changes billing mode. API mode is an explicit separate choice: its keys are
entered once in the password dialog and stored in the OS Keychain under the
`hyverion-quant-ai` service.

Optional local quota/context values live in `config/providers.yaml` under `ai.provider_limits`:
`context_window`, `session_limit`, `four_hour_limit`, and `weekly_limit`. Leave them `null` when
the provider does not expose an official quota API; the UI deliberately shows `UNKNOWN` rather
than inventing subscription limits. Each row also shows whether the value is `CONFIGURED`,
`OFFICIAL`, or `UNKNOWN`.
