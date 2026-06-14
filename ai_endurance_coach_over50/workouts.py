"""Build and schedule Garmin structured workouts (cycling, strength, ruck) from the training plan."""
from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

from garminconnect.workout import (
    BaseWorkout,
    CyclingWorkout,
    FitnessEquipmentWorkout,
    HikingWorkout,
    ExecutableStep,
    WorkoutSegment,
    create_cooldown_step,
    create_repeat_group,
    create_warmup_step,
    StepType,
    TargetType,
    ConditionType,
    SportType,
)

from .plan import (
    TRAINING_WEEKS, PLAN_START,
    MAXI_INTERVALS, KB_FULL_SPECS, KB_SPECS, COMPOUND_SESSIONS, RUCK_SPECS,
)

_SPORT        = {"sportTypeId": SportType.CYCLING,           "sportTypeKey": "cycling",           "displayOrder": 1}
# garminconnect 0.3.6 (needed for the per-date push API) renumbered SportType and
# dropped the FITNESS_EQUIPMENT / HIKING members. Pin the sport-type IDs to the
# values the plan's workouts were built and validated against (FITNESS_EQUIPMENT=6,
# HIKING=7); getattr still picks the named members on the older lib.
_FE_SPORT     = {"sportTypeId": getattr(SportType, "FITNESS_EQUIPMENT", 6), "sportTypeKey": "fitness_equipment", "displayOrder": 6}
_HIKE_SPORT   = {"sportTypeId": getattr(SportType, "HIKING", 7),           "sportTypeKey": "hiking",             "displayOrder": 7}
_CARDIO_SPORT = {"sportTypeId": 11,                          "sportTypeKey": "cardio",             "displayOrder": 11}

_BIKE_TYPES         = {"bike", "tempo", "ftp", "long"}
_STRENGTH_RUCK_TYPES = {"strength", "ruck"}


# ── Target type dicts ────────────────────────────────────────────────────────

def _hr_zone_target() -> dict[str, Any]:
    # 0.3.6 renamed HEART_RATE → HEART_RATE_ZONE (both id 4); OPEN (id 6) was dropped.
    tid = getattr(TargetType, "HEART_RATE", getattr(TargetType, "HEART_RATE_ZONE", 4))
    return {"workoutTargetTypeId": tid, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 4}

def _cadence_target() -> dict[str, Any]:
    return {"workoutTargetTypeId": TargetType.CADENCE, "workoutTargetTypeKey": "cadence", "displayOrder": 3}

def _no_target() -> dict[str, Any]:
    return {"workoutTargetTypeId": TargetType.NO_TARGET, "workoutTargetTypeKey": "no.target", "displayOrder": 1}

def _open_target() -> dict[str, Any]:
    return {"workoutTargetTypeId": getattr(TargetType, "OPEN", 6), "workoutTargetTypeKey": "open", "displayOrder": 6}


# ── Step builders ────────────────────────────────────────────────────────────

def _step(
    step_order: int,
    stype_id: int,
    stype_key: str,
    stype_display: int,
    secs: float,
    target: dict[str, Any],
    lo: int | None = None,
    hi: int | None = None,
) -> ExecutableStep:
    extra: dict[str, Any] = {}
    target_key = target.get("workoutTargetTypeKey", "")
    if target_key == "heart.rate.zone":
        # Garmin Connect expects zoneNumber (1–5), not targetValueOne/Two (those are
        # for speed/cadence absolute ranges and leave zoneNumber null in Connect).
        if lo is not None:
            extra["zoneNumber"] = lo if hi is None or lo == hi else hi
    elif lo is not None or hi is not None:
        if lo is not None:
            extra["targetValueOne"] = lo
        if hi is not None:
            extra["targetValueTwo"] = hi
    return ExecutableStep(
        stepOrder=step_order,
        stepType={"stepTypeId": stype_id, "stepTypeKey": stype_key, "displayOrder": stype_display},
        endCondition={"conditionTypeId": ConditionType.TIME, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True},
        endConditionValue=float(secs),
        targetType=target,
        **extra,
    )


def _interval(order: int, secs: float, target: dict, lo: int | None = None, hi: int | None = None) -> ExecutableStep:
    return _step(order, StepType.INTERVAL, "interval", 3, secs, target, lo, hi)

def _recovery(order: int, secs: float, target: dict | None = None, lo: int | None = None, hi: int | None = None) -> ExecutableStep:
    return _step(order, StepType.RECOVERY, "recovery", 4, secs, target or _no_target(), lo, hi)


# ── Workout factories ────────────────────────────────────────────────────────

def _make(name: str, steps: list, dur_min: int) -> CyclingWorkout:
    return CyclingWorkout(
        workoutName=name,
        estimatedDurationInSecs=dur_min * 60,
        workoutSegments=[WorkoutSegment(segmentOrder=1, sportType=_SPORT, workoutSteps=steps)],
    )


def _make_fe(name: str, steps: list, dur_min: int) -> FitnessEquipmentWorkout:
    return FitnessEquipmentWorkout(
        workoutName=name,
        estimatedDurationInSecs=dur_min * 60,
        workoutSegments=[WorkoutSegment(segmentOrder=1, sportType=_FE_SPORT, workoutSteps=steps)],
    )


