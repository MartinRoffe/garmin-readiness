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
        ("rest",     "Rest",                  0),   # Thu 21 — KB moved to Sun
        ("rest",     "Rest",                  0),
        ("bike",     "Zone 2 Steady",        60),
        ("ruck",     "Ruck + KB",           105),   # Sun 24 — Ruck 8 kg then KB
    ],
    # WK 02
    [
        ("rest",     "Rest",                  0),   # Mon 25 — rest (recovery/sleep; long ride moved to Wed)
        ("strength", "KB + MaxiClimber",     45),
        ("long",     "Long Ride",            90),   # Wed 27 — moved from Mon
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
        ("ruck",     "Mersea Coastal Spur",  120),
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
        ("tempo",    "Hill Repeats",          60),
        ("ruck",     "Ruck  10 kg",          75),
        ("long",     "Long Ride",           135),
    ],
    # WK 06
    [
        ("rest",     "Rest",                  0),
        ("strength", "KB + MaxiClimber",     45),
        ("bike",     "Cadence Drills",       60),
        ("strength", "KB + MaxiClimber",     45),
        ("tempo",    "Hill Repeats",          60),
        ("ruck",     "Ruck  10–12 kg",       85),
        ("long",     "Long Ride",           160),   # raised from 140 to smooth the ramp into wk7 (was +29% to 180; now 135→160→180)
    ],
    # WK 07
    [
        ("rest",     "Rest",                  0),
        ("strength", "Light KB",             35),
        ("ftp",      "FTP Re-test",          60),
        ("bike",     "Z2 Ride",              60),   # freed from Easy MaxiClimber → 1 strength/week from here
        ("tempo",    "Tempo Intervals",      75),   # extended from 60m
        ("ruck",     "Ruck  12 kg",          95),
        ("long",     "Long Ride",           180),   # extended from 150m
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
        ("bike",     "Sweetspot Ride",       90),   # extended from Z2 60m, add structure
        ("tempo",    "Over-Unders",          75),   # freed from KB+MaxiClimber — threshold intervals
        ("bike",     "Z2 Endurance",         75),   # de-stacked from Tempo Intervals — avoid 3 consecutive intensity days
        ("ruck",     "Ruck  12–15 kg",      105),
        ("long",     "Long Ride",           210),   # extended from 165m
    ],
    # WK 10
    [
        ("rest",     "Rest",                  0),
        ("strength", "KB + MaxiClimber",     45),
        ("bike",     "Low Cadence Ride",     90),   # extended from 60m
        ("tempo",    "Threshold Ride",       90),   # freed from KB+MaxiClimber — sustained threshold
        ("bike",     "Z2 Endurance",         90),   # de-stacked from Tempo Intervals — avoid 3 consecutive intensity days
        ("ruck",     "Ruck  12–15 kg",      110),
        ("long",     "Long Ride",           255),   # extended from 180m
    ],
    # WK 11
    [
        ("bike",     "Recovery Spin",        60),   # back-to-back day 2 after Wk10 Sun 255m long ride
        ("strength", "KB + MaxiClimber",     45),
        ("bike",     "Z2 Endurance",         90),   # extended from 60m
        ("bike",     "Easy Ride",            45),   # freed from Light KB — 1 strength/week
        ("bike",     "Easy Prep Ride",       60),
        ("ruck",     "Easy Ruck  8 kg",      60),
        ("long",     "Long Ride",           300),   # extended from 210m — 5 hr event simulation
    ],
    # WK 12
    [
        ("bike",     "Recovery Spin",        60),   # event sim: day 2 tired legs after Wk11 Sun 300m ride
        ("strength", "Light KB",             30),
        ("ftp",      "Final FTP Test",       60),
        ("strength", "Easy MaxiClimber",     20),
        ("bike",     "Easy Spin",            45),
        ("ruck",     "Celebration Ruck",     60),
        ("long",     "Long Ride (Easy)",    120),
    ],
]

# Sessions whose label maps to multiple independent Garmin activities.
# Each sub-session specifies the Garmin type_key that marks it complete.
COMPOUND_SESSIONS: dict[str, list[dict]] = {
    "KB + MaxiClimber": [
        {"label": "Kettlebell",  "garmin_key": "strength_training"},
        {"label": "MaxiClimber", "garmin_key": "stair_climbing"},
    ],
    "Ruck + KB": [
        {"label": "Ruck",        "garmin_key": "rucking"},
        {"label": "Kettlebell",  "garmin_key": "strength_training"},
    ],
}

