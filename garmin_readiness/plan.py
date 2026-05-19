"""12-week training plan data and lookup helpers."""
from __future__ import annotations

from datetime import date, timedelta

# Each week: list of 7 sessions Mon–Sun, each (type, label, duration_min)
# Types: rest | strength | bike | tempo | ftp | ruck | long
PLAN_START = date(2026, 5, 18)
assert PLAN_START.weekday() == 0, "Plan must start on Monday"

TRAINING_WEEKS: list[list[tuple[str, str, int]]] = [
    # WK 01
    [
        ("rest",     "Rest",                  0),
        ("strength", "KB + MaxiClimber",     45),
        ("bike",     "Easy Spin",            60),
        ("strength", "KB + MaxiClimber",     45),
        ("tempo",    "Threshold / Tempo Ride", 80),  # AI coach upgrade from Easy Spin
        ("ruck",     "Ruck  8 kg",           60),
        ("long",     "Long Ride",            90),
    ],
    # WK 02
    [
        ("rest",     "Rest",                  0),
        ("strength", "KB + MaxiClimber",     45),
        ("bike",     "Zone 2 Steady",        60),
        ("strength", "KB + MaxiClimber",     45),
        ("bike",     "Zone 2 Steady",        60),
        ("ruck",     "Ruck  8–10 kg",        70),
        ("long",     "Long Ride",           105),
    ],
    # WK 03
    [
        ("rest",     "Rest",                  0),
        ("strength", "Light KB",             35),
        ("ftp",      "FTP Test",             60),
        ("strength", "Easy MaxiClimber",     20),
        ("bike",     "Recovery Spin",        60),
        ("ruck",     "Ruck  10 kg",          80),
        ("long",     "Long Ride",           120),
    ],
    # WK 04 (deload)
    [
        ("rest",     "Rest",                  0),
        ("strength", "Light KB",             30),
        ("bike",     "Easy Spin",            45),
        ("strength", "MaxiClimber",          20),
        ("bike",     "Easy Spin",            45),
        ("ruck",     "Ruck  8 kg",           45),
        ("long",     "Long Ride",            75),
    ],
    # WK 05
    [
        ("rest",     "Rest",                  0),
        ("strength", "KB + MaxiClimber",     45),
        ("bike",     "Structured Z2",        60),
        ("strength", "KB + MaxiClimber",     45),
        ("bike",     "Z2 + Hills",           60),
        ("ruck",     "Ruck  10 kg",          75),
        ("long",     "Long Ride",           135),
    ],
    # WK 06
    [
        ("rest",     "Rest",                  0),
        ("strength", "KB + MaxiClimber",     45),
        ("bike",     "Cadence Drills",       60),
        ("strength", "KB + MaxiClimber",     45),
        ("bike",     "Hilly Z2",             60),
        ("ruck",     "Ruck  10–12 kg",       85),
        ("long",     "Long Ride",           140),
    ],
    # WK 07
    [
        ("rest",     "Rest",                  0),
        ("strength", "Light KB",             35),
        ("ftp",      "FTP Re-test",          60),
        ("strength", "Easy MaxiClimber",     25),
        ("tempo",    "Tempo Intervals",      60),
        ("ruck",     "Ruck  12 kg",          95),
        ("long",     "Long Ride",           150),
    ],
    # WK 08 (deload)
    [
        ("rest",     "Rest",                  0),
        ("strength", "Light KB",             30),
        ("bike",     "Easy Spin",            45),
        ("strength", "MaxiClimber",          20),
        ("bike",     "Easy Spin",            45),
        ("ruck",     "Ruck  10 kg",          50),
        ("long",     "Long Ride",            80),
    ],
    # WK 09
    [
        ("rest",     "Rest",                  0),
        ("strength", "KB + MaxiClimber",     45),
        ("bike",     "Z2 Endurance",         60),
        ("strength", "KB + MaxiClimber",     45),
        ("tempo",    "Tempo Intervals",      60),
        ("ruck",     "Ruck  12–15 kg",      105),
        ("long",     "Long Ride",           165),
    ],
    # WK 10
    [
        ("rest",     "Rest",                  0),
        ("strength", "KB + MaxiClimber",     45),
        ("bike",     "Low Cadence",          60),
        ("strength", "KB + MaxiClimber",     45),
        ("tempo",    "Tempo Intervals",      60),
        ("ruck",     "Ruck  12–15 kg",      110),
        ("long",     "Long Ride",           180),
    ],
    # WK 11
    [
        ("rest",     "Rest",                  0),
        ("strength", "KB + MaxiClimber",     45),
        ("bike",     "Z2 Endurance",         60),
        ("strength", "Light KB",             35),
        ("bike",     "Easy Prep Ride",       60),
        ("ruck",     "Easy Ruck  8 kg",      60),
        ("long",     "Long Ride",           210),
    ],
    # WK 12
    [
        ("rest",     "Rest",                  0),
        ("strength", "Light KB",             30),
        ("ftp",      "Final FTP Test",       60),
        ("strength", "Easy MaxiClimber",     20),
        ("bike",     "Easy Spin",            45),
        ("ruck",     "Celebration Ruck",     60),
        ("long",     "Long Ride (Easy)",    120),
    ],
]

