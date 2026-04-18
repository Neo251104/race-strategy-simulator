from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


GRAVITY     = 9.8
K_BASE      = 0.0005         
K_DRAG      = 0.0000000015    
K_STRAIGHT  = 0.0000166      
K_BRAKING   = 0.0398          
K_CORNER    = 0.000265        
CRASH_DEG   = 0.1             


DEFAULT_TYRE_BASE_FRICTION = {
    "Soft": 1.8, "Medium": 1.7, "Hard": 1.6,
    "Intermediate": 1.2, "Wet": 1.1,
}


@dataclass
class CarParams:
    max_speed: float
    accel: float
    brake: float
    limp_speed: float
    crawl_speed: float
    tank_capacity: float
    initial_fuel: float


@dataclass
class RaceParams:
    name: str
    laps: int
    base_pit_time: float
    tyre_swap_time: float
    refuel_rate: float          
    crash_penalty: float        
    pit_exit_speed: float
    fuel_soft_cap: float
    starting_weather_id: int
    time_reference: float


@dataclass
class Segment:
    id: int
    type: str                   
    length: float
    radius: Optional[float] = None


@dataclass
class TyreSet:
    id: int                     
    compound: str
    life_span: float
    dry_friction_mult: float
    cold_friction_mult: float
    light_rain_friction_mult: float
    heavy_rain_friction_mult: float
    dry_deg: float
    cold_deg: float
    light_rain_deg: float
    heavy_rain_deg: float

    @property
    def base_friction(self) -> float:
        return DEFAULT_TYRE_BASE_FRICTION.get(self.compound, 1.5)

    def friction_mult(self, weather: str) -> float:
        return {
            "dry":        self.dry_friction_mult,
            "cold":       self.cold_friction_mult,
            "light_rain": self.light_rain_friction_mult,
            "heavy_rain": self.heavy_rain_friction_mult,
        }.get(weather, self.dry_friction_mult)

    def deg_rate(self, weather: str) -> float:
        return {
            "dry":        self.dry_deg,
            "cold":       self.cold_deg,
            "light_rain": self.light_rain_deg,
            "heavy_rain": self.heavy_rain_deg,
        }.get(weather, self.dry_deg)


@dataclass
class WeatherCondition:
    id: int
    condition: str
    duration: float
    accel_mult: float
    decel_mult: float


@dataclass
class SegmentAction:
    id: int
    type: str
    target_speed: Optional[float] = None       
    brake_before: Optional[float] = None       


@dataclass
class PitAction:
    enter: bool
    tyre_id: Optional[int] = None
    refuel: float = 0.0


@dataclass
class LapPlan:
    lap: int
    segments: list[SegmentAction]
    pit: PitAction


@dataclass
class SegmentResult:
    lap: int
    seg_id: int
    seg_type: str
    time: float
    fuel_used: float
    tyre_deg: float
    entry_speed: float
    exit_speed: float
    crashed: bool
    limp: bool
    weather: str


@dataclass
class SimResult:
    total_time: float
    total_fuel_used: float
    total_tyre_deg: float
    blowouts: int
    crashes: int
    lap_times: list[float]
    seg_log: list[SegmentResult]
    tyre_history: list[dict]
    base_score: float
    fuel_bonus: float
    tyre_bonus: float
    final_score: float
    fuel_soft_cap: float


