"""Haute Route Alpes 2027 — 10-month training plan (Oct 2026 → Aug 2027).

Plan start: Mon 5 Oct 2026 (3 weeks post charity ride)
Event:      Mon 23 Aug – Sun 29 Aug 2027 (7 stages, 808 km, 19,255 m)

Phase structure:
  Phase 1 — Base          Wks  1–13   Oct  5 → Jan  3  (aerobic engine + gym)
  Phase 2 — Build         Wks 14–25   Jan  4 → Mar 28  (FTP + VO2 + TTE)
  Phase 3 — Specific      Wks 26–35   Mar 29 → Jun  7  (back-to-back + camp)
  Phase 4 — Peak          Wks 36–43   Jun  8 → Aug  2  (multi-day simulation)
  Phase 5 — Taper         Wks 44–46   Aug  3 → Aug 22  (arrive fresh)
  Event                              Aug 23 – Aug 29
"""
from __future__ import annotations

from datetime import date, timedelta

HR_PLAN_START = date(2026, 10, 5)
assert HR_PLAN_START.weekday() == 0, "Plan must start on Monday"

HR_EVENT_START = date(2027, 8, 23)
HR_EVENT_END   = date(2027, 8, 29)

# Phase metadata — week_start/week_end are 1-indexed, inclusive
HR_PHASES: list[dict] = [
    {"name": "base",     "label": "Base",          "colour": "emerald", "week_start":  1, "week_end": 13},
    {"name": "build",    "label": "Build",          "colour": "blue",    "week_start": 14, "week_end": 25},
    {"name": "specific", "label": "Specific Build", "colour": "violet",  "week_start": 26, "week_end": 35},
    {"name": "peak",     "label": "Peak",           "colour": "orange",  "week_start": 36, "week_end": 43},
    {"name": "taper",    "label": "Taper",          "colour": "rose",    "week_start": 44, "week_end": 46},
]

# Session types used in this plan:
#   rest | endurance | sweetspot | tempo | vo2 | recovery | long | ftp | gym | back_to_back
#
# Each week: list of 7 sessions Mon–Sun, each (type, label, duration_min)