_PLAN_DAYS = len(TRAINING_WEEKS) * 7  # 84

# AI coach recommendations keyed by ISO date; surfaced on the calendar card + modal.
COACH_NOTES: dict[str, str] = {
    "2026-05-22": (
        "You've earned capacity to absorb moderate work. Replace the second easy spin "
        "(May 22) with a threshold or tempo effort around 75–90 minutes. This maintains "
        "fatigue clearance while stimulating fitness without regression."
    ),
}


def session_for_date(d: date) -> tuple[str, str, int] | None:
    """Return (type, label, duration_min) for the given date, or None if outside the plan."""
    delta = (d - PLAN_START).days
    if delta < 0 or delta >= _PLAN_DAYS:
        return None
    week_idx, day_idx = divmod(delta, 7)
    return TRAINING_WEEKS[week_idx][day_idx]


def build_calendar_weeks() -> list[dict]:
    today = date.today()
    weeks = []
    for wk_idx, sessions in enumerate(TRAINING_WEEKS):
        wk_start = PLAN_START + timedelta(weeks=wk_idx)
        days = []
        for day_offset, (stype, label, dur) in enumerate(sessions):
            d = wk_start + timedelta(days=day_offset)
            dur_fmt = ""
            if dur:
                if dur < 60:
                    dur_fmt = f"{dur}m"
                elif dur % 60:
                    dur_fmt = f"{dur // 60}h{dur % 60:02d}m"
                else:
                    dur_fmt = f"{dur // 60}h"
            days.append({
                "date": d,
                "day_num": d.day,
                "month_abbr": d.strftime("%b"),
                "type": stype,
                "label": label,
                "dur_fmt": dur_fmt,
                "dur_min": dur,
                "is_today": d == today,
                "is_past": d < today,
                "coach_note": COACH_NOTES.get(d.isoformat(), ""),
            })
        weeks.append({"week_num": wk_idx + 1, "start": wk_start, "days": days})
    return weeks


# ── Tenerife Cycling Camp ─────────────────────────────────────────────────────
CAMP_START = date(2026, 8, 13)
CAMP_END   = date(2026, 8, 27)