def load_level(path: str) -> tuple[CarParams, RaceParams, list[Segment], dict[int, TyreSet], list[WeatherCondition]]:
    with open(path, "r") as f:
        data = json.load(f)

    c = data["car"]
    car = CarParams(
        max_speed     = c["max_speed_m/s"],
        accel         = c["accel_m/se2"],
        brake         = c["brake_m/se2"],
        limp_speed    = c["limp_constant_m/s"],
        crawl_speed   = c["crawl_constant_m/s"],
        tank_capacity = c.get("fuel_tank_capacity_l", 150.0),
        initial_fuel  = c.get("initial_fuel_l", 150.0),
    )

    r = data["race"]
    race = RaceParams(
        name                = r.get("name", "Race"),
        laps                = r["laps"],
        base_pit_time       = r.get("base_pit_stop_time_s", 20.0),
        tyre_swap_time      = r.get("pit_tyre_swap_time_s", 10.0),
        refuel_rate         = r.get("pit_refuel_rate_l/s", 5.0),
        crash_penalty       = r.get("corner_crash_penalty_s", 10.0),
        pit_exit_speed      = r.get("pit_exit_speed_m/s", 20.0),
        fuel_soft_cap       = r.get("fuel_soft_cap_limit_l", float("inf")),
        starting_weather_id = r.get("starting_weather_condition_id", 1),
        time_reference      = r.get("time_reference", 7300.0),
    )

    segments = [
        Segment(
            id     = s["id"],
            type   = s["type"],
            length = s["length_m"],
            radius = s.get("radius_m"),
        )
        for s in data["track"]["segments"]
    ]

    tyre_props_raw = data["tyres"].get("properties", {})
    available_sets = data["tyres"].get("available_sets", [])
    tyre_map: dict[int, TyreSet] = {}
    for tyre_set in available_sets:
        compound = tyre_set["compound"]
        props = tyre_props_raw.get(compound, {})
        for tid in tyre_set["ids"]:
            tyre_map[tid] = TyreSet(
                id                      = tid,
                compound                = compound,
                life_span               = props.get("life_span", 1.0),
                dry_friction_mult       = props.get("dry_friction_multiplier", 1.0),
                cold_friction_mult      = props.get("cold_friction_multiplier", 1.0),
                light_rain_friction_mult= props.get("light_rain_friction_multiplier", 1.0),
                heavy_rain_friction_mult= props.get("heavy_rain_friction_multiplier", 1.0),
                dry_deg                 = props.get("dry_degradation", 0.10),
                cold_deg                = props.get("cold_degradation", 0.08),
                light_rain_deg          = props.get("light_rain_degradation", 0.09),
                heavy_rain_deg          = props.get("heavy_rain_degradation", 0.10),
            )

    weather_conditions: list[WeatherCondition] = []
    for w in data.get("weather", {}).get("conditions", []):
        weather_conditions.append(WeatherCondition(
            id         = w["id"],
            condition  = w["condition"],
            duration   = w["duration_s"],
            accel_mult = w.get("acceleration_multiplier", 1.0),
            decel_mult = w.get("deceleration_multiplier", 1.0),
        ))

    return car, race, segments, tyre_map, weather_conditions


def load_strategy(path: str) -> list[LapPlan]:
    with open(path, "r") as f:
        data = json.load(f)
    plans = []
    init_tyre = data.get("initial_tyre_id", 1)
    for lap_data in data["laps"]:
        segs = []
        for s in lap_data["segments"]:
            segs.append(SegmentAction(
                id           = s["id"],
                type         = s["type"],
                target_speed = s.get("target_m/s"),
                brake_before = s.get("brake_start_m_before_next"),
            ))
        p = lap_data.get("pit", {})
        pit = PitAction(
            enter   = p.get("enter", False),
            tyre_id = p.get("tyre_change_set_id"),
            refuel  = p.get("fuel_refuel_amount_l", 0.0),
        )
        plans.append(LapPlan(lap=lap_data["lap"], segments=segs, pit=pit))
    return init_tyre, plans


def fuel_used(vi: float, vf: float, dist: float) -> float:
    avg = (vi + vf) / 2.0
    return (K_BASE + K_DRAG * avg * avg) * dist


def get_weather(race_time: float, conditions: list[WeatherCondition]) -> WeatherCondition:
    if not conditions:
        return WeatherCondition(id=0, condition="dry", duration=1e12,
                                accel_mult=1.0, decel_mult=1.0)
    total = sum(w.duration for w in conditions)
    t = race_time % total
    acc = 0.0
    for w in conditions:
        acc += w.duration
        if t < acc:
            return w
    return conditions[-1]


def tyre_friction(tyre: TyreSet, degradation: float, weather: str) -> float:
    return (tyre.base_friction - degradation) * tyre.friction_mult(weather)


