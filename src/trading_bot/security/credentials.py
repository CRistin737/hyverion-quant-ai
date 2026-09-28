"""Broker credentials: PAPER profile only, resolved from env or the OS keychain.

There is deliberately no live profile: no environment variable, keychain name
or settings field exists for live keys, so a test or a misconfiguration cannot
load one (§128 of the QQQ plan). Values are wrapped so ``repr``/logs never show
them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trading_bot.config.models import SecretSettings
from trading_bot.security.secrets import KeyringSecretStore, SecretStore

__all__ = [
    "ALPACA_PAPER_KEY_ID",
    "ALPACA_PAPER_SECRET_KEY",
    "AlpacaPaperCredentials",
    "KeyringSecretStore",
    "alpaca_paper_credentials",
]

ALPACA_PAPER_KEY_ID = "broker:alpaca_paper:key_id"
# Keychain entry *names*, not secrets.
ALPACA_PAPER_SECRET_KEY = "broker:alpaca_paper:secret_key"  # noqa: S105


@dataclass(frozen=True, slots=True)
class AlpacaPaperCredentials:
    key_id: str = field(repr=False)
    secret_key: str = field(repr=False)

    @property
    def masked_key_id(self) -> str:
        return f"{self.key_id[:4]}…{self.key_id[-2:]}" if len(self.key_id) > 6 else "…"

    def headers(self) -> dict[str, str]:
        return {"APCA-API-KEY-ID": self.key_id, "APCA-API-SECRET-KEY": self.secret_key}


def alpaca_paper_credentials(
    secrets: SecretSettings, store: SecretStore | None = None
) -> AlpacaPaperCredentials | None:
    """Environment first (headless/VPS), then the keychain the desktop app writes."""

    key_id = secrets.alpaca_paper_key_id.get_secret_value() if secrets.alpaca_paper_key_id else ""
    secret = (
        secrets.alpaca_paper_secret_key.get_secret_value()
        if secrets.alpaca_paper_secret_key
        else ""
    )
    if not (key_id and secret):
        keychain = store or KeyringSecretStore()
        key_id = key_id or keychain.get(ALPACA_PAPER_KEY_ID) or ""
        secret = secret or keychain.get(ALPACA_PAPER_SECRET_KEY) or ""
    key_id, secret = key_id.strip(), secret.strip()
    if not key_id or not secret:
        return None
    return AlpacaPaperCredentials(key_id=key_id, secret_key=secret)