def _make_cardio(name: str, steps: list, dur_min: int) -> BaseWorkout:
    """Cardio sport type for MaxiClimber — matches stair stepper activity profile on watch."""
    return BaseWorkout(
        workoutName=name,
        estimatedDurationInSecs=dur_min * 60,
        sportType=_CARDIO_SPORT,
        workoutSegments=[WorkoutSegment(segmentOrder=1, sportType=_CARDIO_SPORT, workoutSteps=steps)],
    )


def _make_hike(name: str, steps: list, dur_min: int) -> HikingWorkout:
    return HikingWorkout(
        workoutName=name,
        estimatedDurationInSecs=dur_min * 60,
        workoutSegments=[WorkoutSegment(segmentOrder=1, sportType=_HIKE_SPORT, workoutSteps=steps)],
    )


# ── Individual cycling workout builders ──────────────────────────────────────

def _easy_spin(dur_min: int) -> CyclingWorkout:
    return _make(f"Easy Spin {dur_min}m", [
        create_warmup_step(600.0, step_order=1),
        _interval(2, (dur_min - 20) * 60, _hr_zone_target(), 1, 2),
        create_cooldown_step(600.0, step_order=3),
    ], dur_min)


def _zone2_steady(dur_min: int) -> CyclingWorkout:
    return _make(f"Zone 2 Steady {dur_min}m", [
        create_warmup_step(600.0, step_order=1),
        _interval(2, (dur_min - 20) * 60, _hr_zone_target(), 2, 2),
        create_cooldown_step(600.0, step_order=3),
    ], dur_min)


def _recovery_spin(dur_min: int) -> CyclingWorkout:
    return _make(f"Recovery Spin {dur_min}m", [
        create_warmup_step(600.0, step_order=1),
        _interval(2, (dur_min - 20) * 60, _hr_zone_target(), 1, 1),
        create_cooldown_step(600.0, step_order=3),
    ], dur_min)


def _structured_z2(dur_min: int) -> CyclingWorkout:
    # 10m warmup + 3×(12m Z2 + 2m easy) + 10m cooldown = 60m
    return _make(f"Structured Z2 {dur_min}m", [
        create_warmup_step(600.0, step_order=1),
        _interval(2, 720, _hr_zone_target(), 2, 2),
        _recovery(3, 120),
        _interval(4, 720, _hr_zone_target(), 2, 2),
        _recovery(5, 120),
        _interval(6, 720, _hr_zone_target(), 2, 2),
        create_cooldown_step(600.0, step_order=7),
    ], dur_min)


def _z2_hills(dur_min: int) -> CyclingWorkout:
    # 10m warmup + 20m Z2 + 4×(3m Z3-4 hill + 3m Z1) + 6m cooldown = 60m
    return _make(f"Z2 + Hills {dur_min}m", [
        create_warmup_step(600.0, step_order=1),
        _interval(2, 1200, _hr_zone_target(), 2, 2),
        _interval(3, 180, _hr_zone_target(), 3, 4),
        _recovery(4, 180, _hr_zone_target(), 1, 1),
        _interval(5, 180, _hr_zone_target(), 3, 4),
        _recovery(6, 180, _hr_zone_target(), 1, 1),
        _interval(7, 180, _hr_zone_target(), 3, 4),
        _recovery(8, 180, _hr_zone_target(), 1, 1),
        _interval(9, 180, _hr_zone_target(), 3, 4),
        create_cooldown_step(360.0, step_order=10),
    ], dur_min)


def _cadence_drills(dur_min: int) -> CyclingWorkout:
    # 10m warmup + 5×(3m 90-110rpm + 2m Z2) + 15m Z2 + 10m cooldown = 60m
    steps: list = [create_warmup_step(600.0, step_order=1)]
    o = 2
    for _ in range(5):
        steps.append(_interval(o, 180, _cadence_target(), 90, 110))
        o += 1
        steps.append(_recovery(o, 120, _hr_zone_target(), 2, 2))
        o += 1
    steps.append(_interval(o, 900, _hr_zone_target(), 2, 2))
    o += 1
    steps.append(create_cooldown_step(600.0, step_order=o))
    return _make(f"Cadence Drills {dur_min}m", steps, dur_min)


def _hilly_z2(dur_min: int) -> CyclingWorkout:
    # Z2 target; Z3 accepted on climbs
    return _make(f"Hilly Z2 {dur_min}m", [
        create_warmup_step(600.0, step_order=1),
        _interval(2, (dur_min - 20) * 60, _hr_zone_target(), 2, 3),
        create_cooldown_step(600.0, step_order=3),
    ], dur_min)


def _z2_endurance(dur_min: int) -> CyclingWorkout:
    return _make(f"Z2 Endurance {dur_min}m", [
        create_warmup_step(600.0, step_order=1),
        _interval(2, (dur_min - 20) * 60, _hr_zone_target(), 2, 2),
        create_cooldown_step(600.0, step_order=3),
    ], dur_min)


def _low_cadence(dur_min: int) -> CyclingWorkout:
    # 10m warmup + 5×(4m low cadence Z3 + 2m Z1 recovery) + 10m Z2 + 10m cooldown = 60m
    steps: list = [create_warmup_step(600.0, step_order=1)]
    o = 2
    for _ in range(5):
        steps.append(_interval(o, 240, _cadence_target(), 60, 70))
        o += 1
        steps.append(_recovery(o, 120, _hr_zone_target(), 1, 1))
        o += 1
    steps.append(_interval(o, 600, _hr_zone_target(), 2, 2))
    o += 1
    steps.append(create_cooldown_step(600.0, step_order=o))
    return _make(f"Low Cadence {dur_min}m", steps, dur_min)