def max_corner_speed(tyre: TyreSet, degradation: float, weather: str,
                     radius: float, crawl: float) -> float:
    f = tyre_friction(tyre, degradation, weather)
    return math.sqrt(max(0.0, f * GRAVITY * radius)) + crawl

def simulate(
    car: CarParams,
    race: RaceParams,
    segments: list[Segment],
    tyre_map: dict[int, TyreSet],
    weather_conditions: list[WeatherCondition],
    initial_tyre_id: int,
    lap_plans: list[LapPlan],
) -> SimResult:

    seg_index = {s.id: s for s in segments}

    speed       = 0.0
    fuel        = car.initial_fuel
    race_time   = 0.0
    total_fuel  = 0.0
    crashes     = 0
    blowouts    = 0

    current_tyre_id = initial_tyre_id
    tyre            = tyre_map[current_tyre_id]
    degradation     = 0.0

    limp_mode  = False
    crawl_mode = False

    lap_times: list[float]     = []
    seg_log: list[SegmentResult] = []
    tyre_history: list[dict]   = []

    for lap_plan in lap_plans:
        lap_start = race_time

        for action in lap_plan.segments:
            seg = seg_index[action.id]

            # ── weather at this moment ──
            w        = get_weather(race_time, weather_conditions)
            wcond    = w.condition
            eff_acc  = car.accel * w.accel_mult
            eff_brk  = car.brake * w.decel_mult
            deg_rate = tyre.deg_rate(wcond)

            # ── limp mode check ──
            if fuel <= 0 or degradation >= tyre.life_span:
                if not limp_mode and degradation >= tyre.life_span:
                    blowouts += 1
                limp_mode = True

            seg_time  = 0.0
            seg_fuel  = 0.0
            seg_deg   = 0.0
            crashed   = False

            entry_speed = speed

            if limp_mode:
                v         = car.limp_speed
                seg_time  = seg.length / v
                seg_fuel  = fuel_used(v, v, seg.length)
                seg_deg   = 0.0
                speed     = v

            elif seg.type == "straight":
                crawl_mode = False

                target = min(action.target_speed or car.max_speed, car.max_speed)
                brake_before = action.brake_before or 0.0
                brake_start_m = max(0.0, seg.length - brake_before)
                seg_ids  = [s.id for s in segments]
                si       = seg_ids.index(seg.id)
                next_seg = segments[si + 1] if si + 1 < len(segments) else None
                if next_seg and next_seg.type == "corner":
                    exit_v = max(
                        car.crawl_speed,
                        min(
                            max_corner_speed(tyre, degradation, wcond,
                                             next_seg.radius, car.crawl_speed),
                            target,
                        ),
                    )
                else:
                    exit_v = max(car.crawl_speed, target)

                v   = max(speed, car.crawl_speed)
                pos = 0.0
                t   = 0.0
                f   = 0.0
                d   = 0.0

                if v < target and pos < brake_start_m:
                    room = brake_start_m - pos
                    dist_needed = (target**2 - v**2) / (2 * eff_acc)
                    if dist_needed <= room:
                        dt = (target - v) / eff_acc
                        f += fuel_used(v, target, dist_needed)
                        d += deg_rate * dist_needed * K_STRAIGHT
                        t += dt; pos += dist_needed; v = target
                    else:
                        v2 = math.sqrt(v**2 + 2 * eff_acc * room)
                        v2 = min(v2, car.max_speed)
                        dt = (v2 - v) / eff_acc
                        f += fuel_used(v, v2, room)
                        d += deg_rate * room * K_STRAIGHT
                        t += dt; pos = brake_start_m; v = v2

                if pos < brake_start_m:
                    dist = brake_start_m - pos
                    f   += fuel_used(v, v, dist)
                    d   += deg_rate * dist * K_STRAIGHT
                    t   += dist / max(v, 1e-6)
                    pos  = brake_start_m

                if pos < seg.length:
                    dist   = seg.length - pos
                    vi_brk = v
                    vf_brk = max(exit_v, car.crawl_speed)
                    brk_dist = (vi_brk**2 - vf_brk**2) / (2 * eff_brk) if vi_brk > vf_brk else 0.0

                    if brk_dist <= dist:
                        if brk_dist > 0:
                            dt = (vi_brk - vf_brk) / eff_brk
                            f += fuel_used(vi_brk, vf_brk, brk_dist)
                            d += deg_rate * brk_dist * K_STRAIGHT
                            d += ((vi_brk / 100)**2 - (vf_brk / 100)**2) * K_BRAKING * deg_rate
                            t += dt
                        rem = dist - brk_dist
                        if rem > 0:
                            f += fuel_used(vf_brk, vf_brk, rem)
                            d += deg_rate * rem * K_STRAIGHT
                            t += rem / max(vf_brk, 1e-6)
                        v = vf_brk
                    else:
                        vf2 = math.sqrt(max(0.0, vi_brk**2 - 2 * eff_brk * dist))
                        dt  = (vi_brk - vf2) / eff_brk if vi_brk > vf2 else 0.0
                        f  += fuel_used(vi_brk, vf2, dist)
                        d  += deg_rate * dist * K_STRAIGHT
                        d  += ((vi_brk / 100)**2 - (vf2 / 100)**2) * K_BRAKING * deg_rate
                        t  += dt
                        v   = max(vf2, car.crawl_speed)

                seg_time = t; seg_fuel = f; seg_deg = d; speed = v

            elif seg.type == "corner":
                mc_speed = max_corner_speed(tyre, degradation, wcond,
                                            seg.radius, car.crawl_speed)

                if crawl_mode:
                    corner_v = car.crawl_speed
                elif speed > mc_speed + 1e-6:

                    crashed    = True
                    crashes   += 1
                    race_time += race.crash_penalty
                    degradation += CRASH_DEG
                    corner_v   = car.crawl_speed
                    crawl_mode = True
                else:
                    corner_v = max(speed, car.crawl_speed)

                seg_time = seg.length / max(corner_v, 1e-6)
                seg_fuel = fuel_used(corner_v, corner_v, seg.length)
                seg_deg  = K_CORNER * (corner_v**2 / seg.radius) * deg_rate
                speed    = corner_v

            fuel        -= seg_fuel
            seg_fuel_actual = seg_fuel
            if fuel < 0:
                seg_fuel_actual += fuel  
                fuel = 0.0

            total_fuel  += seg_fuel_actual
            degradation += seg_deg
            race_time   += seg_time

            seg_log.append(SegmentResult(
                lap        = lap_plan.lap,
                seg_id     = seg.id,
                seg_type   = seg.type,
                time       = seg_time,
                fuel_used  = seg_fuel_actual,
                tyre_deg   = seg_deg,
                entry_speed= entry_speed,
                exit_speed = speed,
                crashed    = crashed,
                limp       = limp_mode,
                weather    = wcond,
            ))

        if lap_plan.pit.enter:
            pit_time = race.base_pit_time
            if lap_plan.pit.tyre_id:
                pit_time += race.tyre_swap_time
            if lap_plan.pit.refuel > 0:
                refuel_amt = min(lap_plan.pit.refuel, car.tank_capacity - fuel)
                pit_time  += refuel_amt / race.refuel_rate
                fuel       += refuel_amt
            if lap_plan.pit.tyre_id:
                tyre_history.append({
                    "id": current_tyre_id, "compound": tyre.compound,
                    "degradation": round(degradation, 6), "life_span": tyre.life_span,
                })
                current_tyre_id = lap_plan.pit.tyre_id
                tyre            = tyre_map[current_tyre_id]
                degradation     = 0.0
            limp_mode  = False
            speed      = race.pit_exit_speed
            race_time += pit_time

        lap_times.append(round(race_time - lap_start, 4))

    tyre_history.append({
        "id": current_tyre_id, "compound": tyre.compound,
        "degradation": round(degradation, 6), "life_span": tyre.life_span,
    })

    total_deg  = sum(t["degradation"] for t in tyre_history)
    ref        = race.time_reference
    base_score = 500_000 * (ref / race_time) ** 3

    if race.fuel_soft_cap < float("inf"):
        fuel_bonus = -500_000 * (1 - total_fuel / race.fuel_soft_cap) ** 2 + 500_000
    else:
        fuel_bonus = 0.0

    tyre_bonus = 100_000 * total_deg - 50_000 * blowouts
    final_score = base_score + fuel_bonus + tyre_bonus

    return SimResult(
        total_time     = round(race_time, 4),
        total_fuel_used= round(total_fuel, 4),
        total_tyre_deg = round(total_deg, 6),
        blowouts       = blowouts,
        crashes        = crashes,
        lap_times      = lap_times,
        seg_log        = seg_log,
        tyre_history   = tyre_history,
        base_score     = round(base_score, 2),
        fuel_bonus     = round(fuel_bonus, 2),
        tyre_bonus     = round(tyre_bonus, 2),
        final_score    = round(final_score, 2),
        fuel_soft_cap  = race.fuel_soft_cap,
    )

