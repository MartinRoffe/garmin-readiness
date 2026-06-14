"""HRV-guided daily session modulation (green/amber/red traffic light).

Turns morning readiness data into a concrete, accept/declinable change to
today's planned session. Green = train as planned; amber = keep duration but
drop intensity; red = swap to short recovery. Applied via the existing
plan-override machinery (/apply-plan-change), so the calendar, email, and
Garmin workout sync all respect an accepted modulation automatically.
"""
from __future__ import annotations

import statistics
from datetime import date
from typing import Optional

from .history import get_plan_override, raw_history
from .hr_plan import hr_session_for_date
from .metrics import DailyMetrics
from .plan import session_for_date_extended

# Map a planned session type to its easier amber-day variant (duration kept).
EASIER_VARIANT: dict[str, tuple[str, str]] = {
    "ftp":      ("bike", "Zone 2 Steady"),
    "tempo":    ("bike", "Zone 2 Steady"),
    "long":     ("long", "Long Ride (Easy)"),
    "bike":     ("bike", "Recovery Spin"),
    "strength": ("strength", "Light KB"),
    "ruck":     ("ruck", "Easy Walk (no load)"),
}

# Haute Route plan equivalent — kept separate so swaps stay within the HR
# plan's type/label vocabulary (hr_calendar.html colours and modals key off
# them). recovery/gym are deliberately absent: already easy, pill only.
HR_EASIER_VARIANT: dict[str, tuple[str, str]] = {
    "ftp":          ("endurance", "Z2 Endurance"),
    "tempo":        ("endurance", "Z2 Endurance"),
    "vo2":          ("endurance", "Z2 Endurance"),
    "sweetspot":    ("endurance", "Z2 Endurance"),
    "endurance":    ("endurance", "Z2 Easy"),
    "long":         ("long", "Long Ride (Easy)"),
    "back_to_back": ("long", "Long Ride (Easy)"),
}


def hrv_traffic_light(m: DailyMetrics, comp_z: Optional[float]) -> dict:
    """Classify today's readiness as green / amber / red / unknown.

    Primary signal: last-night HRV vs its own 30-day baseline (z-score),
    with the 7-day vs 30-day mean ratio as a secondary chronic-suppression
    signal, and the composite readiness z as a backstop.
    """
    # Exclude the row for m's own date from the baseline (when present) rather
    # than blindly dropping the last row — before the watch syncs, the last DB
    # row is yesterday and must stay in the baseline.
    today_iso = m.date.isoformat() if m.date else None
    rows = raw_history(31)
    baseline = [r["hrv_last_night"] for r in rows
                if r.get("date") != today_iso and r["hrv_last_night"] is not None]
    hrv_today = m.hrv_last_night

    hrv_z = None
    ratio = None
    if hrv_today is not None and len(baseline) >= 7:
        mean = statistics.mean(baseline)
        stdev = statistics.pstdev(baseline)
        if stdev > 0:
            hrv_z = (hrv_today - mean) / stdev
        last7 = [v for v in baseline[-7:] if v is not None]
        if last7 and mean > 0:
            ratio = statistics.mean(last7) / mean

    if hrv_z is None and comp_z is None:
        return {"status": "unknown", "hrv_z": None, "ratio": None,
                "reason": "Not enough HRV history yet for a baseline."}

    def _fmt(reasons: list[str]) -> str:
        return "; ".join(reasons)

    red_reasons = []
    if hrv_z is not None and hrv_z < -1.5:
        red_reasons.append(f"HRV {hrv_today:.0f} ms is {abs(hrv_z):.1f}σ below your 30-day baseline")
    if comp_z is not None and comp_z < -1.2:
        red_reasons.append(f"composite readiness is {comp_z:+.1f}σ")
    if red_reasons:
        return {"status": "red", "hrv_z": hrv_z, "ratio": ratio, "reason": _fmt(red_reasons)}

    amber_reasons = []
    if hrv_z is not None and hrv_z < -0.75:
        amber_reasons.append(f"HRV {hrv_today:.0f} ms is {abs(hrv_z):.1f}σ below baseline")
    if comp_z is not None and comp_z < -0.5:
        amber_reasons.append(f"composite readiness is {comp_z:+.1f}σ")
    if ratio is not None and ratio < 0.92:
        amber_reasons.append(f"7-day HRV average is {(1 - ratio) * 100:.0f}% below your 30-day norm")
    if amber_reasons:
        return {"status": "amber", "hrv_z": hrv_z, "ratio": ratio, "reason": _fmt(amber_reasons)}

    return {"status": "green", "hrv_z": hrv_z, "ratio": ratio,
            "reason": "HRV and readiness in normal range."}


def session_modulation(target: date, m: DailyMetrics, comp_z: Optional[float],
                       light: Optional[dict] = None) -> Optional[dict]:
    """Suggested session modification for today, or None when nothing to do.

    None when: status green/unknown, no planned session, rest day, or an
    override already exists for today (don't re-suggest over a decision).
    Pass a precomputed `light` (from hrv_traffic_light) to avoid recomputing.
    """
    if light is None:
        light = hrv_traffic_light(m, comp_z)
    status = light["status"]
    base = {"light": light, "date": target.isoformat()}

    sess = session_for_date_extended(target)
    hr_day = False
    if sess is None:
        sess = hr_session_for_date(target)
        hr_day = sess is not None
    if sess is None or sess[0] == "rest":
        return base if status in ("amber", "red") else None  # show pill, no swap
    if get_plan_override(target.isoformat()):
        return None
    stype, label, dur = sess
    base.update({"planned_type": stype, "planned_label": label, "planned_dur": dur})

    if status == "red":
        base.update({
            "session_type": "recovery" if hr_day else "bike",
            "label": "Recovery Spin",
            "duration_min": 30,
            "headline": "Red day — swap to recovery",
        })
        return base
    if status == "amber":
        variant = (HR_EASIER_VARIANT if hr_day else EASIER_VARIANT).get(stype)
        if variant is None or (variant[0] == stype and variant[1] == label):
            return base  # already easy — show pill only, no swap
        base.update({
            "session_type": variant[0],
            "label": variant[1],
            "duration_min": dur,
            "headline": "Amber day — keep duration, drop intensity",
        })
        return base
    return None