def _easy_prep_ride(dur_min: int) -> CyclingWorkout:
    return _make(f"Easy Prep Ride {dur_min}m", [
        create_warmup_step(600.0, step_order=1),
        _interval(2, (dur_min - 20) * 60, _hr_zone_target(), 1, 2),
        create_cooldown_step(600.0, step_order=3),
    ], dur_min)


def _ftp_test(name: str, dur_min: int) -> CyclingWorkout:
    # 15m warmup + 3m priming + 5m easy + 20m all-out + 17m cooldown = 60m
    return _make(name, [
        create_warmup_step(900.0, step_order=1),
        _interval(2, 180, _open_target()),
        _recovery(3, 300, _hr_zone_target(), 1, 1),
        _interval(4, 1200, _open_target()),
        create_cooldown_step(1020.0, step_order=5),
    ], dur_min)


def _tempo_intervals(dur_min: int) -> CyclingWorkout:
    # 15m warmup + 3×(10m Z4 + 5m Z1) + 5m cooldown = 60m
    return _make(f"Tempo Intervals {dur_min}m", [
        create_warmup_step(900.0, step_order=1),
        _interval(2, 600, _hr_zone_target(), 4, 4),
        _recovery(3, 300, _hr_zone_target(), 1, 1),
        _interval(4, 600, _hr_zone_target(), 4, 4),
        _recovery(5, 300, _hr_zone_target(), 1, 1),
        _interval(6, 600, _hr_zone_target(), 4, 4),
        create_cooldown_step(300.0, step_order=7),
    ], dur_min)


def _long_ride(name: str, dur_min: int) -> CyclingWorkout:
    # 15m warmup + main Z2 + 15m cooldown
    return _make(f"{name} {dur_min}m", [
        create_warmup_step(900.0, step_order=1),
        _interval(2, (dur_min - 30) * 60, _hr_zone_target(), 2, 2),
        create_cooldown_step(900.0, step_order=3),
    ], dur_min)


def _easy_ride(dur_min: int) -> CyclingWorkout:
    # 10m warmup + easy Z1-2 + 10m cooldown
    return _make(f"Easy Ride {dur_min}m", [
        create_warmup_step(600.0, step_order=1),
        _interval(2, (dur_min - 20) * 60, _hr_zone_target(), 1, 2),
        create_cooldown_step(600.0, step_order=3),
    ], dur_min)


def _z2_ride(dur_min: int) -> CyclingWorkout:
    # 10m warmup + Z2 block + 10m cooldown
    return _make(f"Z2 Ride {dur_min}m", [
        create_warmup_step(600.0, step_order=1),
        _interval(2, (dur_min - 20) * 60, _hr_zone_target(), 2, 2),
        create_cooldown_step(600.0, step_order=3),
    ], dur_min)


def _low_cadence_ride(dur_min: int) -> CyclingWorkout:
    # 10m warmup + 5×(6m 60-70rpm Z3 + 3m Z1) + Z2 filler + 10m cooldown.
    # The Z2 block absorbs the remaining time so total matches dur_min
    # (25m at the standard 90m).
    steps: list = [create_warmup_step(600.0, step_order=1)]
    o = 2
    for _ in range(5):
        steps.append(_interval(o, 360, _cadence_target(), 60, 70))
        o += 1
        steps.append(_recovery(o, 180, _hr_zone_target(), 1, 1))
        o += 1
    z2_secs = max(0, (dur_min - 65) * 60)
    if z2_secs:
        steps.append(_interval(o, z2_secs, _hr_zone_target(), 2, 2))
        o += 1
    steps.append(create_cooldown_step(600.0, step_order=o))
    return _make(f"Low Cadence Ride {dur_min}m", steps, dur_min)


def _sweetspot_ride(dur_min: int) -> CyclingWorkout:
    # 15m warmup + 3×(15m Z3-4 sweetspot + 5m Z1) + 15m cooldown = 90m
    return _make(f"Sweetspot Ride {dur_min}m", [
        create_warmup_step(900.0, step_order=1),
        _interval(2, 900, _hr_zone_target(), 3, 4),
        _recovery(3, 300, _hr_zone_target(), 1, 1),
        _interval(4, 900, _hr_zone_target(), 3, 4),
        _recovery(5, 300, _hr_zone_target(), 1, 1),
        _interval(6, 900, _hr_zone_target(), 3, 4),
        create_cooldown_step(900.0, step_order=7),
    ], dur_min)


def _over_unders(dur_min: int) -> CyclingWorkout:
    # 15m warmup + 3×(8m Z4 under + 2m Z5 over + 5m Z1 recovery) + 15m cooldown = 75m
    return _make(f"Over-Unders {dur_min}m", [
        create_warmup_step(900.0, step_order=1),
        _interval(2, 480, _hr_zone_target(), 4, 4),
        _interval(3, 120, _hr_zone_target(), 5, 5),
        _recovery(4, 300, _hr_zone_target(), 1, 1),
        _interval(5, 480, _hr_zone_target(), 4, 4),
        _interval(6, 120, _hr_zone_target(), 5, 5),
        _recovery(7, 300, _hr_zone_target(), 1, 1),
        _interval(8, 480, _hr_zone_target(), 4, 4),
        _interval(9, 120, _hr_zone_target(), 5, 5),
        create_cooldown_step(900.0, step_order=10),
    ], dur_min)


