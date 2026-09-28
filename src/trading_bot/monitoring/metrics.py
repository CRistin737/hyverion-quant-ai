"""Small dependency-free metrics registry for local and VPS runtimes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from threading import Lock


@dataclass(frozen=True, slots=True)
class MetricSample:
    name: str
    value: Decimal
    labels: tuple[tuple[str, str], ...] = ()


class MetricsRegistry:
    """Thread-safe counters/gauges with bounded label cardinality."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], Decimal] = {}
        self._lock = Lock()

    def increment(
        self,
        name: str,
        amount: Decimal = Decimal("1"),
        *,
        labels: Mapping[str, str] | None = None,
    ) -> Decimal:
        key = (name, _labels(labels))
        with self._lock:
            value = self._values.get(key, Decimal("0")) + amount
            self._values[key] = value
            return value

    def set(
        self,
        name: str,
        value: Decimal,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        with self._lock:
            self._values[(name, _labels(labels))] = value

    def snapshot(self) -> tuple[MetricSample, ...]:
        with self._lock:
            return tuple(
                MetricSample(name=name, value=value, labels=labels)
                for (name, labels), value in sorted(self._values.items())
            )


def _labels(labels: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    normalized = tuple(sorted((str(key), str(value)) for key, value in labels.items()))
    if len(normalized) > 8:
        raise ValueError("metric labels are limited to eight keys")
    return normalized
