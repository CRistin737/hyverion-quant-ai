from __future__ import annotations

import pytest

from trading_bot.data.sources import SourceDefinition, SourceRegistry


def _definition(**overrides: object) -> SourceDefinition:
    values: dict[str, object] = {
        "source_id": "regulator-sec",
        "category": "news",
        "region": "US",
        "transport": "rss",
        "base_url": "https://example.gov/feed.xml",
        "allowed_hosts": ("example.gov",),
        "parser_version": "1.0.0",
        "compliance_status": "APPROVED",
        "enabled": True,
    }
    values.update(overrides)
    return SourceDefinition(**values)


def test_registry_requires_explicit_approval_before_fetch() -> None:
    registry = SourceRegistry((_definition(compliance_status="PENDING_REVIEW"),))

    with pytest.raises(ValueError, match="source_not_reviewed"):
        registry.require_fetchable("regulator-sec")


def test_html_requires_explicit_policy_flag() -> None:
    registry = SourceRegistry((_definition(transport="html", html_allowed=False),))

    with pytest.raises(ValueError, match="html_not_allowed"):
        registry.require_fetchable("regulator-sec")


def test_registry_rejects_duplicate_source_ids() -> None:
    registry = SourceRegistry((_definition(),))

    with pytest.raises(ValueError, match="already registered"):
        registry.add(_definition())


def test_source_urls_and_hosts_are_validated() -> None:
    with pytest.raises(ValueError, match="absolute HTTP"):
        _definition(base_url="file:///tmp/feed.xml")
    with pytest.raises(ValueError, match="hostnames only"):
        _definition(allowed_hosts=("example.gov/path",))