# MaxiClimber interval progression keyed by 1-based week number.
# Weeks not listed use the session as easy/recovery (deload or Easy MaxiClimber weeks).
# Norwegian 4×4 introduced in week 9 after the week-8 deload — body is fresh and
# has 4 weeks of interval base. 4-min intervals at 85–95% max HR with full 3-min recovery.
MAXI_INTERVALS: dict[int, dict] = {
    # KB + MaxiClimber — progressive intervals (kb: True)
    1:  {"sets": 10, "work_s": 150, "rest_s":  45, "kb": True},
    2:  {"sets": 10, "work_s": 150, "rest_s":  45, "kb": True},
    5:  {"sets": 10, "work_s": 180, "rest_s":  45, "kb": True},
    6:  {"sets":  8, "work_s": 210, "rest_s":  45, "kb": True},
    9:  {"sets":  4, "work_s": 240, "rest_s": 180, "kb": True, "norwegian": True},
    10: {"sets":  5, "work_s": 240, "rest_s": 180, "kb": True, "norwegian": True},
    11: {"sets":  4, "work_s": 240, "rest_s": 180, "kb": True, "norwegian": True},
    # Easy MaxiClimber — standalone, no KB, easy aerobic (kb: False, easy: True)
    3:  {"sets": 5, "work_s":  90, "rest_s": 60, "kb": False, "easy": True},   # 20m
    12: {"sets": 5, "work_s":  90, "rest_s": 60, "kb": False, "easy": True},   # 20m
    # Standalone MaxiClimber — deload weeks, no KB, steady aerobic (kb: False)
    4:  {"sets": 5, "work_s": 120, "rest_s": 60, "kb": False, "easy": True},   # 20m
    8:  {"sets": 5, "work_s": 120, "rest_s": 60, "kb": False, "easy": True},   # 20m
}

RUCK_SPECS: dict[int, dict] = {
    1:  {"weight_lo": 8,  "weight_hi": None, "ruck_min": 75, "note": "Compound — ruck first (~75m at 8 kg), then KB block at home"},
    2:  {"weight_lo": 8,  "weight_hi": 10,   "note": "Start at 8 kg; increase to 10 if the first half feels easy"},
    3:  {"weight_lo": 10, "weight_hi": None, "mersea_build": True, "note": "Mersea build — do the Coastal Spur (13 km, ~3.5 hr) at light load (10 kg) to begin building duration for the Sep 20 circuit. Keep effort easy — this is a distance day, not a load day."},
    4:  {"weight_lo": 8,  "weight_hi": None, "note": "Deload week — easy effort at 8 kg"},
    5:  {"weight_lo": 10, "weight_hi": None, "note": "Reload week — solid 10 kg effort"},
    6:  {"weight_lo": 10, "weight_hi": 12,   "note": "Push to 12 kg if last week felt manageable"},
    7:  {"weight_lo": 12, "weight_hi": None, "note": "Peak week — hold 12 kg for the full distance"},
    8:  {"weight_lo": 10, "weight_hi": None, "note": "Deload — step back to 10 kg, easy effort"},
    9:  {"weight_lo": 12, "weight_hi": 15,   "mersea_build": True, "note": "Mersea build — swap this ruck for the Eastern Arc (12 km, ~3 hr at easy load) to start building duration for the Sep 20 circuit. Or do a regular heavy ruck if you prefer — both work."},
    10: {"weight_lo": 12, "weight_hi": 15,   "mersea_build": True, "note": "Mersea build — repeat the Eastern Arc or step up to the Coastal Spur (13 km). Either way keep load light (~10 kg) so the focus is duration, not weight."},
    11: {"weight_lo": 8,  "weight_hi": None, "note": "Taper — 8 kg, focus on stride quality and pace"},
    12: {"weight_lo": 8,  "weight_hi": None, "note": "Celebration ruck — any comfortable load, enjoy it"},
}