def prompt(msg: str, default=None, cast=str):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"  {msg}{suffix}: ").strip()
        if raw == "" and default is not None:
            return default
        try:
            return cast(raw)
        except ValueError:
            print(f"  ! Invalid input, expected {cast.__name__}")


def build_strategy_interactive(
    car: CarParams,
    race_params: RaceParams,
    segments: list[Segment],
    tyre_map: dict[int, TyreSet],
    weather_conditions: list[WeatherCondition],
) -> tuple[int, list[LapPlan]]:

    print("\n─── Strategy Builder ───────────────────────────────────")

    # list available tyres
    seen = {}
    for tid, t in sorted(tyre_map.items()):
        seen.setdefault(t.compound, []).append(tid)
    print("  Available tyres:")
    for comp, ids in seen.items():
        print(f"    {comp}: IDs {ids}")

    init_id = prompt("Initial tyre ID", default=list(tyre_map.keys())[0], cast=int)
    while init_id not in tyre_map:
        print("  ! Tyre ID not found.")
        init_id = prompt("Initial tyre ID", default=list(tyre_map.keys())[0], cast=int)

    lap_plans: list[LapPlan] = []
    for lap in range(1, race_params.laps + 1):
        print(f"\n  ── Lap {lap} ──────────────────")
        actions: list[SegmentAction] = []
        for seg in segments:
            if seg.type == "straight":
                print(f"    Segment {seg.id} — straight ({seg.length} m)")
                tgt = prompt("    Target speed (m/s)", default=round(car.max_speed * 0.8), cast=float)
                brk = prompt("    Brake point (m before next seg)", default=200, cast=float)
                actions.append(SegmentAction(id=seg.id, type="straight",
                                             target_speed=tgt, brake_before=brk))
            else:
                actions.append(SegmentAction(id=seg.id, type="corner"))

        enter_pit = prompt("  Pit stop this lap? (y/n)", default="n").lower().startswith("y")
        pit = PitAction(enter=enter_pit)
        if enter_pit:
            print("  Available tyre IDs:", list(tyre_map.keys()))
            raw_tid = prompt("  New tyre ID (Enter to skip)", default="")
            pit.tyre_id = int(raw_tid) if raw_tid else None
            pit.refuel  = prompt("  Fuel to add (L)", default=0.0, cast=float)

        lap_plans.append(LapPlan(lap=lap, segments=actions, pit=pit))

    return init_id, lap_plans