TENERIFE_DAYS: list[dict] = [
    {"day": 0,  "date": date(2026, 8, 13), "intensity": "travel", "label": "Travel — Arrive Tenerife",              "km": 0,   "elev_m": 0},
    {"day": 1,  "date": date(2026, 8, 14), "intensity": "easy",   "label": "Leg Openers — Coastal Loop South",      "km": 40,  "elev_m": 450},
    {"day": 2,  "date": date(2026, 8, 15), "intensity": "medium", "label": "Tamaimo Climb + Teno Loop",              "km": 65,  "elev_m": 1100},
    {"day": 3,  "date": date(2026, 8, 16), "intensity": "hard",   "label": "Teide from the West — TF-38 Ascent",    "km": 90,  "elev_m": 2100},
    {"day": 4,  "date": date(2026, 8, 17), "intensity": "easy",   "label": "Active Recovery — Harbour Spin",        "km": 30,  "elev_m": 300},
    {"day": 5,  "date": date(2026, 8, 18), "intensity": "hard",   "label": "Masca + North Coast Grand Tour",        "km": 105, "elev_m": 2200},
    {"day": 6,  "date": date(2026, 8, 19), "intensity": "easy",   "label": "Banana Plantations & Alcalá Coffee",   "km": 45,  "elev_m": 500},
    {"day": 7,  "date": date(2026, 8, 20), "intensity": "rest",   "label": "Full Rest — Explore Los Gigantes",      "km": 0,   "elev_m": 0},
    {"day": 8,  "date": date(2026, 8, 21), "intensity": "easy",   "label": "Legs Back — Coastal Ramble",            "km": 50,  "elev_m": 600},
    {"day": 9,  "date": date(2026, 8, 22), "intensity": "hard",   "label": "Teide Full Loop — West Up, South Down", "km": 115, "elev_m": 2400},
    {"day": 10, "date": date(2026, 8, 23), "intensity": "easy",   "label": "Recovery Spin — Cliffs Views Route",   "km": 35,  "elev_m": 350},
    {"day": 11, "date": date(2026, 8, 24), "intensity": "hard",   "label": "Masca + Teide Double — Camp Finale",   "km": 130, "elev_m": 3200},
    {"day": 12, "date": date(2026, 8, 25), "intensity": "easy",   "label": "The Farewell Loop — Cliffs & Coffee",  "km": 40,  "elev_m": 400},
    {"day": 0,  "date": date(2026, 8, 26), "intensity": "rest",   "label": "Rest Before Flight",                    "km": 0,   "elev_m": 0},
    {"day": 0,  "date": date(2026, 8, 27), "intensity": "travel", "label": "Travel — Home",                         "km": 0,   "elev_m": 0},
]


def build_camp_weeks() -> list[dict]:
    today = date.today()
    days_by_date = {d["date"]: d for d in TENERIFE_DAYS}
    grid_start = date(2026, 8, 10)  # Monday before Aug 13
    weeks = []
    for wk in range(3):
        wk_start = grid_start + timedelta(weeks=wk)
        cells = []
        for day_off in range(7):
            d = wk_start + timedelta(days=day_off)
            camp_day = days_by_date.get(d)
            if camp_day:
                cells.append({
                    "date": d, "day_num": d.day, "month_abbr": d.strftime("%b"),
                    "intensity": camp_day["intensity"], "label": camp_day["label"],
                    "km": camp_day["km"], "elev_m": camp_day["elev_m"],
                    "camp_day_num": camp_day["day"],
                    "is_today": d == today, "is_past": d < today, "is_camp": True,
                })
            else:
                workout = CAMP_GRID_WORKOUTS.get(d)
                if workout:
                    dur = workout["dur_min"]
                    if dur < 60:
                        dur_fmt = f"{dur}m"
                    elif dur % 60:
                        dur_fmt = f"{dur // 60}h{dur % 60:02d}m"
                    else:
                        dur_fmt = f"{dur // 60}h"
                    cells.append({
                        "date": d, "day_num": d.day, "month_abbr": d.strftime("%b"),
                        "type": workout["type"], "label": workout["label"],
                        "dur_fmt": dur_fmt, "dur_min": dur,
                        "is_today": d == today, "is_past": d < today,
                        "is_camp": False, "is_workout": True,
                    })
                else:
                    cells.append({
                        "date": d, "day_num": d.day, "month_abbr": d.strftime("%b"),
                        "intensity": "empty", "is_camp": False, "is_workout": False,
                    })
        weeks.append({"week_label": wk_start.strftime("%-d %b"), "days": cells})
    return weeks


# Non-camp workout days that sit within the Tenerife grid window (Aug 10–30).
# Aug 10–11: pre-camp activation; Aug 28–30: first recovery days post-camp.
CAMP_GRID_WORKOUTS: dict[date, dict] = {
    date(2026, 8, 10): {"type": "bike",  "label": "Easy Spin",     "dur_min": 45},
    date(2026, 8, 11): {"type": "bike",  "label": "Zone 2 Steady", "dur_min": 60},
    date(2026, 8, 28): {"type": "bike",  "label": "Easy Spin",     "dur_min": 45},
    date(2026, 8, 30): {"type": "bike",  "label": "Recovery Spin", "dur_min": 60},
}

