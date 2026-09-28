"""RatesContextService (§23) and the daily VIX close, from FRED (free key).

Series: ``DGS2`` and ``DGS10`` (Treasury yields), ``T10Y2Y`` (curve slope),
``DTWEXBGS`` (broad dollar index) and ``VIXCLS`` (Cboe VIX close, published on
FRED). Daily data: a context, never an intraday trigger. The relationship with
QQQ is *measured* by regime later (§23), never assumed ("yields up = QQQ down"
is exactly the kind of rule we must not hard-code).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from trading_bot.schemas.common import StrictSchema
from trading_bot.sources.fetch import FetchError, SafeFetcher

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
SERIES = {
    "DGS2": "Tipo a 2 años",
    "DGS10": "Tipo a 10 años",
    "T10Y2Y": "Pendiente 10a-2a",
    "DTWEXBGS": "Dólar amplio",
    "VIXCLS": "VIX (cierre)",
}


class SeriesPoint(StrictSchema):
    series_id: str
    label: str
    as_of: date
    value: Decimal
    change_1d: Decimal | None
    change_5d: Decimal | None


class RatesContext(StrictSchema):
    status: str  # "ok" | "key_missing" | "unavailable"
    series: tuple[SeriesPoint, ...] = ()
    error: str | None = None

    def value(self, series_id: str) -> Decimal | None:
        return next((p.value for p in self.series if p.series_id == series_id), None)


def parse_fred_observations(series_id: str, raw: bytes) -> SeriesPoint | None:
    data: dict[str, Any] = json.loads(raw)
    points: list[tuple[date, Decimal]] = []
    for row in data.get("observations", []):
        try:
            points.append((date.fromisoformat(row["date"]), Decimal(row["value"])))
        except (KeyError, ValueError, InvalidOperation):
            continue  # FRED marks missing days with "."
    if not points:
        return None
    points.sort()
    latest_day, latest = points[-1]

    def change(lag: int) -> Decimal | None:
        return latest - points[-1 - lag][1] if len(points) > lag else None

    return SeriesPoint(
        series_id=series_id,
        label=SERIES.get(series_id, series_id),
        as_of=latest_day,
        value=latest,
        change_1d=change(1),
        change_5d=change(5),
    )


async def fetch_rates_context(
    fetcher: SafeFetcher, api_key: str | None, *, today: date
) -> RatesContext:
    if not api_key:
        return RatesContext(status="key_missing")
    points: list[SeriesPoint] = []
    for series_id in SERIES:
        try:
            raw = await fetcher.get(
                FRED_URL,
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "observation_start": (today - timedelta(days=21)).isoformat(),
                },
                check_robots=False,
            )
        except FetchError as exc:
            return RatesContext(status="unavailable", series=tuple(points), error=exc.code)
        point = parse_fred_observations(series_id, raw)
        if point is not None:
            points.append(point)
    return RatesContext(status="ok" if points else "unavailable", series=tuple(points))