def build_strategy_auto(
    car: CarParams,
    race_params: RaceParams,
    segments: list[Segment],
    tyre_map: dict[int, TyreSet],
    weather_conditions: list[WeatherCondition],
    init_tyre_id: int,
) -> tuple[int, list[LapPlan]]:
    """Conservative auto-strategy: safe speeds, correct braking distances."""

    tyre    = tyre_map[init_tyre_id]
    w_start = get_weather(0, weather_conditions)
    wcond   = w_start.condition

    lap_plans: list[LapPlan] = []
    for lap in range(1, race_params.laps + 1):
        actions: list[SegmentAction] = []
        for i, seg in enumerate(segments):
            if seg.type == "straight":
                # find next corner
                next_corner = next(
                    (segments[j] for j in range(i + 1, len(segments))
                     if segments[j].type == "corner"), None)
                if next_corner:
                    mc = max_corner_speed(tyre, 0.0, wcond,
                                         next_corner.radius, car.crawl_speed)
                    target = min(car.max_speed * 0.85, mc * 1.1)
                else:
                    target = car.max_speed * 0.85
                target   = max(car.crawl_speed, target)
                brk_dist = (target**2) / (2 * car.brake)
                brk_dist = min(brk_dist + 30, seg.length - 50)
                actions.append(SegmentAction(
                    id=seg.id, type="straight",
                    target_speed=round(target, 2),
                    brake_before=max(0.0, round(brk_dist, 2)),
                ))
            else:
                actions.append(SegmentAction(id=seg.id, type="corner"))

        lap_plans.append(LapPlan(
            lap      = lap,
            segments = actions,
            pit      = PitAction(enter=False),
        ))

    return init_tyre_id, lap_plans

