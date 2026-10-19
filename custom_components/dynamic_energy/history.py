"""House load profile from recorder long-term statistics (hourly means)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .forecast import LoadProfile, build_load_profile

_LOGGER = logging.getLogger(__name__)

HISTORY_DAYS = 28
_UNIT_TO_KW = {"W": 0.001, "kW": 1.0, "MW": 1000.0}


def _hourly_kw(
    rows: list[dict], factor: float
) -> dict[datetime, float]:
    result: dict[datetime, float] = {}
    for row in rows:
        mean = row.get("mean")
        start = row.get("start")
        if mean is None or start is None:
            continue
        moment = datetime.fromtimestamp(float(start), tz=timezone.utc)
        result[moment] = float(mean) * factor
    return result


def _fetch(hass: HomeAssistant, statistic_ids: set[str]) -> dict[str, list[dict]]:
    end = dt_util.utcnow()
    return statistics_during_period(
        hass, end - timedelta(days=HISTORY_DAYS), end, statistic_ids, "hour", None, {"mean"}
    )


async def async_load_profile(
    hass: HomeAssistant,
    house_entity: str,
    ev_entity: str | None,
    fallback_kw: float,
) -> LoadProfile | None:
    """Median house load per weekday class and hour, without the EV share.

    Returns None when the history is unusable (no recorder data, unknown unit),
    so the caller falls back to a flat load.
    """
    wanted = {house_entity} | ({ev_entity} if ev_entity else set())
    try:
        stats = await get_instance(hass).async_add_executor_job(_fetch, hass, wanted)
    except Exception:  # noqa: BLE001 - history is optional; never break the plan
        _LOGGER.exception("Could not read house load statistics")
        return None

    def factor_for(entity_id: str) -> float | None:
        state = hass.states.get(entity_id)
        unit = state.attributes.get("unit_of_measurement") if state else None
        return _UNIT_TO_KW.get(unit)

    house_factor = factor_for(house_entity)
    house_rows = stats.get(house_entity)
    if house_factor is None or not house_rows:
        return None
    house = _hourly_kw(house_rows, house_factor)
    ev: dict[datetime, float] = {}
    if ev_entity:
        ev_factor = factor_for(ev_entity)
        if ev_factor is not None and stats.get(ev_entity):
            ev = _hourly_kw(stats[ev_entity], ev_factor)
    samples = [
        (dt_util.as_local(moment), max(0.0, kw - ev.get(moment, 0.0)))
        for moment, kw in house.items()
    ]
    if len(samples) < 24:
        return None
    return build_load_profile(samples, fallback_kw)