def _threshold_ride(dur_min: int) -> CyclingWorkout:
    # 15m warmup + 3×(15m Z4 + 5m Z1) + 15m cooldown = 90m
    return _make(f"Threshold Ride {dur_min}m", [
        create_warmup_step(900.0, step_order=1),
        _interval(2, 900, _hr_zone_target(), 4, 4),
        _recovery(3, 300, _hr_zone_target(), 1, 1),
        _interval(4, 900, _hr_zone_target(), 4, 4),
        _recovery(5, 300, _hr_zone_target(), 1, 1),
        _interval(6, 900, _hr_zone_target(), 4, 4),
        create_cooldown_step(900.0, step_order=7),
    ], dur_min)


def _hill_repeats(dur_min: int) -> CyclingWorkout:
    # 15m warmup + 5×(3m Z4-5 effort + 3m Z1 recovery) + 15m cooldown = 60m
    steps: list = [create_warmup_step(900.0, step_order=1)]
    o = 2
    for _ in range(5):
        steps.append(_interval(o, 180, _hr_zone_target(), 4, 5))
        o += 1
        steps.append(_recovery(o, 180, _hr_zone_target(), 1, 1))
        o += 1
    steps.append(create_cooldown_step(900.0, step_order=o))
    return _make(f"Hill Repeats {dur_min}m", steps, dur_min)


# ── Cycling label → builder dispatch ─────────────────────────────────────────

_BUILDERS: dict[str, Any] = {
    "Easy Spin":        lambda d: _easy_spin(d),
    "Zone 2 Steady":    lambda d: _zone2_steady(d),
    "Recovery Spin":    lambda d: _recovery_spin(d),
    "Structured Z2":    lambda d: _structured_z2(d),
    "Z2 + Hills":       lambda d: _z2_hills(d),
    "Cadence Drills":   lambda d: _cadence_drills(d),
    "Hilly Z2":         lambda d: _hilly_z2(d),
    "Z2 Endurance":     lambda d: _z2_endurance(d),
    "Low Cadence":      lambda d: _low_cadence(d),
    "Easy Prep Ride":   lambda d: _easy_prep_ride(d),
    "FTP Test":         lambda d: _ftp_test("FTP Test", d),
    "FTP Re-test":      lambda d: _ftp_test("FTP Re-test", d),
    "Final FTP Test":   lambda d: _ftp_test("Final FTP Test", d),
    "Tempo Intervals":  lambda d: _tempo_intervals(d),
    "Long Ride":        lambda d: _long_ride("Long Ride", d),
    "Long Ride (Easy)": lambda d: _long_ride("Long Ride Easy", d),
    "Easy Ride":        lambda d: _easy_ride(d),
    "Z2 Ride":          lambda d: _z2_ride(d),
    "Low Cadence Ride": lambda d: _low_cadence_ride(d),
    "Sweetspot Ride":   lambda d: _sweetspot_ride(d),
    "Over-Unders":      lambda d: _over_unders(d),
    "Threshold Ride":   lambda d: _threshold_ride(d),
    "Hill Repeats":     lambda d: _hill_repeats(d),
}

# Name prefixes used when generating workout names — used to find and delete stale uploads
_NAME_PREFIXES: tuple[str, ...] = (
    "Easy Spin ", "Zone 2 Steady ", "Recovery Spin ", "Structured Z2 ", "Z2 + Hills ",
    "Cadence Drills ", "Hilly Z2 ", "Z2 Endurance ", "Low Cadence ", "Easy Prep Ride ",
    "FTP Test", "FTP Re-test", "Final FTP Test", "Tempo Intervals ", "Long Ride ",
    "Long Ride Easy ", "Easy Ride ", "Z2 Ride ", "Low Cadence Ride ", "Sweetspot Ride ",
    "Over-Unders ", "Threshold Ride ", "Hill Repeats ",
    # Strength & ruck
    "Kettlebell Wk", "Light KB Wk", "MaxiClimber Wk", "Ruck ",
)


def _resolve_builder(label: str) -> Any | None:
    """Return a builder for a label, tolerating coach-generated free-text labels.

    Plan labels match `_BUILDERS` keys exactly, but the coach's `propose_plan_change`
    `new_label` is model-generated and may carry a duration suffix or differ in case.
    Falls back through: exact → duration-stripped → case-insensitive → substring.
    """
    if label in _BUILDERS:
        return _BUILDERS[label]
    base = re.sub(r"\s*\d+\s*m(in)?\s*$", "", label, flags=re.I).strip()
    if base in _BUILDERS:
        return _BUILDERS[base]
    low = base.lower()
    for key, b in _BUILDERS.items():
        if key.lower() == low:
            return b
    for key, b in _BUILDERS.items():
        if key.lower() in low or low in key.lower():
            return b
    return None


# ── Strength & ruck workout builders ────────────────────────────────────────