def print_results(result: SimResult, race_name: str = "") -> None:
    W = 58
    bar = "─" * W

    print(f"\n{'═' * W}")
    print(f"  RACE RESULTS  {race_name}")
    print(f"{'═' * W}")

    print(f"  Total race time   : {result.total_time:>10.3f} s")
    print(f"  Total fuel used   : {result.total_fuel_used:>10.4f} L", end="")
    if result.fuel_soft_cap < float("inf"):
        pct = result.total_fuel_used / result.fuel_soft_cap * 100
        print(f"  ({pct:.1f}% of soft cap {result.fuel_soft_cap} L)", end="")
    print()
    print(f"  Total tyre deg    : {result.total_tyre_deg:>10.5f}")
    print(f"  Blowouts          : {result.blowouts:>10}")
    print(f"  Crashes           : {result.crashes:>10}")
    print(bar)

    print("  Lap times:")
    for i, t in enumerate(result.lap_times, 1):
        print(f"    Lap {i:>2}: {t:.3f} s")

    print(bar)
    print("  Tyre sets used:")
    for i, t in enumerate(result.tyre_history, 1):
        pct = t["degradation"] / t["life_span"] * 100 if t["life_span"] else 0
        bar_fill = int(pct / 5)
        bar_vis  = "█" * bar_fill + "░" * (20 - bar_fill)
        print(f"    {i}. {t['compound']:12} ID {t['id']:>3}  "
              f"deg {t['degradation']:.5f}/{t['life_span']:.1f}  "
              f"[{bar_vis}] {pct:.1f}%")

    print(bar)
    print(f"  Score breakdown:")
    print(f"    Base score   : {result.base_score:>12,.2f}")
    print(f"    Fuel bonus   : {result.fuel_bonus:>12,.2f}")
    print(f"    Tyre bonus   : {result.tyre_bonus:>12,.2f}")
    print(f"    ─────────────────────────")
    print(f"    FINAL SCORE  : {result.final_score:>12,.2f}")
    print(f"{'═' * W}")

    print("\n  Segment log:")
    hdr = f"  {'Lap':>3} {'Seg':>4} {'Type':>8} {'Time(s)':>9} {'Fuel(L)':>9} {'TyreDeg':>9} {'ExitV':>7} {'Status'}"
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    for s in result.seg_log:
        status = "CRASH" if s.crashed else ("LIMP" if s.limp else "ok")
        print(f"  {s.lap:>3} {s.seg_id:>4} {s.seg_type:>8} "
              f"{s.time:>9.3f} {s.fuel_used:>9.5f} {s.tyre_deg:>9.6f} "
              f"{s.exit_speed:>7.2f} {status}")
    print()