HR_TRAINING_WEEKS: list[list[tuple[str, str, int]]] = [

    # ── PHASE 1: BASE ──────────────────────────────────────────────────────────
    # Goal: aerobic engine, structural durability, gym strength. 7–11 hrs/wk.
    # 3:1 build:deload cadence. FTP baseline in wk 8.

    # WK 01 — Transition · 7 hrs (Oct 5)01
    [
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Endurance",           60),
        ("sweetspot", "Low Cadence Sweetspot",  60),
        ("recovery",  "Recovery + Core",        45),
        ("endurance", "Z2 Steady",              60),
        ("gym",       "Gym — Strength",         60),
        ("long",      "Long Ride",              90),
    ],
    # WK 02 — Base build · 8 hrs (Oct 12)02
    [
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Endurance",           60),
        ("sweetspot", "Low Cadence Sweetspot",  60),
        ("recovery",  "Recovery + Core",        45),
        ("tempo",     "Tempo Intervals 2×20",   75),
        ("gym",       "Gym — Strength",         60),
        ("long",      "Long Ride",             120),
    ],
    # WK 03 — Base build · 9 hrs (Oct 19)03
    [
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Endurance",           60),
        ("sweetspot", "Low Cadence Sweetspot",  60),
        ("recovery",  "Recovery + Core",        45),
        ("tempo",     "Tempo Intervals 2×20",   75),
        ("gym",       "Gym — Strength",         60),
        ("long",      "Long Ride",             150),
    ],
    # WK 04 — Deload · 6 hrs (Oct 26)04
    [
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Easy",                45),
        ("recovery",  "Recovery Spin",          45),
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Easy",                45),
        ("gym",       "Gym — Maintenance",      45),
        ("long",      "Long Ride (Easy)",       75),
    ],
    # WK 05 — Base build · 9 hrs (Nov 2)05
    [
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Endurance",           60),
        ("sweetspot", "Low Cadence Sweetspot",  60),
        ("recovery",  "Recovery + Core",        45),
        ("tempo",     "Tempo Intervals 2×20",   90),
        ("gym",       "Gym — Strength",         60),
        ("long",      "Long Ride",             150),
    ],
    # WK 06 — Base build · 10 hrs (Nov 9)06
    [
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Endurance",           60),
        ("sweetspot", "Low Cadence Sweetspot",  75),
        ("recovery",  "Recovery + Core",        45),
        ("tempo",     "Tempo Intervals 2×20",   90),
        ("gym",       "Gym — Strength",         60),
        ("long",      "Long Ride",             180),
    ],
    # WK 07 — Base build · 10 hrs (Nov 16)07
    [
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Endurance",           60),
        ("sweetspot", "Low Cadence Sweetspot",  75),
        ("recovery",  "Recovery + Core",        45),
        ("tempo",     "Tempo Intervals 3×15",   90),
        ("gym",       "Gym — Strength",         60),
        ("long",      "Long Ride",             180),
    ],
    # WK 08 — Deload + FTP Baseline · 7 hrs (Nov 23)08
    [
        ("rest",      "Rest",                    0),
        ("ftp",       "FTP Baseline Test",      60),
        ("recovery",  "Recovery Spin",          45),
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Easy",                45),
        ("gym",       "Gym — Maintenance",      45),
        ("long",      "Long Ride (Easy)",       90),
    ],
    # WK 09 — Base build · 10 hrs (Nov 30)09
    [
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Endurance",           60),
        ("sweetspot", "Low Cadence Sweetspot",  75),
        ("recovery",  "Recovery + Core",        45),
        ("tempo",     "Tempo Intervals 3×20",  105),
        ("gym",       "Gym — Strength",         60),
        ("long",      "Long Ride",             210),
    ],
    # WK 10 — Base build · 11 hrs (Dec 7)10
    [
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Endurance",           60),
        ("sweetspot", "Low Cadence Sweetspot",  75),
        ("recovery",  "Recovery + Core",        45),
        ("tempo",     "Tempo Intervals 3×20",  105),
        ("gym",       "Gym — Strength",         60),
        ("long",      "Long Ride",             240),
    ],
    # WK 11 — Base peak · 11 hrs (Dec 14)11
    [
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Endurance",           60),
        ("sweetspot", "Low Cadence Sweetspot",  75),
        ("recovery",  "Recovery + Core",        45),
        ("tempo",     "Tempo Intervals 3×20",  105),
        ("gym",       "Gym — Strength",         60),
        ("long",      "Long Ride",             270),
    ],
    # WK 12 — Christmas deload · 6 hrs (Dec 21)12
    [
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Easy",                45),
        ("recovery",  "Recovery Spin",          45),
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Easy",                45),
        ("gym",       "Gym — Maintenance",      45),
        ("long",      "Long Ride (Easy)",       90),
    ],
    # WK 13 — New Year transition · 8 hrs (Dec 28)13
    [
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Endurance",           60),
        ("sweetspot", "Low Cadence Sweetspot",  60),
        ("recovery",  "Recovery + Core",        45),
        ("tempo",     "Tempo Intervals 2×20",   90),
        ("gym",       "Gym — Strength",         60),
        ("long",      "Long Ride",             150),
    ],

    # ── PHASE 2: BUILD ─────────────────────────────────────────────────────────
    # Goal: raise FTP, extend TTE, VO2 max development. 10–14 hrs/wk.
    # VO2 replaces easy Z2 on Tuesdays. Sundays extend to 5+ hrs by end of phase.

    # WK 14 — Build entry · 10 hrs (Jan 4)14
    [
        ("rest",      "Rest",                    0),
        ("vo2",       "VO2 Intervals 4×3 min",  60),
        ("sweetspot", "Low Cadence Sweetspot",  75),
        ("recovery",  "Strength + Core",        60),
        ("tempo",     "Tempo Intervals 2×20",   90),
        ("endurance", "Z2 Endurance",           60),
        ("long",      "Long Ride",             180),
    ],
    # WK 15 — Build · 11 hrs (Jan 11)15
    [
        ("rest",      "Rest",                    0),
        ("vo2",       "VO2 Intervals 5×3 min",  60),
        ("sweetspot", "Low Cadence Sweetspot",  75),
        ("recovery",  "Strength + Core",        60),
        ("tempo",     "Tempo Intervals 2×20",   90),
        ("endurance", "Z2 Endurance",           60),
        ("long",      "Long Ride",             210),
    ],
    # WK 16 — Absorption · ~8.5 hrs (Jan 18)16
    # Was a 3rd consecutive build week; softened to a reduced-load absorption week
    # to give a masters-friendly 2:1 load:recovery rhythm (VO2 dropped to Z2, long
    # ride and tempo trimmed). Lowers projected CTL here by design.
    [
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Endurance",           60),
        ("sweetspot", "Low Cadence Sweetspot",  60),
        ("recovery",  "Strength + Core",        60),
        ("tempo",     "Tempo Intervals 2×15",   75),
        ("endurance", "Z2 Easy",                45),
        ("long",      "Long Ride",             180),
    ],
    # WK 17 — Deload · 7 hrs (Jan 25)17
    [
        ("rest",      "Rest",                    0),
        ("vo2",       "VO2 Intervals 4×3 min",  60),
        ("recovery",  "Recovery Spin",          45),
        ("rest",      "Rest",                    0),
        ("tempo",     "Tempo Intervals 2×15",   75),
        ("endurance", "Z2 Easy",                45),
        ("long",      "Long Ride (Easy)",       90),
    ],
    # WK 18 — Build · 12 hrs (Feb 1)18
    [
        ("rest",      "Rest",                    0),
        ("vo2",       "VO2 Intervals 6×3 min",  60),
        ("sweetspot", "Low Cadence Sweetspot",  75),
        ("recovery",  "Strength + Core",        60),
        ("tempo",     "Tempo Intervals 3×20",  105),
        ("endurance", "Z2 Endurance",           75),
        ("long",      "Long Ride",             270),
    ],
    # WK 19 — Build · 12 hrs (Feb 8)19
    [
        ("rest",      "Rest",                    0),
        ("vo2",       "VO2 Intervals 6×3 min",  60),
        ("sweetspot", "Low Cadence Sweetspot",  90),
        ("recovery",  "Strength + Core",        60),
        ("tempo",     "Tempo Intervals 3×20",  105),
        ("endurance", "Z2 Endurance",           75),
        ("long",      "Long Ride",             270),
    ],
    # WK 20 — Absorption · ~9 hrs (Feb 15)20
    # 3rd consecutive build week softened to an absorption week (2:1 rhythm).
    [
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Endurance",           60),
        ("sweetspot", "Low Cadence Sweetspot",  60),
        ("recovery",  "Strength + Core",        60),
        ("tempo",     "Tempo Intervals 2×15",   75),
        ("endurance", "Z2 Easy",                45),
        ("long",      "Long Ride",             210),
    ],
    # WK 21 — Deload + FTP Re-test · 8 hrs (Feb 22)21
    [
        ("rest",      "Rest",                    0),
        ("ftp",       "FTP Re-test",            60),
        ("recovery",  "Recovery Spin",          45),
        ("rest",      "Rest",                    0),
        ("tempo",     "Tempo Intervals 2×15",   75),
        ("endurance", "Z2 Easy",                60),
        ("long",      "Long Ride (Easy)",      105),
    ],
    # WK 22 — Build · 13 hrs (Mar 1)22
    [
        ("rest",      "Rest",                    0),
        ("vo2",       "VO2 Intervals 5×4 min",  75),
        ("sweetspot", "Low Cadence Sweetspot",  90),
        ("recovery",  "Strength + Core",        60),
        ("tempo",     "Under-Overs 3×10 min",  105),
        ("endurance", "Z2 Endurance",           90),
        ("long",      "Long Ride",             300),
    ],
    # WK 23 — Build · 13 hrs (Mar 8)23
    [
        ("rest",      "Rest",                    0),
        ("vo2",       "VO2 Intervals 5×4 min",  75),
        ("sweetspot", "Low Cadence Sweetspot",  90),
        ("recovery",  "Strength + Core",        60),
        ("tempo",     "Under-Overs 3×10 min",  105),
        ("endurance", "Z2 Endurance",           90),
        ("long",      "Long Ride",             330),
    ],
    # WK 24 — Build peak · 14 hrs (Mar 15)24
    [
        ("rest",      "Rest",                    0),
        ("vo2",       "VO2 Intervals 5×4 min",  75),
        ("sweetspot", "Low Cadence Sweetspot",  90),
        ("recovery",  "Strength + Core",        60),
        ("tempo",     "Under-Overs 3×10 min",  105),
        ("endurance", "Z2 Endurance",           90),
        ("long",      "Long Ride",             360),
    ],
    # WK 25 — Deload · 8 hrs (Mar 22)25
    [
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Easy",                60),
        ("recovery",  "Recovery Spin",          45),
        ("rest",      "Rest",                    0),
        ("tempo",     "Tempo Intervals 2×15",   75),
        ("endurance", "Z2 Endurance",           60),
        ("long",      "Long Ride (Easy)",      120),
    ],

    # ── PHASE 3: SPECIFIC BUILD ────────────────────────────────────────────────
    # Goal: multi-day fatigue resistance, climbing volume, back-to-back days.
    # 5-day mountain training camp in wk 31. 14–16 hrs/wk at peak.

    # WK 26 — Specific entry · 14 hrs (Mar 29)26
    [
        ("rest",         "Rest",                    0),
        ("vo2",          "VO2 Intervals 5×4 min",  75),
        ("sweetspot",    "Low Cadence Sweetspot",  90),
        ("recovery",     "Strength + Core",        60),
        ("tempo",        "Under-Overs 3×10 min",  105),
        ("back_to_back", "Back-to-Back Day 1",    210),
        ("back_to_back", "Back-to-Back Day 2",    150),
    ],
    # WK 27 — Specific · 15 hrs (Apr 5)27
    [
        ("rest",         "Rest",                    0),
        ("vo2",          "VO2 Intervals 5×4 min",  75),
        ("sweetspot",    "Low Cadence Sweetspot",  90),
        ("recovery",     "Strength + Core",        60),
        ("tempo",        "Under-Overs 3×10 min",  105),
        ("back_to_back", "Back-to-Back Day 1",    240),
        ("back_to_back", "Back-to-Back Day 2",    180),
    ],
    # WK 28 — Absorption · ~9.5 hrs (Apr 12)28
    # 3rd consecutive specific-build week softened to an absorption week (2:1).
    # Keeps a SHORTER back-to-back to retain multi-day specificity, drops the
    # VO2 + under-overs to Z2, trims volume.
    [
        ("rest",         "Rest",                    0),
        ("endurance",    "Z2 Endurance",           60),
        ("sweetspot",    "Low Cadence Sweetspot",  75),
        ("recovery",     "Strength + Core",        60),
        ("endurance",    "Z2 Endurance",           75),
        ("back_to_back", "Back-to-Back Day 1 (Easy)", 180),
        ("back_to_back", "Back-to-Back Day 2 (Easy)", 120),
    ],
    # WK 29 — Deload · 9 hrs (Apr 19)29
    [
        ("rest",      "Rest",                    0),
        ("vo2",       "VO2 Intervals 4×3 min",  60),
        ("recovery",  "Recovery Spin",          45),
        ("rest",      "Rest",                    0),
        ("tempo",     "Tempo Intervals 2×20",   90),
        ("endurance", "Z2 Endurance",           75),
        ("long",      "Long Ride (Easy)",      150),
    ],
    # WK 30 — Specific build · 16 hrs (Apr 26)30
    [
        ("rest",         "Rest",                    0),
        ("vo2",          "VO2 Intervals 5×4 min",  75),
        ("sweetspot",    "Low Cadence Sweetspot",  90),
        ("recovery",     "Strength + Core",        60),
        ("tempo",        "Under-Overs 3×12 min",  105),
        ("back_to_back", "Back-to-Back Day 1",    300),
        ("back_to_back", "Back-to-Back Day 2",    210),
    ],
    # WK 31 — MOUNTAIN TRAINING CAMP · Pyrenees / Alps (May 3)31
    # 5 consecutive riding days; indicative sessions, adapt on the ground
    [
        ("long",     "Camp — Arrival + Leg Openers",    180),
        ("long",     "Camp — Mountain Stage",           330),
        ("long",     "Camp — Summit Day",               360),
        ("recovery", "Camp — Active Recovery",          120),
        ("long",     "Camp — Back-to-Back Day 1",       360),
        ("long",     "Camp — Back-to-Back Day 2",       300),
        ("rest",     "Camp — Rest / Travel Home",         0),
    ],
    # WK 32 — Post-camp recovery + FTP Re-test · 9 hrs (May 10)32
    [
        ("rest",      "Rest",                    0),
        ("recovery",  "Recovery Spin",          60),
        ("ftp",       "FTP Re-test",            60),
        ("recovery",  "Recovery + Core",        60),
        ("endurance", "Z2 Endurance",           60),
        ("endurance", "Z2 Endurance",           75),
        ("long",      "Long Ride (Easy)",      150),
    ],
    # WK 33 — Specific · 16 hrs (May 17)33
    [
        ("rest",         "Rest",                    0),
        ("vo2",          "VO2 Intervals 5×4 min",  75),
        ("sweetspot",    "Low Cadence Sweetspot",  90),
        ("recovery",     "Strength + Core",        60),
        ("tempo",        "Under-Overs 3×12 min",  105),
        ("back_to_back", "Back-to-Back Day 1",    300),
        ("back_to_back", "Back-to-Back Day 2",    240),
    ],
    # WK 34 — Specific peak · 16 hrs (May 24)34
    [
        ("rest",         "Rest",                    0),
        ("vo2",          "VO2 Intervals 5×4 min",  75),
        ("sweetspot",    "Low Cadence Sweetspot",  90),
        ("recovery",     "Strength + Core",        60),
        ("tempo",        "Under-Overs 3×12 min",  105),
        ("back_to_back", "Back-to-Back Day 1",    330),
        ("back_to_back", "Back-to-Back Day 2",    240),
    ],
    # WK 35 — Deload · 9 hrs (May 31)35
    [
        ("rest",      "Rest",                    0),
        ("vo2",       "VO2 Intervals 4×3 min",  60),
        ("recovery",  "Recovery Spin",          45),
        ("rest",      "Rest",                    0),
        ("tempo",     "Tempo Intervals 2×20",   90),
        ("endurance", "Z2 Endurance",           90),
        ("long",      "Long Ride (Easy)",      165),
    ],

    # ── PHASE 4: PEAK ──────────────────────────────────────────────────────────
    # Goal: event simulation, peak durability. Two 3-day simulation blocks.
    # 14–17 hrs/wk. Gym dropped to maintenance only.

    # WK 36 — Peak entry · 15 hrs (Jun 7)36
    [
        ("rest",         "Rest",                    0),
        ("tempo",        "Under-Overs 3×12 min",   90),
        ("sweetspot",    "Low Cadence Sweetspot",  75),
        ("recovery",     "Strength + Core",        60),
        ("endurance",    "Z2 Endurance",           90),
        ("back_to_back", "Simulation Day 1",      330),
        ("back_to_back", "Simulation Day 2",      270),
    ],
    # WK 37 — Peak · 17 hrs — first 3-day block (Jun 14)37
    [
        ("rest",         "Rest",                    0),
        ("tempo",        "Under-Overs 3×12 min",   90),
        ("sweetspot",    "Low Cadence Sweetspot",  75),
        ("recovery",     "Strength + Core",        60),
        ("back_to_back", "Simulation Day 1",      330),
        ("back_to_back", "Simulation Day 2",      300),
        ("back_to_back", "Simulation Day 3",      210),
    ],
    # WK 38 — Deload · 8 hrs (Jun 21)38
    [
        ("rest",      "Rest",                    0),
        ("recovery",  "Recovery Spin",          60),
        ("endurance", "Z2 Endurance",           60),
        ("rest",      "Rest",                    0),
        ("tempo",     "Tempo Intervals 2×20",   90),
        ("endurance", "Z2 Endurance",           75),
        ("long",      "Long Ride (Easy)",      120),
    ],
    # WK 39 — Peak · 17 hrs — second 3-day block (Jun 28)39
    [
        ("rest",         "Rest",                    0),
        ("tempo",        "Under-Overs 3×12 min",   90),
        ("sweetspot",    "Low Cadence Sweetspot",  75),
        ("recovery",     "Strength + Core",        60),
        ("back_to_back", "Simulation Day 1",      360),
        ("back_to_back", "Simulation Day 2",      330),
        ("back_to_back", "Simulation Day 3",      240),
    ],
    # WK 40 — Absorption · ~11 hrs (Jul 5)40
    # Breaks up a 4-week unbroken peak stretch (wks 39–42, deloads only at 38/43):
    # softened to an absorption week so there is real recovery BETWEEN the two
    # 3-day simulation blocks (wk39 and wk42). Masters athletes can't absorb four
    # consecutive peak weeks. Intensity dropped to Z2, sim block shortened.
    [
        ("rest",         "Rest",                    0),
        ("recovery",     "Recovery Spin",          60),
        ("endurance",    "Z2 Endurance",           75),
        ("recovery",     "Strength + Core",        60),
        ("endurance",    "Z2 Endurance",           90),
        ("long",         "Long Ride",             210),
        ("long",         "Long Ride (Easy)",      150),
    ],
    # WK 41 — Peak + FTP Final Test · 13 hrs (Jul 12)41
    [
        ("rest",      "Rest",                    0),
        ("ftp",       "FTP Final Test",         60),
        ("sweetspot", "Low Cadence Sweetspot",  75),
        ("recovery",  "Recovery + Core",        60),
        ("tempo",     "Under-Overs 3×10 min",   90),
        ("endurance", "Z2 Endurance",           90),
        ("long",      "Long Ride",             300),
    ],
    # WK 42 — Peak final block · 16 hrs (Jul 19)42
    [
        ("rest",         "Rest",                    0),
        ("tempo",        "Under-Overs 3×12 min",   90),
        ("sweetspot",    "Low Cadence Sweetspot",  75),
        ("recovery",     "Strength + Core",        60),
        ("back_to_back", "Simulation Day 1",      330),
        ("back_to_back", "Simulation Day 2",      300),
        ("back_to_back", "Simulation Day 3",      210),
    ],
    # WK 43 — Pre-taper deload · 9 hrs (Jul 26)43
    [
        ("rest",      "Rest",                    0),
        ("endurance", "Z2 Endurance",           60),
        ("sweetspot", "Low Cadence Sweetspot",  60),
        ("recovery",  "Recovery + Core",        45),
        ("tempo",     "Tempo Intervals 2×15",   75),
        ("endurance", "Z2 Endurance",           60),
        ("long",      "Long Ride (Easy)",      150),
    ],

    # ── PHASE 5: TAPER ─────────────────────────────────────────────────────────
    # 60% → 40% → race-week volume. Same intensity, halved duration.

    # WK 44 — Taper wk 1 · ~6 hrs (Aug 2)44
    [
        ("rest",      "Rest",                    0),
        ("tempo",     "Under-Overs 2×10 min",   75),
        ("sweetspot", "Low Cadence Sweetspot",  60),
        ("recovery",  "Recovery + Core",        45),
        ("endurance", "Z2 Endurance",           60),
        ("endurance", "Z2 Easy",                45),
        ("long",      "Long Ride (Moderate)",  180),
    ],
    # WK 45 — Taper wk 2 · ~4 hrs (Aug 9)45
    [
        ("rest",      "Rest",                    0),
        ("tempo",     "Tempo Sharpener",        60),
        ("recovery",  "Recovery Spin",          45),
        ("rest",      "Rest",                    0),
        ("sweetspot", "Short Sweetspot",        45),
        ("endurance", "Z2 Easy",                45),
        ("long",      "Easy Endurance",        120),
    ],
    # WK 46 — Race week · arrive fresh (Aug 16)46
    [
        ("rest",      "Rest",                    0),
        ("endurance", "Easy Spin",              45),
        ("recovery",  "Leg Openers",            30),
        ("rest",      "Rest",                    0),
        ("endurance", "Easy Prep Ride",         30),
        ("rest",      "Rest",                    0),
        ("rest",      "Travel — Nice",           0),
    ],
]