def _kb_workout_steps(specs: dict | None, dur_min: int) -> list:
    """Build timed superset blocks from KB exercise specs; fallback to a single timed block."""
    if not specs:
        return [
            create_warmup_step(180.0, step_order=1),
            _interval(2, max(60.0, (dur_min - 5) * 60), _open_target()),
            create_cooldown_step(120.0, step_order=3),
        ]
    groups: dict[str, list] = {}
    for ex in specs["exercises"]:
        groups.setdefault(ex["id"][0], []).append(ex)
    steps: list = [create_warmup_step(180.0, step_order=1)]
    order = 2
    for _ in sorted(groups.keys()):
        # One 4-min open interval per superset group (A, B, C, D)
        steps.append(_interval(order, 240.0, _open_target()))
        order += 1
    steps.append(create_cooldown_step(120.0, step_order=order))
    return steps


def _kb_full_workout(week_num: int, dur_min: int) -> FitnessEquipmentWorkout:
    """Full KB circuit for KB + MaxiClimber sessions (from KB_FULL_SPECS)."""
    steps = _kb_workout_steps(KB_FULL_SPECS.get(week_num), dur_min)
    return _make_fe(f"Kettlebell Wk{week_num} {dur_min}m", steps, dur_min)


def _kb_light_workout(week_num: int, dur_min: int) -> FitnessEquipmentWorkout:
    """Abbreviated KB circuit for Light KB / post-ruck sessions (from KB_SPECS)."""
    steps = _kb_workout_steps(KB_SPECS.get(week_num), dur_min)
    return _make_fe(f"Light KB Wk{week_num} {dur_min}m", steps, dur_min)


def _maxiclimber_workout(week_num: int, dur_min: int) -> CyclingWorkout:
    """Structured MaxiClimber intervals from MAXI_INTERVALS for the given week.

    Uses a RepeatGroup so Garmin displays '10 × interval (Xs rest)' rather
    than 20 flat steps. Sport type is cycling so Garmin classifies this as
    cardio and renders the structured warmup/intervals/cooldown with HR zones.
    """
    spec = MAXI_INTERVALS.get(week_num)
    if not spec:
        steps = [
            create_warmup_step(180.0, step_order=1),
            _interval(2, max(60.0, dur_min * 60 - 360.0), _hr_zone_target(), 2, 3),
            create_cooldown_step(180.0, step_order=3),
        ]
        return _make_cardio(f"MaxiClimber Wk{week_num} {dur_min}m", steps, dur_min)

    sets = spec["sets"]
    work_s = float(spec["work_s"])
    rest_s = float(spec["rest_s"])
    norwegian = spec.get("norwegian", False)
    easy = spec.get("easy", False)

    if norwegian:
        work_lo, work_hi = 4, 4
    elif easy:
        work_lo, work_hi = 1, 2
    else:
        work_lo, work_hi = 2, 3

    repeat = create_repeat_group(
        iterations=sets,
        workout_steps=[
            _interval(1, work_s, _hr_zone_target(), work_lo, work_hi),
            _recovery(2, rest_s, _hr_zone_target(), 1, 1),
        ],
        step_order=2,
    )
    steps = [
        create_warmup_step(180.0, step_order=1),
        repeat,
        create_cooldown_step(180.0, step_order=3),
    ]

    calculated_dur = math.ceil((sets * (spec["work_s"] + spec["rest_s"]) + 360) / 60)
    return _make_cardio(f"MaxiClimber Wk{week_num} {dur_min}m", steps, calculated_dur)


def _ruck_workout(dur_min: int) -> HikingWorkout:
    """Timed ruck with Z2 HR guidance."""
    main_secs = max(60.0, (dur_min - 20) * 60)
    return _make_hike(f"Ruck {dur_min}m", [
        create_warmup_step(600.0, step_order=1),
        _interval(2, main_secs, _hr_zone_target(), 2, 2),
        create_cooldown_step(600.0, step_order=3),
    ], dur_min)


def _build_strength_ruck_workout(kind: str, week_num: int, dur_min: int) -> Any | None:
    if kind == "KB Full":
        return _kb_full_workout(week_num, dur_min)
    if kind == "KB Light":
        return _kb_light_workout(week_num, dur_min)
    if kind == "MaxiClimber":
        return _maxiclimber_workout(week_num, dur_min)
    if kind == "Ruck":
        return _ruck_workout(dur_min)
    return None


# ── Schedule builders ────────────────────────────────────────────────────────

def _workout_schedule() -> dict[tuple[str, int], list[str]]:
    """Map (label, duration) → dates for cycling sessions, applying any coach plan overrides.

    An override that swaps a day to a non-bike type (ruck/strength/rest) drops that
    cycling workout entirely so the stale one is removed on the next full re-sync.
    """
    from .history import get_plan_override

    schedule: dict[tuple[str, int], list[str]] = defaultdict(list)
    for wk_idx, week in enumerate(TRAINING_WEEKS):
        for day_idx, (stype, label, dur) in enumerate(week):
            d = PLAN_START + timedelta(weeks=wk_idx, days=day_idx)
            ov = get_plan_override(d.isoformat())
            if ov:
                o_type = ov.get("session_type") or stype
                o_label = ov.get("label") or label
                o_dur = ov.get("duration_min") or dur
                if o_type not in _BIKE_TYPES:
                    # Swapped to a non-cycling session — no Garmin cycling workout for this day.
                    continue
                if _resolve_builder(o_label) is None:
                    print(f"  [warn] override label '{o_label}' on {d.isoformat()} has no "
                          f"builder; using plan label '{label}'")
                    o_label = label
                stype, label, dur = o_type, o_label, o_dur
            if stype in _BIKE_TYPES:
                schedule[(label, dur)].append(d.isoformat())
    return schedule


