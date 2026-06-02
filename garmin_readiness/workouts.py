"""Build and schedule Garmin structured cycling workouts from the training plan."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from garminconnect.workout import (
    CyclingWorkout,
    ExecutableStep,
    WorkoutSegment,
    create_cooldown_step,
    create_warmup_step,
    StepType,
    TargetType,
    ConditionType,
    SportType,
)

from .plan import TRAINING_WEEKS, PLAN_START

_SPORT = {"sportTypeId": SportType.CYCLING, "sportTypeKey": "cycling", "displayOrder": 1}
_BIKE_TYPES = {"bike", "tempo", "ftp", "long"}


# ── Target type dicts ────────────────────────────────────────────────────────

def _hr_zone_target() -> dict[str, Any]:
    return {"workoutTargetTypeId": TargetType.HEART_RATE, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 4}

def _cadence_target() -> dict[str, Any]:
    return {"workoutTargetTypeId": TargetType.CADENCE, "workoutTargetTypeKey": "cadence", "displayOrder": 3}

def _no_target() -> dict[str, Any]:
    return {"workoutTargetTypeId": TargetType.NO_TARGET, "workoutTargetTypeKey": "no.target", "displayOrder": 1}

def _open_target() -> dict[str, Any]:
    return {"workoutTargetTypeId": TargetType.OPEN, "workoutTargetTypeKey": "open", "displayOrder": 6}


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


def _make(name: str, steps: list, dur_min: int) -> CyclingWorkout:
    return CyclingWorkout(
        workoutName=name,
        estimatedDurationInSecs=dur_min * 60,
        workoutSegments=[WorkoutSegment(segmentOrder=1, sportType=_SPORT, workoutSteps=steps)],
    )


# ── Individual workout builders ──────────────────────────────────────────────

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
    # 10m warmup + 5×(6m 60-70rpm Z3 + 3m Z1) + 25m Z2 + 10m cooldown = 90m
    steps: list = [create_warmup_step(600.0, step_order=1)]
    o = 2
    for _ in range(5):
        steps.append(_interval(o, 360, _cadence_target(), 60, 70))
        o += 1
        steps.append(_recovery(o, 180, _hr_zone_target(), 1, 1))
        o += 1
    steps.append(_interval(o, 1500, _hr_zone_target(), 2, 2))
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


# ── Label → builder dispatch ─────────────────────────────────────────────────

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
)


def _workout_schedule() -> dict[tuple[str, int], list[str]]:
    schedule: dict[tuple[str, int], list[str]] = defaultdict(list)
    for wk_idx, week in enumerate(TRAINING_WEEKS):
        for day_idx, (stype, label, dur) in enumerate(week):
            if stype in _BIKE_TYPES:
                d = PLAN_START + timedelta(weeks=wk_idx, days=day_idx)
                schedule[(label, dur)].append(d.isoformat())
    return schedule


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


def upload_and_schedule(api: Any, dry_run: bool = False) -> None:
    """Delete stale plan workouts, upload fresh ones, and schedule them."""
    _delete_existing_plan_workouts(api, dry_run=dry_run)
    schedule = _workout_schedule()
    total_sessions = sum(len(v) for v in schedule.values())
    print(f"\nPlan has {len(schedule)} unique session templates covering {total_sessions} sessions")

    for (label, dur), dates in sorted(schedule.items()):
        builder = _BUILDERS.get(label)
        if not builder:
            print(f"  [skip] no builder for '{label}' {dur}m")
            continue

        workout = builder(dur)
        if dry_run:
            print(f"  [dry]  '{label}' {dur}m → would schedule on {', '.join(dates)}")
            continue

        try:
            response = api.upload_cycling_workout(workout)
        except Exception as exc:
            print(f"  [error] upload failed for '{label}' {dur}m: {exc}")
            continue

        workout_id = _extract_id(response)
        if not workout_id:
            print(f"  [error] no workoutId in response for '{label}' {dur}m: {response}")
            continue

        print(f"  uploaded '{label}' {dur}m → id={workout_id}")
        for date_str in dates:
            try:
                api.schedule_workout(workout_id, date_str)
                print(f"    scheduled {date_str}")
            except Exception as exc:
                print(f"    [error] schedule {date_str}: {exc}")
