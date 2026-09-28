# Security Policy

Hyverion Quant AI trades real market data against a broker account (PAPER only;
LIVE is locked). Please treat security problems seriously and report them
privately.

## Reporting a vulnerability

**Do not open a public issue, pull request or discussion for a vulnerability.**

Report it privately through GitHub Security Advisories:

1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability**
   (direct link: <https://github.com/CRistin737/hyverion-quant-ai/security/advisories/new>).
3. Describe the problem, the affected commit or version, reproduction steps and
   the impact you expect.

Do not include real API keys, broker credentials or personal data in the report.
If a proof of concept needs credentials, use PAPER/sandbox keys you revoke
afterwards.

The maintainer ([@CRistin737](https://github.com/CRistin737)) aims to
acknowledge reports within 7 days and to agree on a disclosure date with you once
a fix is available. This is a volunteer project, so timelines are best effort.

## Supported versions

| Version | Supported |
|---|---|
| `main` (latest commit) | Yes |
| Older tags and forks | No |

Fixes land on `main` and in the next release.

## Scope

In scope:

- The Python core (`src/trading_bot`): risk engine, execution and broker
  adapters, the control API, agent and provider integrations, secret handling.
- The macOS desktop app (`app/`, including the Tauri shell in `app/src-tauri`).
- CI workflows, release artifacts and the Docker image built from this repository.
- Anything that could bypass a hard rail described in [AGENTS.md](AGENTS.md):
  unlocking LIVE, skipping the risk engine, submitting orders outside
  `ExecutionEngine`, removing native stops, leaking secrets, or letting untrusted
  news/social text act as instructions (prompt injection).

Out of scope:

- Trading losses, strategy performance or model quality.
- Vulnerabilities in third-party services (broker, AI providers, data vendors);
  report those to the vendor.
- Issues that require an already compromised machine or keychain.
- Self-inflicted misconfiguration, such as committing your own keys.

## Security model

How secrets, local data, the PAPER/LIVE boundary and logging are designed is
documented in [docs/SECURITY.md](docs/SECURITY.md).