def _specs_for(stype: str, label: str, dur: int, week_num: int) -> list[tuple]:
    """Map one (override-resolved) plan session to its Garmin workout spec(s).

    Returns ``("bike", label, dur_min)`` for cycling sessions and
    ``("sr", kind, week_num, dur_min)`` for strength/ruck sub-workouts. Compound
    sessions (KB + MaxiClimber, Ruck + KB) expand to two specs. This is the single
    source of truth for the plan-label → Garmin-workout mapping, shared by the bulk
    re-sync (`_workout_schedule_strength_ruck`) and the per-date push
    (`_workouts_for_date`). week_num=0 is used for ruck (all durations share
    structure regardless of week).
    """
    if stype in _BIKE_TYPES:
        return [("bike", label, dur)]
    if stype not in _STRENGTH_RUCK_TYPES:
        return []
    if label == "KB + MaxiClimber":
        spec = MAXI_INTERVALS.get(week_num)
        maxi_dur = math.ceil((spec["sets"] * (spec["work_s"] + spec["rest_s"]) + 360) / 60) if spec else 25
        return [("sr", "KB Full", week_num, 20), ("sr", "MaxiClimber", week_num, maxi_dur)]
    if label == "Ruck + KB":
        ruck_dur = RUCK_SPECS.get(week_num, {}).get("ruck_min", max(30, dur - 30))
        return [("sr", "Ruck", 0, ruck_dur), ("sr", "KB Light", week_num, 30)]
    if label == "Light KB":
        return [("sr", "KB Light", week_num, dur)]
    if label in ("Easy MaxiClimber", "MaxiClimber"):
        return [("sr", "MaxiClimber", week_num, dur)]
    if stype == "ruck":
        return [("sr", "Ruck", 0, dur)]
    print(f"  [skip] no handler for strength/ruck label '{label}'")
    return []


def _workout_schedule_strength_ruck() -> dict[tuple[str, int, int], list[str]]:
    """Map (kind, week_num, dur_min) → dates for strength and ruck sessions.

    Compound sessions (KB + MaxiClimber, Ruck + KB) are split into two sub-templates
    so each appears as a separate workout on the Garmin calendar (see `_specs_for`).
    """
    from .history import get_plan_override

    schedule: dict[tuple[str, int, int], list[str]] = defaultdict(list)

    for wk_idx, week in enumerate(TRAINING_WEEKS):
        week_num = wk_idx + 1
        for day_idx, (stype, label, dur) in enumerate(week):
            if stype not in _STRENGTH_RUCK_TYPES:
                continue
            d = PLAN_START + timedelta(weeks=wk_idx, days=day_idx)
            ov = get_plan_override(d.isoformat())
            if ov:
                o_type = ov.get("session_type") or stype
                o_label = ov.get("label") or label
                o_dur = ov.get("duration_min") or dur
                if o_type not in _STRENGTH_RUCK_TYPES:
                    continue
                stype, label, dur = o_type, o_label, o_dur

            date_str = d.isoformat()
            for kind, sr_label, sr_week, sr_dur in (
                (s[0], s[1], s[2], s[3]) for s in _specs_for(stype, label, dur, week_num) if s[0] == "sr"
            ):
                schedule[(sr_label, sr_week, sr_dur)].append(date_str)

    return schedule


def _workouts_for_date(d: date) -> list[tuple]:
    """Override-aware Garmin workout specs for a single date (see `_specs_for`).

    Mirrors the per-day logic of the two bulk builders: reads the plan session
    (12-week plan tuples directly, so week-keyed KB/MaxiClimber/ruck specs resolve;
    other blocks via the override-aware lookups) and applies any coach plan override.
    """
    from .history import get_plan_override

    delta = (d - PLAN_START).days
    if 0 <= delta < len(TRAINING_WEEKS) * 7:
        wk_idx, day_idx = divmod(delta, 7)
        week_num = wk_idx + 1
        stype, label, dur = TRAINING_WEEKS[wk_idx][day_idx]
    else:
        from .plan import session_for_date_extended
        from .hr_plan import hr_session_for_date
        sess = session_for_date_extended(d) or hr_session_for_date(d)
        if not sess:
            return []
        week_num = 0
        stype, label, dur = sess

    ov = get_plan_override(d.isoformat())
    if ov:
        stype = ov.get("session_type") or stype
        label = ov.get("label") or label
        dur = ov.get("duration_min") or dur
    return _specs_for(stype, label, dur, week_num)


# ── Upload helpers ───────────────────────────────────────────────────────────

def _extract_id(response: Any) -> int | None:
    if isinstance(response, list):
        response = response[0] if response else {}
    if isinstance(response, dict):
        return (
            response.get("workoutId")
            or (response.get("workout") or {}).get("workoutId")
        )
    return None


