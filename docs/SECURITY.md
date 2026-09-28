# Security Model

## Secrets

- `.env` and `config/local.yaml` are ignored by Git.
- The wizard writes non-secret configuration only.
- `KeyringSecretStore` is the preferred local secret boundary.
- VPS secrets must be injected at runtime or mounted through a secret manager.
- Logs redact keys containing secret, token, password, API key, passphrase, or credential.
- Broker keys are Alpaca PAPER keys; LIVE stays locked.

## Where your data lives

Nothing personal is ever part of the repository:

| Data | Where | Versioned |
|---|---|---|
| API keys, broker keys, contact e-mail | macOS Keychain, service `hyverion-quant-ai` | never |
| Settings you choose in the app | `~/Library/Application Support/Hyverion Quant AI/config/local.yaml` | never |
| Trades, decisions, agent runs, memory | `~/Library/Application Support/Hyverion Quant AI/data/` (SQLite) | never |
| Backups | `~/Library/Application Support/Hyverion Quant AI/backups/` | never |
| Engine log | `.../Hyverion Quant AI/data/engine.log` (identifiers and decisions, no credentials) | never |

`.gitignore` excludes databases, `*.bak`, `config/local*.yaml`, `.env` and
exports, and `scripts/privacy_check.py` (run in CI) fails if a tracked file
contains a personal e-mail, a home-directory path or a secret-shaped string.

## Prompt injection

News/social collectors run outside the model. External text is length-limited, NUL-stripped, and
wrapped in `UNTRUSTED_EXTERNAL_CONTENT`. Agent specs explicitly prohibit following embedded
instructions. Agents receive no shell, filesystem, private exchange endpoints, or credentials.

## Process isolation

Subscription adapters use only provider-owned headless CLIs (`codex exec`, `claude -p`, and
`grok --single`) in a temporary directory with schema-constrained output and no model tools. They
strip all configured API-key environment variables before spawning the process. They check the
official login status where documented but never read or copy cached authentication files. API
adapters use keys only in their own process boundary, and subscription mode never falls back to
them silently. Claude's read-only `/usage` probe is a local CLI command with zero model turns; its
session/week percentages and reset labels are accepted only when the CLI emits them as structured
JSON. Codex/Grok remain `UNKNOWN` for quotas when no official machine-readable value exists.

Docker runs as a non-root user, with a read-only root filesystem, `no-new-privileges`, explicit
writable volumes, and localhost-only control API publishing. The native terminal is the only
visual product surface.

Remote control API binding requires `API_ALLOW_REMOTE_BIND=true` and a non-empty
`CONTROL_API_TOKEN`; state routes require `Authorization: Bearer ...`. Local loopback mode can run
without a token and is not intended to be exposed outside the machine.