# ── Ghent–Amsterdam Event Prep ────────────────────────────────────────────────
# AI-designed periodised block: recovery → sweetspot build → back-to-back simulation → taper.
# Aug 31–Sep 3: recovery rides to absorb the Tenerife gains.
# Sep 5–9: event-specific quality (sweetspot, 2.5h + 1.5h back-to-back, tempo sharpener).
# Sep 11: short activation; Sep 12: full rest before the start.
EVENT_PREP_DAYS: list[dict] = [
    {"date": date(2026, 8, 31), "type": "bike",  "label": "Easy Spin",           "dur_min": 45},
    {"date": date(2026, 9,  1), "type": "bike",  "label": "Zone 2 Steady",       "dur_min": 90},
    {"date": date(2026, 9,  3), "type": "long",  "label": "Z2 Endurance",        "dur_min": 120},
    {"date": date(2026, 9,  5), "type": "tempo", "label": "Sweetspot Intervals", "dur_min": 90},
    {"date": date(2026, 9,  6), "type": "long",  "label": "Pre-Event Long Ride",  "dur_min": 270},
    {"date": date(2026, 9,  7), "type": "long",  "label": "Long Ride (Easy)",    "dur_min": 90},
    {"date": date(2026, 9,  8), "type": "bike",  "label": "Recovery Spin",       "dur_min": 45},
    {"date": date(2026, 9,  9), "type": "tempo", "label": "Tempo Intervals",     "dur_min": 75},
    {"date": date(2026, 9, 11), "type": "bike",  "label": "Easy Prep Ride",      "dur_min": 30},
]


def build_event_prep_weeks() -> list[dict]:
    """Two Mon–Sun rows covering Aug 31–Sep 13 (event prep + taper)."""
    today = date.today()
    days_by_date = {d["date"]: d for d in EVENT_PREP_DAYS}
    grid_start = date(2026, 8, 31)  # Monday
    weeks = []
    for wk in range(2):
        wk_start = grid_start + timedelta(weeks=wk)
        cells = []
        for day_off in range(7):
            d = wk_start + timedelta(days=day_off)
            day = days_by_date.get(d)
            if day:
                dur = day["dur_min"]
                if dur < 60:
                    dur_fmt = f"{dur}m"
                elif dur % 60:
                    dur_fmt = f"{dur // 60}h{dur % 60:02d}m"
                else:
                    dur_fmt = f"{dur // 60}h"
                cells.append({
                    "date": d, "day_num": d.day, "month_abbr": d.strftime("%b"),
                    "type": day["type"], "label": day["label"],
                    "dur_fmt": dur_fmt, "dur_min": dur,
                    "is_today": d == today, "is_past": d < today,
                })
            else:
                cells.append({
                    "date": d, "day_num": d.day, "month_abbr": d.strftime("%b"),
                    "type": "rest", "label": None, "dur_fmt": None, "dur_min": 0,
                    "is_today": d == today, "is_past": d < today,
                })
        weeks.append({"week_label": wk_start.strftime("%-d %b"), "days": cells})
    return weeks


# ── Ghent–Amsterdam Charity Ride ──────────────────────────────────────────────
CHARITY_DAYS: list[dict] = [
    {"day": 1, "date": date(2026, 9, 13), "label": "Ghent → Eindhoven", "km": 190},
    {"day": 2, "date": date(2026, 9, 14), "label": "Eindhoven → Amsterdam", "km": 120},
]


def build_charity_weeks() -> list[dict]:
    today = date.today()
    days_by_date = {d["date"]: d for d in CHARITY_DAYS}
    # Sep 13 = Sunday, Sep 14 = Monday → two rows: Sep 7–13, Sep 14–20
    grid_start = date(2026, 9, 7)
    weeks = []
    for wk in range(2):
        wk_start = grid_start + timedelta(weeks=wk)
        cells = []
        for day_off in range(7):
            d = wk_start + timedelta(days=day_off)
            ride_day = days_by_date.get(d)
            if ride_day:
                cells.append({
                    "date": d, "day_num": d.day, "month_abbr": d.strftime("%b"),
                    "label": ride_day["label"], "km": ride_day["km"],
                    "day_num_ride": ride_day["day"],
                    "is_today": d == today, "is_past": d < today, "is_event": True,
                })
            else:
                cells.append({
                    "date": d, "day_num": d.day, "month_abbr": d.strftime("%b"),
                    "is_event": False,
                })
        weeks.append({"week_label": wk_start.strftime("%-d %b"), "days": cells})
    return weeks
