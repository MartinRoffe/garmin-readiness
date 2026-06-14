"""Proactive fatigue alert checks."""
from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Optional

from .history import (
    load_activities_by_date,
    pmc_history,
    raw_history,
    weekly_monotony_strain,
)
from .plan import session_for_date


def _signal_z(rows: list[dict], field: str) -> Optional[float]:
    """Z-score of today's value for `field` vs its own baseline (all prior rows).

    Returns None (signal abstains) when today's value is missing, fewer than
    7 baseline samples exist, or the baseline has no variance.
    """
    today_val = rows[-1].get(field) if rows else None
    if today_val is None:
        return None
    baseline = [r.get(field) for r in rows[:-1] if r.get(field) is not None]
    if len(baseline) < 7:
        return None
    stdev = statistics.pstdev(baseline)
    if stdev == 0:
        return None
    return (today_val - statistics.mean(baseline)) / stdev


def check_fatigue_alerts(today: date) -> list[dict]:
    alerts: list[dict] = []

    # 1. HRV_TREND: 4 consecutive mornings of declining HRV
    rows = raw_history(5)
    hrv_vals = [r["hrv_last_night"] for r in rows if r.get("hrv_last_night") is not None]
    if len(hrv_vals) >= 4:
        last4 = hrv_vals[-4:]
        if all(last4[i] < last4[i - 1] for i in range(1, 4)):
            alerts.append({
                "type": "HRV_TREND",
                "severity": "HIGH",
                "message": (
                    f"HRV has declined for 4 consecutive mornings "
                    f"({last4[0]:.0f} → {last4[-1]:.0f} ms). "
                    "Consider reducing today's intensity."
                ),
            })

    # 2. TSB_DEEP: TSB below -180 for 5+ of the last 6 days
    hist = pmc_history(days=6)
    tsb_vals = [h["tsb"] for h in hist if h.get("tsb") is not None]
    if sum(1 for v in tsb_vals if v < -180) >= 5:
        alerts.append({
            "type": "TSB_DEEP",
            "severity": "HIGH",
            "message": (
                "Form (TSB) has been very negative for 5+ days. "
                "A rest or recovery day is overdue."
            ),
        })

    # 3. VOLUME_SPIKE: actual this week >20% over planned
    mon = today - timedelta(days=today.weekday())
    planned_min = 0
    for i in range(7):
        sess = session_for_date(mon + timedelta(days=i))
        if sess and sess[0] != "rest" and sess[2]:
            planned_min += sess[2]

    if planned_min > 0:
        acts_this_week = load_activities_by_date(mon, today)
        actual_min = sum(
            int((a.get("duration_seconds") or 0) / 60)
            for day_acts in acts_this_week.values()
            for a in day_acts
        )
        if actual_min > planned_min * 1.20:
            planned_h = planned_min / 60
            actual_h = actual_min / 60
            alerts.append({
                "type": "VOLUME_SPIKE",
                "severity": "MODERATE",
                "message": (
                    f"You're tracking {round((actual_min / planned_min - 1) * 100)}% over planned volume this week "
                    f"({actual_h:.1f}h vs {planned_h:.1f}h planned). Protect the rest of the week."
                ),
            })

    # 4. ILLNESS_RISK: 2-of-3 — depressed HRV, elevated resting HR (or rest
    #    stress as fallback), degraded sleep. Distinct from fatigue: the
    #    intervention is full rest, not just easier training.
    rows31 = raw_history(31)
    # Only evaluate when the latest row really is `today` — before the watch
    # syncs, rows31[-1] is yesterday and we'd report stale values as today's.
    if rows31 and rows31[-1].get("date") == today.isoformat():
        hrv_z = _signal_z(rows31, "hrv_last_night")
        rhr_z = _signal_z(rows31, "resting_hr")
        if rhr_z is None:
            rhr_z = _signal_z(rows31, "rest_stress")  # fallback signal
        sleep_z = _signal_z(rows31, "sleep_score")

        triggers = []
        today_row = rows31[-1]
        if hrv_z is not None and hrv_z < -1.5:
            triggers.append(f"HRV {today_row['hrv_last_night']:.0f} ms ({hrv_z:+.1f}σ)")
        if rhr_z is not None and rhr_z > 1.5:
            if today_row.get("resting_hr") is not None:
                triggers.append(f"resting HR {today_row['resting_hr']:.0f} bpm ({rhr_z:+.1f}σ)")
            else:
                triggers.append(f"resting stress elevated ({rhr_z:+.1f}σ)")
        if sleep_z is not None and sleep_z < -1.5:
            triggers.append(f"sleep score {today_row['sleep_score']:.0f} ({sleep_z:+.1f}σ)")

        if len(triggers) >= 2:
            alerts.append({
                "type": "ILLNESS_RISK",
                "severity": "HIGH",
                "message": (
                    "Possible illness onset: " + ", ".join(triggers) + ". "
                    "Consider full rest (not just an easy spin) and monitor temperature."
                ),
            })

    # 5. MONOTONY_HIGH: Foster monotony > 2.0 in the most recent meaningful week
    try:
        ms = weekly_monotony_strain(weeks=2)
        recent = None
        for wk in reversed(ms):
            elapsed = (today - wk["week_start"]).days + 1
            if elapsed >= 4 and wk.get("monotony") is not None:
                recent = wk
                break
        if recent and recent["monotony"] > 2.0:
            alerts.append({
                "type": "MONOTONY_HIGH",
                "severity": "MODERATE",
                "message": (
                    f"Training monotony is {recent['monotony']:.1f} (>2.0) with strain "
                    f"{recent['strain']:.0f} — too-similar daily loads raise illness/overuse "
                    "risk. Make hard days harder and easy days easier."
                ),
            })
    except Exception:
        pass

    return alerts