KB_VIDEO_URLS: dict[str, str] = {
    "KB Deadlift":            "https://youtu.be/G8wX8wwDJsM?si=vsgSzurbJu91MrBo",
    "KB Swing (Two-Hand)":    "https://www.youtube.com/watch?v=1Qi0NQW89Oc",
    "KB Goblet Squat":        "https://www.youtube.com/watch?v=aNDUbH_Uv4g",
    "Single-Leg KB Deadlift": "https://www.youtube.com/watch?v=0P0t3h7Qbns",
    "KB Suitcase Carry":      "https://www.youtube.com/watch?v=SXf6ePnQMJ8",
    "KB Half-Kneeling Press": "https://www.youtube.com/watch?v=5cUBtrQ5LPQ",
    "KB Windmill":            "https://www.youtube.com/watch?v=1N1Qs9FO4GU",
    "Single-Arm Swing":       "https://www.youtube.com/watch?v=ANjKto7aSH0",
}

# Full KB circuit for KB+MaxiClimber sessions (done before the MaxiClimber block).
# Progression: add 1 rep per set each week; at the top end, increase weight and reset.
KB_FULL_SPECS: dict[int, dict] = {
    1: {
        "note": "Complete the KB block first, then move to MaxiClimber intervals.",
        "exercises": [
            {"id": "A1", "name": "KB Deadlift",            "sets_reps": "4 × 5",    "target": "Posterior chain"},
            {"id": "A2", "name": "KB Swing (Two-Hand)",    "sets_reps": "4 × 12",   "target": "Explosive hip extension"},
            {"id": "B1", "name": "KB Goblet Squat",        "sets_reps": "3 × 8",    "target": "Quad + hip mobility"},
            {"id": "B2", "name": "Single-Leg KB Deadlift", "sets_reps": "3 × 6/s",  "target": "Balance + hip stability"},
            {"id": "C1", "name": "KB Suitcase Carry",      "sets_reps": "3 × 20m/s","target": "Lateral core"},
            {"id": "C2", "name": "KB Half-Kneeling Press", "sets_reps": "3 × 6/s",  "target": "Anti-rotation + shoulder"},
            {"id": "D1", "name": "KB Windmill",            "sets_reps": "3 × 4/s",  "target": "Thoracic rotation"},
            {"id": "D2", "name": "Single-Arm Swing",       "sets_reps": "3 × 8/s",  "target": "Rotational stability"},
        ]
    },
    2: {
        "note": "Complete the KB block first, then move to MaxiClimber intervals.",
        "exercises": [
            {"id": "A1", "name": "KB Deadlift",            "sets_reps": "4 × 5",    "target": "Posterior chain"},
            {"id": "A2", "name": "KB Swing (Two-Hand)",    "sets_reps": "4 × 13",   "target": "Explosive hip extension"},
            {"id": "B1", "name": "KB Goblet Squat",        "sets_reps": "3 × 9",    "target": "Quad + hip mobility"},
            {"id": "B2", "name": "Single-Leg KB Deadlift", "sets_reps": "3 × 7/s",  "target": "Balance + hip stability"},
            {"id": "C1", "name": "KB Suitcase Carry",      "sets_reps": "3 × 20m/s","target": "Lateral core"},
            {"id": "C2", "name": "KB Half-Kneeling Press", "sets_reps": "3 × 7/s",  "target": "Anti-rotation + shoulder"},
            {"id": "D1", "name": "KB Windmill",            "sets_reps": "3 × 4/s",  "target": "Thoracic rotation"},
            {"id": "D2", "name": "Single-Arm Swing",       "sets_reps": "3 × 9/s",  "target": "Rotational stability"},
        ]
    },
    5: {
        "note": "Reset weight up, rebuild reps. KB block first, then MaxiClimber.",
        "exercises": [
            {"id": "A1", "name": "KB Deadlift",            "sets_reps": "4 × 5",    "target": "Posterior chain"},
            {"id": "A2", "name": "KB Swing (Two-Hand)",    "sets_reps": "4 × 15",   "target": "Explosive hip extension"},
            {"id": "B1", "name": "KB Goblet Squat",        "sets_reps": "3 × 10",   "target": "Quad + hip mobility"},
            {"id": "B2", "name": "Single-Leg KB Deadlift", "sets_reps": "3 × 8/s",  "target": "Balance + hip stability"},
            {"id": "C1", "name": "KB Suitcase Carry",      "sets_reps": "3 × 20m/s","target": "Lateral core"},
            {"id": "C2", "name": "KB Half-Kneeling Press", "sets_reps": "3 × 8/s",  "target": "Anti-rotation + shoulder"},
            {"id": "D1", "name": "KB Windmill",            "sets_reps": "3 × 5/s",  "target": "Thoracic rotation"},
            {"id": "D2", "name": "Single-Arm Swing",       "sets_reps": "3 × 10/s", "target": "Rotational stability"},
        ]
    },
    6: {
        "note": "Extend carry distance this week. KB block first, then MaxiClimber.",
        "exercises": [
            {"id": "A1", "name": "KB Deadlift",            "sets_reps": "4 × 5",    "target": "Posterior chain"},
            {"id": "A2", "name": "KB Swing (Two-Hand)",    "sets_reps": "4 × 15",   "target": "Explosive hip extension"},
            {"id": "B1", "name": "KB Goblet Squat",        "sets_reps": "3 × 12",   "target": "Quad + hip mobility"},
            {"id": "B2", "name": "Single-Leg KB Deadlift", "sets_reps": "3 × 8/s",  "target": "Balance + hip stability"},
            {"id": "C1", "name": "KB Suitcase Carry",      "sets_reps": "3 × 30m/s","target": "Lateral core"},
            {"id": "C2", "name": "KB Half-Kneeling Press", "sets_reps": "3 × 8/s",  "target": "Anti-rotation + shoulder"},
            {"id": "D1", "name": "KB Windmill",            "sets_reps": "3 × 5/s",  "target": "Thoracic rotation"},
            {"id": "D2", "name": "Single-Arm Swing",       "sets_reps": "3 × 10/s", "target": "Rotational stability"},
        ]
    },
    9: {
        "note": "Peak loading. KB block first, then Norwegian 4×4 on MaxiClimber.",
        "exercises": [
            {"id": "A1", "name": "KB Deadlift",            "sets_reps": "4 × 5",    "target": "Posterior chain"},
            {"id": "A2", "name": "KB Swing (Two-Hand)",    "sets_reps": "4 × 15",   "target": "Explosive hip extension"},
            {"id": "B1", "name": "KB Goblet Squat",        "sets_reps": "3 × 12",   "target": "Quad + hip mobility"},
            {"id": "B2", "name": "Single-Leg KB Deadlift", "sets_reps": "3 × 10/s", "target": "Balance + hip stability"},
            {"id": "C1", "name": "KB Suitcase Carry",      "sets_reps": "3 × 30m/s","target": "Lateral core"},
            {"id": "C2", "name": "KB Half-Kneeling Press", "sets_reps": "3 × 10/s", "target": "Anti-rotation + shoulder"},
            {"id": "D1", "name": "KB Windmill",            "sets_reps": "3 × 5/s",  "target": "Thoracic rotation"},
            {"id": "D2", "name": "Single-Arm Swing",       "sets_reps": "3 × 12/s", "target": "Rotational stability"},
        ]
    },
    10: {
        "note": "Match or exceed last week. KB block first, then Norwegian 4×4.",
        "exercises": [
            {"id": "A1", "name": "KB Deadlift",            "sets_reps": "4 × 5",    "target": "Posterior chain"},
            {"id": "A2", "name": "KB Swing (Two-Hand)",    "sets_reps": "4 × 15",   "target": "Explosive hip extension"},
            {"id": "B1", "name": "KB Goblet Squat",        "sets_reps": "3 × 12",   "target": "Quad + hip mobility"},
            {"id": "B2", "name": "Single-Leg KB Deadlift", "sets_reps": "3 × 10/s", "target": "Balance + hip stability"},
            {"id": "C1", "name": "KB Suitcase Carry",      "sets_reps": "3 × 30m/s","target": "Lateral core"},
            {"id": "C2", "name": "KB Half-Kneeling Press", "sets_reps": "3 × 10/s", "target": "Anti-rotation + shoulder"},
            {"id": "D1", "name": "KB Windmill",            "sets_reps": "3 × 5/s",  "target": "Thoracic rotation"},
            {"id": "D2", "name": "Single-Arm Swing",       "sets_reps": "3 × 12/s", "target": "Rotational stability"},
        ]
    },
    11: {
        "note": "Taper — A+B+C only before the Norwegian 4×4.",
        "exercises": [
            {"id": "A1", "name": "KB Deadlift",            "sets_reps": "3 × 5",    "target": "Posterior chain"},
            {"id": "A2", "name": "KB Swing (Two-Hand)",    "sets_reps": "3 × 12",   "target": "Explosive hip extension"},
            {"id": "B1", "name": "KB Goblet Squat",        "sets_reps": "3 × 10",   "target": "Quad + hip mobility"},
            {"id": "B2", "name": "Single-Leg KB Deadlift", "sets_reps": "3 × 8/s",  "target": "Balance + hip stability"},
            {"id": "C1", "name": "KB Suitcase Carry",      "sets_reps": "2 × 20m/s","target": "Lateral core"},
        ]
    },
}