def _destack_quality(weeks: list[list[tuple[str, str, int]]]) -> list[list[tuple[str, str, int]]]:
    """Insert the mid-week recovery day BETWEEN two adjacent quality days.

    The Build/Specific/Peak template runs Tue = hardest quality (VO2 or
    under-overs), Wed = sweetspot, Thu = recovery (Strength + Core). That stacks
    two quality days back-to-back — too much intensity density for a 50+ athlete.
    Where that exact Tue-quality / Wed-sweetspot / Thu-recovery pattern occurs,
    swap Wed and Thu so the hardest session (Tue) is flanked by Mon rest and Wed
    recovery, and the two remaining quality days (Thu sweetspot, Fri tempo) are
    the less-taxing ones.

    Reordering only — weekly duration and per-type totals are unchanged, so
    `_hr_ctl_projection` (which sums each week) is unaffected. Applied once to the
    single source of truth so the calendar, session lookup and projection all see
    the same order, and any future week matching the pattern is handled too.
    """
    QUALITY = {"vo2", "tempo"}
    for wk in weeks:
        if (len(wk) == 7 and wk[1][0] in QUALITY
                and wk[2][0] == "sweetspot" and wk[3][0] == "recovery"):
            wk[2], wk[3] = wk[3], wk[2]
    return weeks


