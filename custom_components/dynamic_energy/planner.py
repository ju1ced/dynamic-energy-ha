"""Pure EV charging planner. No Home Assistant imports, no side effects.

The plan is advisory: it only says when charging would be cheapest. Nothing in
this module (or the integration) switches a charger, battery or inverter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

EPSILON = 1e-9
MAX_INFERRED_SLOT = timedelta(hours=1)

STATUS_PLANNED = "PLANNED"
STATUS_TARGET_REACHED = "TARGET_REACHED"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_DISABLED = "DISABLED"
STATUS_NO_PRICES = "NO_PRICES"


@dataclass(frozen=True)
class PriceSlot:
    start: datetime
    end: datetime
    price: float  # EUR/kWh, all-in


@dataclass(frozen=True)
class SlotContext:
    """Grid net load (kW, house + battery, positive = import) and export price."""

    base_net_kw: float
    export_price: float


@dataclass(frozen=True)
class PlanSettings:
    departure: datetime
    needed_kwh: float
    ev_max_kw: float
    max_grid_import_kw: float
    base_load_kw: float
    price_ceiling: float


@dataclass(frozen=True)
class PlannedSlot:
    start: datetime
    end: datetime
    kw: float
    kwh: float
    price: float
    reason: str


@dataclass(frozen=True)
class EvPlan:
    status: str
    slots: tuple[PlannedSlot, ...]
    needed_kwh: float
    planned_kwh: float
    unmet_kwh: float
    cost_eur: float
    unmanaged_cost_eur: float
    required_ceiling: float | None
    blocked_by_ceiling: int
    blocked_by_limit: int
    prices_cover_until: datetime | None


def _parse_start(value: Any) -> datetime | None:
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


def _price(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and abs(number) != float("inf") else None


def parse_price_slots(
    attributes: dict[str, Any],
    names: tuple[str, ...] = ("today", "tomorrow"),
    price_key: str = "all_in",
) -> list[PriceSlot]:
    """Parse `{start, all_in}` rows of the BE electricity prices integration.

    Rows have no end time: a slot ends where the next one starts, and the last
    one repeats the previous length. Any malformed row discards everything, so
    a partly broken attribute never produces a plan.
    """
    rows: list[tuple[datetime, float]] = []
    seen: dict[datetime, float] = {}
    found_list = False
    for name in names:
        value = attributes.get(name)
        if not isinstance(value, list):
            continue
        found_list = True
        for row in value:
            if not isinstance(row, dict):
                return []
            start = _parse_start(row.get("start"))
            price = _price(row.get(price_key))
            if start is None or price is None:
                return []
            if start in seen:
                if seen[start] != price:
                    return []
                continue
            seen[start] = price
            rows.append((start, price))
    if not found_list or not rows:
        return []
    rows.sort(key=lambda item: item[0])
    slots: list[PriceSlot] = []
    previous_span: timedelta | None = None
    for index, (start, price) in enumerate(rows):
        gap = rows[index + 1][0] - start if index + 1 < len(rows) else None
        span = gap if gap is not None and timedelta(0) < gap <= MAX_INFERRED_SLOT else previous_span
        if span is None or span <= timedelta(0):
            return []
        slots.append(PriceSlot(start=start, end=start + span, price=price))
        previous_span = span
    return slots


def _hours(delta: timedelta) -> float:
    return delta.total_seconds() / 3600


def plan_ev_charging(
    slots: list[PriceSlot],
    now: datetime,
    settings: PlanSettings,
    context: dict[datetime, SlotContext] | None = None,
) -> EvPlan:
    """Allocate EV energy to the cheapest slots before departure.

    `context` maps a slot start to the planned grid net load of the house and
    battery in that slot. Without it the flat `base_load_kw` setting is used.
    In a slot with PV surplus (negative net load) the first kWh the EV takes
    displaces an export, so such a slot is valued at the export price.
    """
    context = context or {}
    needed = max(0.0, settings.needed_kwh)
    window: list[_WindowSlot] = []
    for slot in slots:
        start = max(slot.start, now)
        end = min(slot.end, settings.departure)
        if end <= start:
            continue
        ctx = context.get(slot.start)
        net = settings.base_load_kw if ctx is None else ctx.base_net_kw
        surplus_kw = max(0.0, -net)
        export_price = slot.price if ctx is None else ctx.export_price
        window.append(
            _WindowSlot(
                start=start,
                end=end,
                import_price=slot.price,
                export_price=export_price,
                base_kw=net,
                surplus_kw=surplus_kw,
            )
        )
    covers_until = max((slot.end for slot in slots), default=None)
    if not window:
        return _empty_plan(STATUS_NO_PRICES, needed, covers_until)
    if needed <= EPSILON:
        return _empty_plan(STATUS_TARGET_REACHED, 0.0, covers_until)

    def power_of(item: _WindowSlot) -> float:
        return min(settings.ev_max_kw, max(0.0, settings.max_grid_import_kw - item.base_kw))

    def marginal(item: _WindowSlot) -> float:
        return min(item.import_price, item.export_price) if item.surplus_kw > EPSILON else item.import_price

    ordered = sorted(window, key=lambda item: (marginal(item), item.start))
    chosen: list[tuple[_WindowSlot, float]] = []
    remaining = needed
    blocked_ceiling = blocked_limit = 0
    for item in ordered:
        if marginal(item) > settings.price_ceiling + EPSILON:
            blocked_ceiling += 1
            continue
        power = power_of(item)
        if power <= EPSILON:
            blocked_limit += 1
            continue
        if remaining <= EPSILON:
            continue
        energy = min(remaining, power * _hours(item.end - item.start))
        chosen.append((item, energy))
        remaining -= energy

    planned = tuple(
        PlannedSlot(
            start=item.start,
            end=item.end,
            kw=energy / _hours(item.end - item.start),
            kwh=energy,
            price=_slot_cost(item, energy) / energy if energy > EPSILON else item.import_price,
            reason=(
                "PV-overschot zelf verbruiken"
                if item.surplus_kw > EPSILON
                else "Goedkoopste beschikbare kwartier onder het prijsplafond"
            ),
        )
        for item, energy in sorted(chosen, key=lambda pair: pair[0].start)
    )
    planned_kwh = sum(slot.kwh for slot in planned)
    unmet = max(0.0, needed - planned_kwh)

    required_ceiling = None
    if unmet > 0.001:
        total = 0.0
        for item in ordered:
            total += power_of(item) * _hours(item.end - item.start)
            if total >= needed - 0.001:
                required_ceiling = marginal(item)
                break

    status = STATUS_PLANNED if unmet <= 0.001 else STATUS_INCOMPLETE
    if status == STATUS_PLANNED and (covers_until is None or covers_until < settings.departure):
        status = STATUS_INCOMPLETE  # prices do not reach departure yet
    return EvPlan(
        status=status,
        slots=planned,
        needed_kwh=needed,
        planned_kwh=planned_kwh,
        unmet_kwh=unmet,
        cost_eur=sum(_slot_cost(item, energy) for item, energy in chosen),
        unmanaged_cost_eur=_unmanaged_cost(window, needed, settings.ev_max_kw),
        required_ceiling=required_ceiling,
        blocked_by_ceiling=blocked_ceiling,
        blocked_by_limit=blocked_limit,
        prices_cover_until=covers_until,
    )


@dataclass(frozen=True)
class _WindowSlot:
    start: datetime
    end: datetime
    import_price: float
    export_price: float
    base_kw: float
    surplus_kw: float


def _slot_cost(item: _WindowSlot, energy_kwh: float) -> float:
    """Energy covered by PV surplus displaces an export; the rest is imported."""
    surplus_kwh = item.surplus_kw * _hours(item.end - item.start)
    from_surplus = min(energy_kwh, surplus_kwh)
    return from_surplus * item.export_price + (energy_kwh - from_surplus) * item.import_price


def _unmanaged_cost(window: list[_WindowSlot], needed: float, ev_max_kw: float) -> float:
    """Cost of charging at full power from now until the target is reached."""
    remaining, cost = needed, 0.0
    for item in sorted(window, key=lambda entry: entry.start):
        if remaining <= EPSILON:
            break
        energy = min(remaining, ev_max_kw * _hours(item.end - item.start))
        cost += _slot_cost(item, energy)
        remaining -= energy
    return cost


def _empty_plan(status: str, needed: float, covers_until: datetime | None) -> EvPlan:
    return EvPlan(
        status=status,
        slots=(),
        needed_kwh=needed,
        planned_kwh=0.0,
        unmet_kwh=needed if status != STATUS_TARGET_REACHED else 0.0,
        cost_eur=0.0,
        unmanaged_cost_eur=0.0,
        required_ceiling=None,
        blocked_by_ceiling=0,
        blocked_by_limit=0,
        prices_cover_until=covers_until,
    )


def disabled_plan() -> EvPlan:
    return _empty_plan(STATUS_DISABLED, 0.0, None)