# Abbreviated KB circuit for Light KB / post-ruck sessions.
KB_SPECS: dict[int, dict] = {
    1: {
        "note": "After the ruck — abbreviated. Hip hinge quality over volume.",
        "exercises": [
            {"id": "A1", "name": "KB Deadlift",         "sets_reps": "3 × 5",  "target": "Posterior chain"},
            {"id": "A2", "name": "KB Swing (Two-Hand)", "sets_reps": "3 × 10", "target": "Explosive hip extension"},
            {"id": "B1", "name": "KB Goblet Squat",     "sets_reps": "2 × 8",  "target": "Quad + hip mobility"},
        ]
    },
    3: {
        "note": "FTP test week — light load, perfect form. A+B blocks only.",
        "exercises": [
            {"id": "A1", "name": "KB Deadlift",            "sets_reps": "3 × 5",  "target": "Posterior chain"},
            {"id": "A2", "name": "KB Swing (Two-Hand)",    "sets_reps": "3 × 10", "target": "Explosive hip extension"},
            {"id": "B1", "name": "KB Goblet Squat",        "sets_reps": "2 × 8",  "target": "Quad + hip mobility"},
            {"id": "B2", "name": "Single-Leg KB Deadlift", "sets_reps": "2 × 6/s","target": "Balance + hip stability"},
        ]
    },
    4: {
        "note": "Deload week — minimal volume. Move well, flush the legs.",
        "exercises": [
            {"id": "A1", "name": "KB Deadlift",         "sets_reps": "2 × 5",  "target": "Posterior chain"},
            {"id": "A2", "name": "KB Swing (Two-Hand)", "sets_reps": "2 × 10", "target": "Explosive hip extension"},
            {"id": "B1", "name": "KB Goblet Squat",     "sets_reps": "2 × 8",  "target": "Quad + mobility"},
        ]
    },
    7: {
        "note": "FTP retest week — A+B blocks, moderate effort.",
        "exercises": [
            {"id": "A1", "name": "KB Deadlift",            "sets_reps": "3 × 5",  "target": "Posterior chain"},
            {"id": "A2", "name": "KB Swing (Two-Hand)",    "sets_reps": "3 × 12", "target": "Explosive hip extension"},
            {"id": "B1", "name": "KB Goblet Squat",        "sets_reps": "2 × 10", "target": "Quad + hip mobility"},
            {"id": "B2", "name": "Single-Leg KB Deadlift", "sets_reps": "2 × 8/s","target": "Balance + hip stability"},
        ]
    },
    8: {
        "note": "Deload week — minimal volume. Active recovery.",
        "exercises": [
            {"id": "A1", "name": "KB Deadlift",         "sets_reps": "2 × 5",  "target": "Posterior chain"},
            {"id": "A2", "name": "KB Swing (Two-Hand)", "sets_reps": "2 × 10", "target": "Explosive hip extension"},
            {"id": "B1", "name": "KB Goblet Squat",     "sets_reps": "2 × 8",  "target": "Quad + mobility"},
        ]
    },
    11: {
        "note": "Taper — reduce volume, maintain intensity. A+B+C blocks.",
        "exercises": [
            {"id": "A1", "name": "KB Deadlift",            "sets_reps": "3 × 5",   "target": "Posterior chain"},
            {"id": "A2", "name": "KB Swing (Two-Hand)",    "sets_reps": "3 × 12",  "target": "Explosive hip extension"},
            {"id": "B1", "name": "KB Goblet Squat",        "sets_reps": "3 × 10",  "target": "Quad + hip mobility"},
            {"id": "B2", "name": "Single-Leg KB Deadlift", "sets_reps": "3 × 8/s", "target": "Balance + hip stability"},
            {"id": "C1", "name": "KB Suitcase Carry",      "sets_reps": "2 × 20m/s","target": "Lateral core"},
        ]
    },
    12: {
        "note": "Final strength session — stay fresh. Just the essentials.",
        "exercises": [
            {"id": "A1", "name": "KB Deadlift",         "sets_reps": "2 × 5",  "target": "Posterior chain"},
            {"id": "A2", "name": "KB Swing (Two-Hand)", "sets_reps": "2 × 10", "target": "Explosive hip extension"},
            {"id": "B1", "name": "KB Goblet Squat",     "sets_reps": "2 × 8",  "target": "Quad + mobility"},
        ]
    },
}