def _delete_existing_plan_workouts(api: Any, dry_run: bool = False) -> None:
    """Delete any Garmin workout whose name matches a plan-generated name prefix."""
    print("Scanning Garmin Connect for existing plan workouts to replace...")
    start = 0
    deleted = 0
    while True:
        try:
            batch = api.get_workouts(start=start, limit=100)
        except Exception as exc:
            print(f"  [warn] could not fetch workouts: {exc}")
            break
        if not batch:
            break
        for w in batch:
            wname = w.get("workoutName", "")
            if any(wname.startswith(p) for p in _NAME_PREFIXES):
                wid = w.get("workoutId")
                if dry_run:
                    print(f"  [dry]  would delete '{wname}' (id={wid})")
                else:
                    try:
                        api.delete_workout(wid)
                        print(f"  [deleted] '{wname}' (id={wid})")
                        deleted += 1
                    except Exception as exc:
                        print(f"  [warn] could not delete '{wname}' (id={wid}): {exc}")
        if len(batch) < 100:
            break
        start += 100
    if not dry_run:
        print(f"  {deleted} existing plan workout(s) removed")


def _schedule_dates(api: Any, workout_id: Any, dates: list[str],
                    summary: dict, failed: list[tuple[Any, str]]) -> None:
    for date_str in dates:
        try:
            api.schedule_workout(workout_id, date_str)
            summary["scheduled"] += 1
            print(f"    scheduled {date_str}")
        except Exception as exc:
            failed.append((workout_id, date_str))
            print(f"    [error] schedule {date_str}: {exc}")


def upload_and_schedule(api: Any, dry_run: bool = False) -> dict[str, int]:
    """Delete stale plan workouts, upload fresh ones (override-aware), and schedule them.

    Schedule failures are retried once at the end; any dates still unscheduled
    are returned in the summary so the caller knows a re-run is needed.

    Returns a summary dict: {templates, scheduled, errors, failed_dates}.
    """
    _delete_existing_plan_workouts(api, dry_run=dry_run)
    failed_schedules: list[tuple[Any, str]] = []

    # ── Cycling workouts ──────────────────────────────────────────────────────
    schedule = _workout_schedule()
    total_sessions = sum(len(v) for v in schedule.values())
    print(f"\nCycling plan: {len(schedule)} unique templates, {total_sessions} sessions")

    summary = {"templates": 0, "scheduled": 0, "errors": 0}
    for (label, dur), dates in sorted(schedule.items()):
        builder = _resolve_builder(label)
        if not builder:
            print(f"  [skip] no builder for '{label}' {dur}m")
            summary["errors"] += 1
            continue

        workout = builder(dur)
        if dry_run:
            print(f"  [dry]  '{label}' {dur}m → would schedule on {', '.join(dates)}")
            summary["templates"] += 1
            summary["scheduled"] += len(dates)
            continue

        try:
            response = api.upload_cycling_workout(workout)
        except Exception as exc:
            print(f"  [error] upload failed for '{label}' {dur}m: {exc}")
            summary["errors"] += 1
            continue

        workout_id = _extract_id(response)
        if not workout_id:
            print(f"  [error] no workoutId in response for '{label}' {dur}m: {response}")
            summary["errors"] += 1
            continue

        summary["templates"] += 1
        print(f"  uploaded '{label}' {dur}m → id={workout_id}")
        _schedule_dates(api, workout_id, dates, summary, failed_schedules)

    # ── Strength & ruck workouts ──────────────────────────────────────────────
    sr_schedule = _workout_schedule_strength_ruck()
    sr_total = sum(len(v) for v in sr_schedule.values())
    print(f"\nStrength/ruck plan: {len(sr_schedule)} unique templates, {sr_total} sessions")

    for (kind, week_num, dur), dates in sorted(sr_schedule.items()):
        workout = _build_strength_ruck_workout(kind, week_num, dur)
        if not workout:
            print(f"  [skip] no builder for '{kind}' wk{week_num} {dur}m")
            summary["errors"] += 1
            continue

        wname = workout.workoutName
        if dry_run:
            print(f"  [dry]  '{wname}' → would schedule on {', '.join(dates)}")
            summary["templates"] += 1
            summary["scheduled"] += len(dates)
            continue

        try:
            response = api.upload_workout(workout.to_dict())
        except Exception as exc:
            print(f"  [error] upload failed for '{wname}': {exc}")
            summary["errors"] += 1
            continue

        workout_id = _extract_id(response)
        if not workout_id:
            print(f"  [error] no workoutId for '{wname}': {response}")
            summary["errors"] += 1
            continue

        summary["templates"] += 1
        print(f"  uploaded '{wname}' → id={workout_id}")
        _schedule_dates(api, workout_id, dates, summary, failed_schedules)

    # Retry failed schedules once (transient API errors are common), then
    # surface anything still missing so the caller knows to re-run.
    still_failed: list[str] = []
    if failed_schedules:
        print(f"\nRetrying {len(failed_schedules)} failed schedule(s)...")
        for workout_id, date_str in failed_schedules:
            try:
                api.schedule_workout(workout_id, date_str)
                summary["scheduled"] += 1
                print(f"  scheduled {date_str} (retry)")
            except Exception as exc:
                summary["errors"] += 1
                still_failed.append(date_str)
                print(f"  [error] schedule {date_str} failed again: {exc}")
    summary["failed_dates"] = still_failed
    if still_failed:
        print(f"\n[warn] {len(still_failed)} date(s) left unscheduled: "
              f"{', '.join(still_failed)} — re-run --workouts to fill the gaps")

    return summary


# ── Per-date surgical push (single applied override → Garmin) ─────────────────

