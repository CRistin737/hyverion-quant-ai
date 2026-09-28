from __future__ import annotations

from trading_bot.security.secrets import KeyringSecretStore


class MemoryStore(KeyringSecretStore):
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        if not name or not value:
            raise ValueError
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


def test_secret_store_never_returns_presence_as_secret_value() -> None:
    store = MemoryStore()
    assert not store.has("provider:openai:api_key")
    store.set("provider:openai:api_key", "secret-value")
    assert store.has("provider:openai:api_key")
    assert store.get("provider:openai:api_key") == "secret-value"
    store.delete("provider:openai:api_key")
    assert store.get("provider:openai:api_key") is None