_PLAN_DAYS = len(TRAINING_WEEKS) * 7  # 84

# AI coach recommendations keyed by ISO date; surfaced on the calendar card + modal.
COACH_NOTES: dict[str, str] = {}


def session_for_date(d: date) -> tuple[str, str, int] | None:
    """Return (type, label, duration_min) for the given date, or None if outside the plan."""
    delta = (d - PLAN_START).days
    if delta < 0 or delta >= _PLAN_DAYS:
        return None
    from .history import get_plan_override
    ov = get_plan_override(d.isoformat())
    if ov:
        return (ov["session_type"], ov["label"], ov["duration_min"])
    week_idx, day_idx = divmod(delta, 7)
    return TRAINING_WEEKS[week_idx][day_idx]


def session_for_date_extended(d: date) -> tuple[str, str, int] | None:
    """Like session_for_date but also covers camp buffer days, Tenerife, event prep and charity ride."""
    result = session_for_date(d)
    if result is not None:
        return result

    # Camp buffer days (pre/post Tenerife)
    if d in CAMP_GRID_WORKOUTS:
        s = CAMP_GRID_WORKOUTS[d]
        return (s["type"], s["label"], s["dur_min"])

    # Tenerife cycling camp
    for day in TENERIFE_DAYS:
        if day["date"] == d:
            if day["intensity"] in ("travel", "rest"):
                return ("rest", day["label"], 0)
            km = day.get("km", 0)
            elev = day.get("elev_m", 0)
            label = f"{day['label']} — {km}km, {elev}m elev"
            return ("bike", label, 0)

    # Event prep days
    for ep in EVENT_PREP_DAYS:
        if ep["date"] == d:
            return (ep["type"], ep["label"], ep["dur_min"])

    # Charity ride days
    for cr in CHARITY_DAYS:
        if cr["date"] == d:
            return ("bike", f"{cr['label']} ({cr['km']}km charity ride)", 0)

    return None