def export_submission(
    initial_tyre_id: int,
    lap_plans: list[LapPlan],
    out_path: str,
) -> None:
    laps_out = []
    for lp in lap_plans:
        segs_out = []
        for a in lp.segments:
            seg_dict: dict = {"id": a.id, "type": a.type}
            if a.type == "straight":
                seg_dict["target_m/s"] = a.target_speed
                seg_dict["brake_start_m_before_next"] = a.brake_before
            segs_out.append(seg_dict)
        pit_dict: dict = {"enter": lp.pit.enter}
        if lp.pit.enter:
            if lp.pit.tyre_id:
                pit_dict["tyre_change_set_id"] = lp.pit.tyre_id
            if lp.pit.refuel:
                pit_dict["fuel_refuel_amount_l"] = lp.pit.refuel
        laps_out.append({"lap": lp.lap, "segments": segs_out, "pit": pit_dict})

    submission = {"initial_tyre_id": initial_tyre_id, "laps": laps_out}
    with open(out_path, "w") as f:
        json.dump(submission, f, indent=2)
    print(f"  ✓ Submission JSON written to: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Entelect Grand Prix — Race Simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--level",    "-l", help="Path to level JSON file")
    parser.add_argument("--strategy", "-s", help="Path to strategy JSON file (optional)")
    parser.add_argument("--output",   "-o", default="submission.txt",
                        help="Output submission file (default: submission.txt)")
    parser.add_argument("--auto",     "-a", action="store_true",
                        help="Use auto safe-strategy without interactive input")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════════╗")
    print("║       ENTELECT GRAND PRIX  SIMULATOR         ║")
    print("╚══════════════════════════════════════════════╝\n")

    # ── load level ──
    level_path = args.level
    if not level_path:
        level_path = input("  Enter path to level JSON file: ").strip()
    level_path = level_path.strip('"').strip("'")
    if not os.path.isfile(level_path):
        print(f"  ✗ File not found: {level_path}")
        sys.exit(1)

    print(f"  Loading level: {level_path}")
    car, race_params, segments, tyre_map, weather_conditions = load_level(level_path)
    print(f"  ✓ {race_params.name}  |  {race_params.laps} laps  |  {len(segments)} segments")

    # ── build or load strategy ──
    if args.strategy:
        print(f"  Loading strategy: {args.strategy}")
        init_tyre_id, lap_plans = load_strategy(args.strategy)
        print(f"  ✓ Strategy loaded (initial tyre ID {init_tyre_id})")

    elif args.auto:
        default_tyre = list(tyre_map.keys())[0]
        print(f"  Auto-strategy mode (tyre ID {default_tyre})")
        init_tyre_id, lap_plans = build_strategy_auto(
            car, race_params, segments, tyre_map, weather_conditions, default_tyre)

    else:
        print("\n  How would you like to set the strategy?")
        print("    1. Interactive  — enter values lap by lap")
        print("    2. Auto-safe    — conservative auto-filled strategy")
        print("    3. Load file    — load a strategy JSON")
        choice = input("  Choice [1/2/3]: ").strip()

        if choice == "3":
            spath = input("  Strategy JSON path: ").strip().strip('"').strip("'")
            if not os.path.isfile(spath):
                print(f"  ✗ File not found: {spath}")
                sys.exit(1)
            init_tyre_id, lap_plans = load_strategy(spath)
        elif choice == "2":
            default_tyre = list(tyre_map.keys())[0]
            print(f"\n  Available tyre IDs: {list(tyre_map.keys())}")
            default_tyre = prompt("  Starting tyre ID",
                                  default=default_tyre, cast=int)
            init_tyre_id, lap_plans = build_strategy_auto(
                car, race_params, segments, tyre_map,
                weather_conditions, default_tyre)
        else:
            init_tyre_id, lap_plans = build_strategy_interactive(
                car, race_params, segments, tyre_map, weather_conditions)

    print("\n  Running simulation…")
    result = simulate(
        car, race_params, segments, tyre_map,
        weather_conditions, init_tyre_id, lap_plans,
    )

    print_results(result, race_params.name)

    out_path = args.output
    if not args.level:
        out_path = input(f"  Save submission JSON to [{out_path}]: ").strip() or out_path
    export_submission(init_tyre_id, lap_plans, out_path)


if __name__ == "__main__":
    main()