HR_TRAINING_WEEKS = _destack_quality(HR_TRAINING_WEEKS)

# ── Event stages ──────────────────────────────────────────────────────────────
HR_EVENT_STAGES: list[dict] = [
    {"day": 1, "date": date(2027, 8, 23), "label": "Nice → Cuneo",             "km": 182, "elev_m": 4020, "key_climb": "Col de la Lombarde 2340m"},
    {"day": 2, "date": date(2027, 8, 24), "label": "Cuneo → Col d'Izoard",     "km": 141, "elev_m": 3610, "key_climb": "Col d'Agnel 2744m"},
    {"day": 3, "date": date(2027, 8, 25), "label": "Briançon → Alpe d'Huez",  "km": 81,  "elev_m": 2375, "key_climb": "Col du Lautaret 2060m"},
    {"day": 4, "date": date(2027, 8, 26), "label": "TT — Alpe d'Huez",        "km": 16,  "elev_m": 1125, "key_climb": "Alpe d'Huez — 21 hairpins"},
    {"day": 5, "date": date(2027, 8, 27), "label": "Alpe d'Huez → Megève",    "km": 169, "elev_m": 3525, "key_climb": "Col du Glandon 1924m"},
    {"day": 6, "date": date(2027, 8, 28), "label": "Megève → Côte 2000",      "km": 100, "elev_m": 2560, "key_climb": "Col de la Colombière 1609m"},
    {"day": 7, "date": date(2027, 8, 29), "label": "Megève → Thonon-les-Bains","km": 119, "elev_m": 2040, "key_climb": "Col de Joux-Plane 1712m"},
]

