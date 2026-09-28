from __future__ import annotations

from typing import Protocol

import keyring
from keyring.errors import KeyringError, PasswordDeleteError


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...

    def has(self, name: str) -> bool: ...


class KeyringSecretStore:
    def __init__(self, service_name: str = "hyverion-quant-ai") -> None:
        self._service_name = service_name

    def get(self, name: str) -> str | None:
        try:
            return keyring.get_password(self._service_name, name)
        except KeyringError:
            # A headless VPS may not have an OS keychain.  Callers can then
            # surface an explicit unavailable state instead of crashing.
            return None

    def set(self, name: str, value: str) -> None:
        if not name.strip() or not value:
            raise ValueError("secret name and value are required")
        try:
            keyring.set_password(self._service_name, name, value)
        except KeyringError as exc:
            raise RuntimeError("secure secret backend is unavailable") from exc

    def delete(self, name: str) -> None:
        try:
            keyring.delete_password(self._service_name, name)
        except PasswordDeleteError:
            return
        except KeyringError as exc:
            raise RuntimeError("secure secret backend is unavailable") from exc

    def has(self, name: str) -> bool:
        return self.get(name) is not None
