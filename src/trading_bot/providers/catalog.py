"""Models the owner can pick per role, per subscription.

Only the primary subscription uses the chosen model; fallbacks run their CLI
default. ``default`` always means "whatever the provider's CLI picks".
Any other valid model name can still be typed in the app.
"""

from __future__ import annotations

MODEL_CATALOG: dict[str, tuple[tuple[str, str], ...]] = {
    "anthropic": (
        ("claude-sonnet-5", "Sonnet 5"),
        ("claude-opus-5-5", "Opus 5.5"),
        ("claude-fable-5-1", "Fable 5.1"),
        ("claude-haiku-4-5-20251001", "Haiku 4.5"),
        ("default", "Predeterminado de Claude Code"),
    ),
    "openai": (("default", "Predeterminado de Codex"),),
    "xai": (("default", "Predeterminado de Grok"),),
    "gemini": (("default", "Predeterminado de Gemini"),),
}

ROLE_LABELS: dict[str, str] = {
    "analysis": "Análisis",
    "decision": "Decisión",
    "improvement": "Mejora",
}


def catalog_payload() -> dict[str, list[dict[str, str]]]:
    return {
        provider: [{"id": model_id, "label": label} for model_id, label in models]
        for provider, models in MODEL_CATALOG.items()
    }