def _scheduled_items_on(sched: Any, date_str: str) -> list[tuple[Any, str]]:
    """Extract (scheduledWorkoutId, title) for plan-generated workouts on date_str.

    Garmin's month response shape is not contractually fixed, so probe candidate
    keys defensively and log each raw item at DEBUG (house style — see metrics.py).
    Only items whose title matches `_NAME_PREFIXES` are returned, so the athlete's
    own (non-plan) workouts are never touched.
    """
    if isinstance(sched, dict):
        items = sched.get("calendarItems") or sched.get("scheduledWorkouts") or sched.get("workouts") or []
    elif isinstance(sched, list):
        items = sched
    else:
        items = []
    out: list[tuple[Any, str]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        logger.debug("scheduled item: %s", it)
        idate = (it.get("date") or it.get("scheduledDate")
                 or it.get("calendarDate") or it.get("itemDate"))
        if idate != date_str:
            continue
        title = it.get("title") or it.get("workoutName") or it.get("name") or ""
        if not any(title.startswith(p) for p in _NAME_PREFIXES):
            continue
        sid = (it.get("scheduledWorkoutId") or it.get("workoutScheduleId") or it.get("id"))
        if sid:
            out.append((sid, title))
    return out


def _find_template_id(api: Any, name: str) -> int | None:
    """Return the id of an existing workout template named `name`, or None.

    Lets the per-date push reuse a template already in the library (it's scheduled
    on other dates too) instead of uploading a near-duplicate each time.
    """
    start = 0
    while True:
        try:
            batch = api.get_workouts(start=start, limit=100)
        except Exception as exc:
            logger.debug("get_workouts failed while looking up '%s': %s", name, exc)
            return None
        if not batch:
            return None
        for w in batch:
            if w.get("workoutName") == name:
                return w.get("workoutId")
        if len(batch) < 100:
            return None
        start += 100


def _push_one_workout(api: Any, workout: Any, kind: str, date_str: str) -> int | None:
    """Upload (or reuse) one workout template and schedule it on date_str."""
    name = workout.workoutName
    wid = _find_template_id(api, name)
    if wid:
        print(f"  reusing template '{name}' (id={wid})")
    else:
        try:
            response = (api.upload_cycling_workout(workout) if kind == "bike"
                        else api.upload_workout(workout.to_dict()))
        except Exception as exc:
            print(f"  [error] upload '{name}': {exc}")
            return None
        wid = _extract_id(response)
        if not wid:
            print(f"  [error] no workoutId for '{name}': {response}")
            return None
        print(f"  uploaded '{name}' → id={wid}")
    try:
        api.schedule_workout(wid, date_str)
        print(f"    scheduled {date_str}")
        return wid
    except Exception as exc:
        print(f"  [error] schedule {date_str}: {exc}")
        return None


def apply_override_to_garmin(api: Any, date_str: str, dry_run: bool = False) -> dict:
    """Surgically reflect a single date's (override-resolved) session on Garmin.

    Unschedules only the plan-generated workout(s) already on that date — other
    dates sharing the same template are untouched (we never `delete_workout` here) —
    then uploads/schedules the new session. A swap to rest/non-cycling unschedules
    and adds nothing. Best-effort: returns a status dict and never raises.

    Returns {ok, unscheduled, scheduled, label, error}.
    """
    result: dict = {"ok": False, "unscheduled": 0, "scheduled": 0, "label": None, "error": None}
    try:
        d = date.fromisoformat(date_str)
    except ValueError as exc:
        result["error"] = f"bad date: {exc}"
        return result

    # 1. Unschedule the existing plan workout(s) on this date.
    try:
        sched = api.get_scheduled_workouts(d.year, d.month)
        items = _scheduled_items_on(sched, date_str)
    except Exception as exc:
        items = []
        logger.debug("could not fetch scheduled workouts for %s: %s", date_str, exc)
        result["error"] = f"fetch scheduled failed: {exc}"
    for sid, name in items:
        if dry_run:
            print(f"  [dry] would unschedule '{name}' (sid={sid}) on {date_str}")
            result["unscheduled"] += 1
            continue
        try:
            api.unschedule_workout(sid)
            result["unscheduled"] += 1
            print(f"  unscheduled '{name}' (sid={sid}) on {date_str}")
        except Exception as exc:
            print(f"  [warn] unschedule {sid} failed: {exc}")

    # 2. Build, upload and schedule the new session for this date.
    labels: list[str] = []
    for spec in _workouts_for_date(d):
        if spec[0] == "bike":
            _, label, dur = spec
            builder = _resolve_builder(label)
            if not builder:
                print(f"  [skip] no builder for '{label}' {dur}m")
                continue
            workout = builder(dur)
        else:  # ("sr", kind, week_num, dur)
            _, kind, week_num, dur = spec
            workout = _build_strength_ruck_workout(kind, week_num, dur)
            if not workout:
                print(f"  [skip] no builder for '{kind}' wk{week_num} {dur}m")
                continue
        labels.append(workout.workoutName)
        if dry_run:
            print(f"  [dry] would upload+schedule '{workout.workoutName}' on {date_str}")
            result["scheduled"] += 1
            continue
        if _push_one_workout(api, workout, spec[0], date_str):
            result["scheduled"] += 1

    result["label"] = ", ".join(labels) if labels else None
    result["ok"] = result["error"] is None
    return result