def _enrich_kb_spec(spec: dict | None) -> dict | None:
    if not spec:
        return None
    return {
        **spec,
        "exercises": [
            {**ex, "video_url": KB_VIDEO_URLS.get(ex["name"])}
            for ex in spec["exercises"]
        ],
    }


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
            compound = COMPOUND_SESSIONS.get(label)
            # Day-level specs for non-compound sessions
            maxi_intervals = None
            ruck_spec = None
            kb_spec = None
            if stype == "ruck":
                ruck_spec = RUCK_SPECS.get(wk_idx + 1)
                if not compound:
                    kb_spec = _enrich_kb_spec(KB_SPECS.get(wk_idx + 1))
            elif stype == "strength" and "MaxiClimber" in label:
                maxi_intervals = MAXI_INTERVALS.get(wk_idx + 1)
                if not compound:
                    kb_spec = _enrich_kb_spec(KB_FULL_SPECS.get(wk_idx + 1))
            elif stype == "strength":
                kb_spec = _enrich_kb_spec(KB_SPECS.get(wk_idx + 1))
            # Build sub-sessions with per-sub modal data for compound sessions
            if compound:
                sub_sessions = []
                for s in compound:
                    sub: dict = {
                        "label": s["label"],
                        "garmin_key": s["garmin_key"],
                        "completed": None,
                        "actual_min": None,
                        "maxi_intervals": None,
                        "kb_spec": None,
                        "ruck_spec": None,
                    }
                    if s["label"] == "MaxiClimber":
                        mi = MAXI_INTERVALS.get(wk_idx + 1)
                        sub["maxi_intervals"] = ({**mi, "kb": False} if mi else None)
                    elif s["label"] == "Kettlebell" and "MaxiClimber" in label:
                        sub["kb_spec"] = _enrich_kb_spec(KB_FULL_SPECS.get(wk_idx + 1))
                    elif s["label"] == "Kettlebell":
                        sub["kb_spec"] = _enrich_kb_spec(KB_SPECS.get(wk_idx + 1))
                    elif s["label"] == "Ruck":
                        sub["ruck_spec"] = RUCK_SPECS.get(wk_idx + 1)
                    sub_sessions.append(sub)
            else:
                sub_sessions = None
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
                "sub_sessions": sub_sessions,
                "maxi_intervals": maxi_intervals,
                "ruck_spec": ruck_spec,
                "kb_spec": kb_spec,
                "mersea_build": bool(ruck_spec and ruck_spec.get("mersea_build")),
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