# Heat protocol — static guidance rendered as a banner on the Taper phase.
# Deliberately NOT merged into HR_TRAINING_WEEKS: those week tuples feed
# _hr_ctl_projection and mutating them would silently change the projection.
HR_HEAT_PROTOCOL: dict = {
    "phase": "taper",
    "start_week": 44,
    "title": "Heat protocol — final 10 days",
    "note": (
        "5×60 min Z2 heat sessions (extra layers, or indoor trainer with no fan), "
        "last one 3 days pre-event — the minimal effective dose for plasma volume "
        "expansion. Maintain sodium 500–800 mg/h in these sessions, and weigh "
        "before/after to calibrate fluid loss."
    ),
}

_HR_PLAN_DAYS = len(HR_TRAINING_WEEKS) * 7  # 322


def _fmt_dur(dur: int) -> str:
    if not dur:
        return ""
    if dur < 60:
        return f"{dur}m"
    if dur % 60:
        return f"{dur // 60}h{dur % 60:02d}m"
    return f"{dur // 60}h"


def phase_for_week(week_num: int) -> dict | None:
    """Return the phase dict for a 1-indexed week number, or None."""
    for p in HR_PHASES:
        if p["week_start"] <= week_num <= p["week_end"]:
            return p
    return None


def hr_session_for_date(d: date) -> tuple[str, str, int] | None:
    """Return (type, label, duration_min) for the given date, or None if outside the plan."""
    delta = (d - HR_PLAN_START).days
    if delta < 0 or delta >= _HR_PLAN_DAYS:
        return None
    from .history import get_plan_override
    ov = get_plan_override(d.isoformat())
    if ov:
        return (ov["session_type"], ov["label"], ov["duration_min"])
    week_idx, day_idx = divmod(delta, 7)
    return HR_TRAINING_WEEKS[week_idx][day_idx]


