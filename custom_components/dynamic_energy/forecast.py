"""Pure forecast helpers: PV forecast, house load profile and planner inputs."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Iterable

from .battery_planner import QuarterInput
from .planner import PriceSlot

QUARTER = timedelta(minutes=15)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None else None


def parse_pv_forecast(
    attribute_lists: Iterable[Any], key: str = "pv_estimate"
) -> list[tuple[datetime, datetime, float]]:
    """Solcast `detailedForecast` rows -> (start, end, average kW).

    Rows carry `period_start` and a kW estimate for a 30-minute period. Any
    malformed row discards the forecast so a broken attribute never feeds the
    planner; an empty result means "no PV forecast".
    """
    rows: list[tuple[datetime, float]] = []
    for rows_value in attribute_lists:
        if rows_value is None:
            continue
        if not isinstance(rows_value, list):
            return []
        for row in rows_value:
            if not isinstance(row, dict):
                return []
            start = _as_datetime(row.get("period_start"))
            value = row.get(key)
            if start is None or isinstance(value, bool) or not isinstance(value, (int, float)):
                return []
            if value != value or value < 0:
                return []
            rows.append((start, float(value)))
    if not rows:
        return []
    rows.sort(key=lambda item: item[0])
    result: list[tuple[datetime, datetime, float]] = []
    previous_span: timedelta | None = None
    for index, (start, kw) in enumerate(rows):
        if index + 1 < len(rows):
            gap = rows[index + 1][0] - start
            span = gap if timedelta(0) < gap <= timedelta(hours=1) else previous_span
        else:
            span = previous_span
        if span is None or span <= timedelta(0):
            return []
        result.append((start, start + span, kw))
        previous_span = span
    return result


def pv_kw_at(forecast: list[tuple[datetime, datetime, float]], moment: datetime) -> float | None:
    for start, end, kw in forecast:
        if start <= moment < end:
            return kw
    return None


@dataclass(frozen=True)
class LoadProfile:
    """Median house load (kW) per (weekend?, local hour)."""

    values: dict[tuple[bool, int], float]
    fallback_kw: float

    def kw(self, moment_local: datetime) -> float:
        key = (moment_local.weekday() >= 5, moment_local.hour)
        if key in self.values:
            return self.values[key]
        same_hour = [v for (weekend, hour), v in self.values.items() if hour == moment_local.hour]
        return median(same_hour) if same_hour else self.fallback_kw


def build_load_profile(
    hourly_kw: Iterable[tuple[datetime, float]], fallback_kw: float
) -> LoadProfile:
    """Median per weekday-class and hour of day from hourly mean power samples."""
    buckets: dict[tuple[bool, int], list[float]] = defaultdict(list)
    for start_local, kw in hourly_kw:
        if kw == kw and kw >= 0:
            buckets[(start_local.weekday() >= 5, start_local.hour)].append(kw)
    return LoadProfile(
        values={key: median(values) for key, values in buckets.items() if values},
        fallback_kw=fallback_kw,
    )


def build_quarter_inputs(
    import_slots: list[PriceSlot],
    export_slots: list[PriceSlot],
    now: datetime,
    load: LoadProfile,
    pv: list[tuple[datetime, datetime, float]],
    export_fallback: float,
    to_local,
    pv_scale: float = 1.0,
) -> tuple[list[QuarterInput], list[str]]:
    """Contiguous quarter inputs from the current quarter to the end of the prices.

    Returns the inputs and quality flags (never silently drops data).
    """
    flags: list[str] = []
    if not import_slots:
        return [], ["NO_IMPORT_PRICES"]
    export_by_start = {slot.start: slot.price for slot in export_slots}
    if not export_slots:
        flags.append("NO_EXPORT_PRICES_FALLBACK_USED")
    if not pv:
        flags.append("NO_PV_FORECAST")
    quarters: list[QuarterInput] = []
    cursor: datetime | None = None
    for slot in sorted(import_slots, key=lambda item: item.start):
        if slot.end <= now:
            continue
        # Split slots (hourly contracts) into quarters aligned with the slot start.
        start = slot.start
        while start < slot.end:
            end = min(start + QUARTER, slot.end)
            if end > now:
                if cursor is not None and start != cursor:
                    flags.append("GAP_IN_PRICES")
                    return quarters, flags
                # The current quarter is planned as a whole so the inputs stay
                # constant within a quarter and the plan can be cached.
                hours = (end - start).total_seconds() / 3600
                mid = start + (end - start) / 2
                kw_pv = pv_kw_at(pv, mid)
                kw_load = load.kw(to_local(mid))
                net_kw = kw_load - (kw_pv or 0.0) * pv_scale
                quarters.append(
                    QuarterInput(
                        start=start,
                        end=end,
                        base_net_kwh=net_kw * hours,
                        import_rate=slot.price,
                        export_rate=export_by_start.get(start, export_by_start.get(slot.start, export_fallback)),
                        slot_start=start,
                    )
                )
                cursor = end
            start = end
    return quarters, sorted(set(flags))
