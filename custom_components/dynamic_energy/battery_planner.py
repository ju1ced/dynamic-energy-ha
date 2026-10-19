"""Pure battery planner (dynamic programming over a SOC grid).

Advisory only. It decides, per quarter, whether a home battery should charge
from the grid, store PV surplus, discharge to the house load or hold, given
import and export prices and a forecast of the house net load (load - PV).
Ported from the validated shadow-controller planner; no Home Assistant
imports and no side effects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

ACTION_CHARGE_FROM_GRID = "CHARGE_FROM_GRID"
ACTION_STORE_PV_SURPLUS = "STORE_PV_SURPLUS"
ACTION_DISCHARGE_TO_LOAD = "DISCHARGE_TO_LOAD"
ACTION_HOLD = "HOLD"

STATUS_PLANNED = "PLANNED"
STATUS_NO_ACTION = "NO_ACTION"
STATUS_DISABLED = "DISABLED"

EPSILON = 1e-9


@dataclass(frozen=True)
class QuarterInput:
    start: datetime
    end: datetime
    base_net_kwh: float  # house load - PV in this slot; negative = surplus
    import_rate: float  # EUR/kWh
    export_rate: float  # EUR/kWh
    slot_start: datetime | None = None  # aligned start when `start` was clipped to now

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


@dataclass(frozen=True)
class BatteryParams:
    capacity_kwh: float  # usable window between the minimum and maximum SOC
    max_charge_kw: float
    max_discharge_kw: float
    round_trip_efficiency: float
    degradation_eur_per_kwh: float = 0.0
    state_step_kwh: float = 0.05


@dataclass(frozen=True)
class BatterySlot:
    start: datetime
    end: datetime
    action: str
    reason: str
    charge_kwh: float
    discharge_kwh: float
    soc_start_kwh: float
    soc_end_kwh: float
    grid_net_kw: float  # after the battery; positive = import
    import_rate: float
    export_rate: float
    base_net_kwh: float
    slot_start: datetime

    @property
    def battery_kw(self) -> float:
        """Positive = charging, negative = discharging."""
        hours = (self.end - self.start).total_seconds() / 3600
        return (self.charge_kwh - self.discharge_kwh) / hours


@dataclass(frozen=True)
class BatteryPlan:
    status: str
    reason: str
    slots: tuple[BatterySlot, ...]
    baseline_cost_eur: float
    planned_cost_eur: float
    charge_kwh: float
    discharge_kwh: float
    grid_charge_kwh: float
    surplus_charge_kwh: float
    cap_kw: float

    @property
    def saving_eur(self) -> float:
        return self.baseline_cost_eur - self.planned_cost_eur


def grid_cost(net_kwh: float, import_rate: float, export_rate: float) -> float:
    return net_kwh * (import_rate if net_kwh >= 0 else export_rate)


def _empty(status: str, reason: str, cap_kw: float = 0.0) -> BatteryPlan:
    return BatteryPlan(status, reason, (), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, cap_kw)


def disabled_plan() -> BatteryPlan:
    return _empty(STATUS_DISABLED, "DISABLED")


def _price_band(rate: float, ordered: list[float]) -> str:
    low = ordered[round((len(ordered) - 1) * 0.25)]
    high = ordered[round((len(ordered) - 1) * 0.75)]
    if rate <= low:
        return "LOW_PRICE"
    if rate >= high:
        return "HIGH_PRICE"
    return "MID_PRICE"


def plan_battery(
    quarters: list[QuarterInput],
    params: BatteryParams,
    initial_soc_kwh: float,
    cap_kw: float,
) -> BatteryPlan:
    """Minimise grid cost plus degradation; end the horizon at the initial SOC.

    `cap_kw` limits the grid import the battery may add. A slot whose house
    load already exceeds the cap is never made worse than its base net load.
    """
    capacity = params.capacity_kwh
    if not quarters:
        return _empty(STATUS_NO_ACTION, "NO_INTERVALS", cap_kw)
    if (
        capacity <= 0
        or params.state_step_kwh <= 0
        or not 0 < params.round_trip_efficiency <= 1
        or any(q.hours <= 0 for q in quarters)
        or not 0 <= initial_soc_kwh <= capacity + EPSILON
    ):
        return _empty(STATUS_NO_ACTION, "INVALID_INPUT", cap_kw)
    for previous, current in zip(quarters, quarters[1:]):
        if current.start != previous.end:
            return _empty(STATUS_NO_ACTION, "INTERVALS_NOT_CONTIGUOUS", cap_kw)

    one_way = math.sqrt(params.round_trip_efficiency)
    levels = max(2, round(capacity / params.state_step_kwh) + 1)
    step = capacity / (levels - 1)
    initial_index = min(levels - 1, max(0, round(min(initial_soc_kwh, capacity) / step)))
    infinite = float("inf")
    previous_cost = [infinite] * levels
    previous_cost[initial_index] = 0.0
    backpointers: list[list[int]] = []

    for quarter in quarters:
        hours = quarter.hours
        base = quarter.base_net_kwh
        row_cap = max(cap_kw * hours, max(0.0, base))
        current_cost = [infinite] * levels
        current_previous = [-1] * levels
        for before, accumulated in enumerate(previous_cost):
            if not math.isfinite(accumulated):
                continue
            energy = before * step
            minimum_after = max(0.0, energy - params.max_discharge_kw * hours / one_way)
            maximum_after = min(capacity, energy + params.max_charge_kw * hours * one_way)
            for after in range(
                math.ceil(minimum_after / step - 1e-12),
                math.floor(maximum_after / step + 1e-12) + 1,
            ):
                delta = after * step - energy
                charge = delta / one_way if delta > 0 else 0.0
                discharge = -delta * one_way if delta < 0 else 0.0
                if discharge > max(0.0, base) + 1e-9:
                    continue  # no battery export
                net = base + charge - discharge
                if net > row_cap + 1e-9:
                    continue
                candidate = (
                    accumulated
                    + grid_cost(net, quarter.import_rate, quarter.export_rate)
                    + discharge * params.degradation_eur_per_kwh
                )
                if candidate < current_cost[after] - 1e-12:
                    current_cost[after] = candidate
                    current_previous[after] = before
                elif abs(candidate - current_cost[after]) <= 1e-12:
                    old = current_previous[after]
                    if old < 0 or abs(after - before) < abs(after - old):
                        current_previous[after] = before
        if all(index < 0 for index in current_previous):
            return _empty(STATUS_NO_ACTION, "NO_FEASIBLE_STATE_TRANSITION", cap_kw)
        previous_cost = current_cost
        backpointers.append(current_previous)

    if not math.isfinite(previous_cost[initial_index]):
        return _empty(STATUS_NO_ACTION, "TERMINAL_SOC_UNREACHABLE", cap_kw)

    path = [initial_index]
    cursor = initial_index
    for pointers in reversed(backpointers):
        cursor = pointers[cursor]
        path.append(cursor)
    path.reverse()

    ordered = sorted(q.import_rate for q in quarters)
    slots: list[BatterySlot] = []
    baseline = planned = charge_total = discharge_total = 0.0
    grid_charge = surplus_charge = 0.0
    for index, quarter in enumerate(quarters):
        before, after = path[index] * step, path[index + 1] * step
        delta = after - before
        charge = delta / one_way if delta > 0 else 0.0
        discharge = -delta * one_way if delta < 0 else 0.0
        base = quarter.base_net_kwh
        net = base + charge - discharge
        band = _price_band(quarter.import_rate, ordered)
        if charge > EPSILON:
            action = ACTION_STORE_PV_SURPLUS if base < 0 else ACTION_CHARGE_FROM_GRID
            reason = "PV_SURPLUS_STORED_BEFORE_EXPORT" if base < 0 else f"{band}_CHARGE_WITHIN_CAP"
        elif discharge > EPSILON:
            action = ACTION_DISCHARGE_TO_LOAD
            reason = f"{band}_AVOID_IMPORT"
        else:
            action = ACTION_HOLD
            reason = f"{band}_NO_PROFITABLE_MOVE"
        added = max(0.0, max(0.0, net) - max(0.0, base)) if charge > EPSILON else 0.0
        baseline += grid_cost(base, quarter.import_rate, quarter.export_rate)
        planned += (
            grid_cost(net, quarter.import_rate, quarter.export_rate)
            + discharge * params.degradation_eur_per_kwh
        )
        charge_total += charge
        discharge_total += discharge
        grid_charge += added
        surplus_charge += max(0.0, charge - added)
        slots.append(
            BatterySlot(
                start=quarter.start,
                end=quarter.end,
                action=action,
                reason=reason,
                charge_kwh=charge,
                discharge_kwh=discharge,
                soc_start_kwh=before,
                soc_end_kwh=after,
                grid_net_kw=net / quarter.hours,
                import_rate=quarter.import_rate,
                export_rate=quarter.export_rate,
                base_net_kwh=base,
                slot_start=quarter.slot_start or quarter.start,
            )
        )
    return BatteryPlan(
        status=STATUS_PLANNED,
        reason="OPTIMISED_WITH_GRID_CAP",
        slots=tuple(slots),
        baseline_cost_eur=baseline,
        planned_cost_eur=planned,
        charge_kwh=charge_total,
        discharge_kwh=discharge_total,
        grid_charge_kwh=grid_charge,
        surplus_charge_kwh=surplus_charge,
        cap_kw=cap_kw,
    )