MERSEA_EVENT_DAYS: list[dict] = [
    {"date": date(2026, 9, 20), "label": "Round Mersea", "km": 22},
]

# ── Ghent–Amsterdam Charity Ride ──────────────────────────────────────────────
CHARITY_DAYS: list[dict] = [
    {"day": 1, "date": date(2026, 9, 13), "label": "Ghent → Eindhoven", "km": 190},
    {"day": 2, "date": date(2026, 9, 14), "label": "Eindhoven → Amsterdam", "km": 120},
]


def build_combined_event_weeks() -> list[dict]:
    """Three Mon–Sun rows (Aug 31–Sep 20) merging event-prep sessions, charity ride days, and Mersea goal."""
    today = date.today()
    prep_by_date = {d["date"]: d for d in EVENT_PREP_DAYS}
    charity_by_date = {d["date"]: d for d in CHARITY_DAYS}
    mersea_by_date = {d["date"]: d for d in MERSEA_EVENT_DAYS}
    grid_start = date(2026, 8, 31)
    weeks = []
    for wk in range(3):
        wk_start = grid_start + timedelta(weeks=wk)
        cells = []
        for day_off in range(7):
            d = wk_start + timedelta(days=day_off)
            charity = charity_by_date.get(d)
            mersea = mersea_by_date.get(d)
            prep = prep_by_date.get(d)
            if charity:
                cells.append({
                    "date": d, "day_num": d.day, "month_abbr": d.strftime("%b"),
                    "is_event": True, "is_mersea": False,
                    "label": charity["label"], "km": charity["km"],
                    "day_num_ride": charity["day"],
                    "is_today": d == today, "is_past": d < today,
                    "type": "charity",
                })
            elif mersea:
                cells.append({
                    "date": d, "day_num": d.day, "month_abbr": d.strftime("%b"),
                    "is_event": True, "is_mersea": True,
                    "label": mersea["label"], "km": mersea["km"],
                    "is_today": d == today, "is_past": d < today,
                    "type": "mersea",
                })
            elif prep:
                dur = prep["dur_min"]
                if dur < 60:
                    dur_fmt = f"{dur}m"
                elif dur % 60:
                    dur_fmt = f"{dur // 60}h{dur % 60:02d}m"
                else:
                    dur_fmt = f"{dur // 60}h"
                cells.append({
                    "date": d, "day_num": d.day, "month_abbr": d.strftime("%b"),
                    "is_event": False,
                    "type": prep["type"], "label": prep["label"],
                    "dur_fmt": dur_fmt, "dur_min": dur,
                    "is_today": d == today, "is_past": d < today,
                })
            else:
                cells.append({
                    "date": d, "day_num": d.day, "month_abbr": d.strftime("%b"),
                    "is_event": False,
                    "type": "rest", "label": None, "dur_fmt": None, "dur_min": 0,
                    "is_today": d == today, "is_past": d < today,
                })
        weeks.append({"week_label": wk_start.strftime("%-d %b"), "days": cells})
    return weeks


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