def build_hr_calendar_weeks() -> list[dict]:
    """Return one dict per training week for template rendering."""
    from .history import list_plan_overrides
    overrides = {o["date"]: o for o in list_plan_overrides()}
    today = date.today()
    weeks = []
    for wk_idx, sessions in enumerate(HR_TRAINING_WEEKS):
        week_num = wk_idx + 1
        wk_start = HR_PLAN_START + timedelta(weeks=wk_idx)
        phase = phase_for_week(week_num)
        days = []
        total_min = 0
        for day_offset, (stype, label, dur) in enumerate(sessions):
            d = wk_start + timedelta(days=day_offset)
            ov = overrides.get(d.isoformat())
            if ov:
                dur = ov["duration_min"]
                if ov.get("session_type"):
                    stype = ov["session_type"]
                if ov.get("label"):
                    label = ov["label"]
            total_min += dur
            days.append({
                "date": d,
                "day_num": d.day,
                "month_abbr": d.strftime("%b"),
                "type": stype,
                "label": label,
                "dur_fmt": _fmt_dur(dur),
                "dur_min": dur,
                "is_today": d == today,
                "is_past": d < today,
                "overridden": bool(ov),
            })
        weeks.append({
            "week_num": week_num,
            "start": wk_start,
            "days": days,
            "phase": phase,
            "total_hrs": round(total_min / 60, 1),
        })
    return weeks


def build_hr_event_weeks() -> list[dict]:
    """Return two Mon–Sun rows covering the Haute Route Alpes event (Aug 23–29 2027)."""
    today = date.today()
    stages_by_date = {s["date"]: s for s in HR_EVENT_STAGES}
    grid_start = date(2027, 8, 23)  # Monday
    weeks = []
    for wk in range(1):
        wk_start = grid_start + timedelta(weeks=wk)
        cells = []
        for day_off in range(7):
            d = wk_start + timedelta(days=day_off)
            stage = stages_by_date.get(d)
            if stage:
                cells.append({
                    "date": d, "day_num": d.day, "month_abbr": d.strftime("%b"),
                    "label": stage["label"], "km": stage["km"],
                    "elev_m": stage["elev_m"], "key_climb": stage["key_climb"],
                    "day_num_stage": stage["day"],
                    "is_today": d == today, "is_past": d < today, "is_event": True,
                })
            else:
                cells.append({
                    "date": d, "day_num": d.day, "month_abbr": d.strftime("%b"),
                    "is_event": False,
                })
        weeks.append({"week_label": wk_start.strftime("%-d %b"), "days": cells})
    return weeks
