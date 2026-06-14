"""Post-training analysis: fetch activity detail from Garmin and generate Claude commentary."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .history import DB_PATH, _conn, get_cached_text, set_cached_text
from .plan import COMPOUND_SESSIONS, session_for_date, session_for_date_extended
from .llm import MODEL_FAST, MODEL_SMART

# ── DB schema ────────────────────────────────────────────────────────────────

def _ensure_analysis_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS activity_analyses (
            activity_id INTEGER PRIMARY KEY,
            hr_zones_json  TEXT,
            training_effect REAL,
            training_effect_label TEXT,
            aerobic_te_message TEXT,
            anaerobic_te REAL,
            training_load REAL,
            avg_respiration REAL,
            analysis_text TEXT,
            analysed_at TEXT DEFAULT (datetime('now'))
        )
    """)
    for col, typ in [
        ("ftp_effort_avg_hr",  "REAL"),
        ("ftp_effort_max_hr",  "REAL"),
        ("interval_data_json", "TEXT"),
    ]:
        try:
            con.execute(f"ALTER TABLE activity_analyses ADD COLUMN {col} {typ}")
        except Exception:
            pass


# ── Fetch detail from Garmin API ─────────────────────────────────────────────

_FTP_SESSION_LABELS = {"FTP Test", "FTP Re-test", "Final FTP Test"}

# effort_min/max in seconds — range that identifies a single effort lap
_INTERVAL_CONFIG: dict[str, dict] = {
    "Tempo Intervals":     {"effort_min": 480,  "effort_max": 780,  "name": "Tempo rep"},
    "Sweetspot Intervals": {"effort_min": 720,  "effort_max": 1200, "name": "Sweetspot block"},
    "Sweetspot Ride":      {"effort_min": 720,  "effort_max": 1200, "name": "Sweetspot block"},
    "Hill Repeats":        {"effort_min": 120,  "effort_max": 330,  "name": "Hill rep"},
    "Threshold Ride":      {"effort_min": 900,  "effort_max": 1500, "name": "Threshold block"},
    "Over-Unders":         {"effort_min": 840,  "effort_max": 1200, "name": "OU set"},
    # MaxiClimber work intervals: 90–240 s across all plan weeks (easy → Norwegian 4×4)
    "MaxiClimber":         {"effort_min": 80,   "effort_max": 260,  "name": "Climbing interval"},
    "Easy MaxiClimber":    {"effort_min": 80,   "effort_max": 260,  "name": "Climbing interval"},
    "KB + MaxiClimber":    {"effort_min": 80,   "effort_max": 260,  "name": "Climbing interval"},
}


def _extract_interval_data(api: Any, activity_id: int, session_label: str) -> dict:
    """Return per-rep HR data for structured interval sessions using lap splits."""
    config = _INTERVAL_CONFIG.get(session_label)
    if not config:
        return {}
    try:
        splits = api.get_activity_splits(activity_id)
        laps = splits.get("lapDTOs") or splits.get("laps") or []
        lo, hi = config["effort_min"], config["effort_max"]
        effort_laps = [
            l for l in laps
            if lo <= (l.get("duration") or l.get("elapsedDuration") or 0) <= hi
            and (l.get("averageHR") or 0) > 0
        ]
        if not effort_laps:
            return {}
        reps = []
        for i, l in enumerate(effort_laps, 1):
            dur = round(l.get("duration") or l.get("elapsedDuration") or 0)
            reps.append({
                "rep":          i,
                "name":         config["name"],
                "avg_hr":       round(l["averageHR"]) if l.get("averageHR") else None,
                "max_hr":       round(l["maxHR"])     if l.get("maxHR")     else None,
                "duration_secs": dur,
            })
        return {"interval_reps": reps} if reps else {}
    except Exception:
        return {}


def _extract_ftp_effort(api: Any, activity_id: int) -> dict:
    """Call get_activity_splits and return avg/max HR for the best ~20-min lap.

    Looks for the lap >= 10 minutes with the highest avg HR — that's the
    all-out 20-min test effort. Returns empty dict on any failure.
    """
    try:
        splits = api.get_activity_splits(activity_id)
        laps = splits.get("lapDTOs") or splits.get("laps") or []
        candidates = [
            l for l in laps
            if (l.get("duration") or l.get("elapsedDuration") or 0) >= 600
        ]
        if not candidates:
            return {}
        best = max(candidates, key=lambda l: l.get("averageHR") or 0)
        avg_hr = best.get("averageHR")
        if not avg_hr:
            return {}
        return {
            "ftp_effort_avg_hr": round(avg_hr),
            "ftp_effort_max_hr": round(best["maxHR"]) if best.get("maxHR") else None,
        }
    except Exception:
        return {}


_CYCLING_TYPES = {"road_biking", "cycling", "virtual_ride", "indoor_cycling", "mountain_biking"}


def _extract_durability(api: Any, activity: dict) -> Optional[dict]:
    """Late-ride HR drift from lap splits: duration-weighted avg HR of the
    final third of the ride vs the first third.

    drift_pct = (final − first) / first × 100. Positive = HR rising late in
    the ride at (assumed) constant effort — a durability/fatigue-resistance
    signal. Returns None when fewer than 3 HR-bearing laps (e.g. single-lap
    indoor rides) so callers can skip silently.
    """
    try:
        splits = api.get_activity_splits(activity["activity_id"])
        laps = splits.get("lapDTOs") or splits.get("laps") or []
        hr_laps = [
            l for l in laps
            if (l.get("averageHR") or 0) > 0
            and (l.get("duration") or l.get("elapsedDuration") or 0) > 0
        ]
        if len(hr_laps) < 3:
            return None

        def _dur(l: dict) -> float:
            return float(l.get("duration") or l.get("elapsedDuration") or 0)

        total = sum(_dur(l) for l in hr_laps)
        third = total / 3.0

        def _weighted_hr(lap_subset: list[dict]) -> Optional[float]:
            secs = sum(_dur(l) for l in lap_subset)
            if secs <= 0:
                return None
            return sum(float(l["averageHR"]) * _dur(l) for l in lap_subset) / secs

        # Bucket laps into thirds by cumulative duration
        first_laps, final_laps = [], []
        elapsed = 0.0
        for l in hr_laps:
            mid = elapsed + _dur(l) / 2.0
            if mid < third:
                first_laps.append(l)
            elif mid >= 2 * third:
                final_laps.append(l)
            elapsed += _dur(l)
        first_hr = _weighted_hr(first_laps)
        final_hr = _weighted_hr(final_laps)
        if not first_hr or not final_hr:
            return None
        return {
            "date": activity["date"],
            "duration_min": round((activity.get("duration_seconds") or total) / 60),
            "first_third_hr": round(first_hr, 1),
            "final_third_hr": round(final_hr, 1),
            "drift_pct": round((final_hr - first_hr) / first_hr * 100, 2),
            "n_laps": len(hr_laps),
        }
    except Exception:
        return None


def fetch_activity_detail(api: Any, activity_id: int, activity: Optional[dict] = None,
                          session_label: Optional[str] = None) -> dict:
    """Return a merged dict of activity summary + HR zones.

    Uses inline fields from the activity row when available (avoids two extra
    API calls). Falls back to API for activities saved before the new columns.
    For FTP test sessions, additionally fetches lap splits to extract the
    20-min effort HR.
    """
    if activity and activity.get("hr_zone_1_sec") is not None:
        total_secs = sum(activity.get(f"hr_zone_{z}_sec") or 0 for z in range(1, 6)) or 1
        hr_zones = [
            {
                "zone": z,
                "secs": round(activity.get(f"hr_zone_{z}_sec") or 0),
                "pct": round((activity.get(f"hr_zone_{z}_sec") or 0) / total_secs * 100),
                "low_bpm": None,
            }
            for z in range(1, 6)
        ]
        result = {
            "training_effect":       activity.get("aerobic_te"),
            "training_effect_label": activity.get("training_effect_label"),
            "aerobic_te_message":    None,
            "anaerobic_te":          activity.get("anaerobic_te"),
            "training_load":         activity.get("training_load"),
            "avg_respiration":       activity.get("avg_respiration"),
            "hr_zones":              hr_zones,
        }
        if session_label in _FTP_SESSION_LABELS:
            result.update(_extract_ftp_effort(api, activity_id))
        elif session_label in _INTERVAL_CONFIG:
            result.update(_extract_interval_data(api, activity_id, session_label))
        return result

    summary_raw = api.get_activity(activity_id)
    s = summary_raw.get("summaryDTO", {})

    hr_zones_raw = api.get_activity_hr_in_timezones(activity_id)
    total_secs = sum(z.get("secsInZone", 0) for z in hr_zones_raw) or 1
    hr_zones = [
        {
            "zone": z["zoneNumber"],
            "secs": round(z["secsInZone"]),
            "pct": round(z["secsInZone"] / total_secs * 100),
            "low_bpm": z.get("zoneLowBoundary"),
        }
        for z in sorted(hr_zones_raw, key=lambda x: x["zoneNumber"])
    ]

    result = {
        "training_effect":       s.get("trainingEffect"),
        "training_effect_label": s.get("trainingEffectLabel"),
        "aerobic_te_message":    s.get("aerobicTrainingEffectMessage"),
        "anaerobic_te":          s.get("anaerobicTrainingEffect"),
        "training_load":         s.get("activityTrainingLoad"),
        "avg_respiration":       s.get("avgRespirationRate"),
        "hr_zones":              hr_zones,
    }
    if session_label in _FTP_SESSION_LABELS:
        result.update(_extract_ftp_effort(api, activity_id))
    elif session_label in _INTERVAL_CONFIG:
        result.update(_extract_interval_data(api, activity_id, session_label))
    return result


def save_detail(activity_id: int, detail: dict, analysis_text: str) -> None:
    with _conn() as con:
        _ensure_analysis_schema(con)
        interval_reps = detail.get("interval_reps")
        con.execute(
            """INSERT OR REPLACE INTO activity_analyses
               (activity_id, hr_zones_json, training_effect, training_effect_label,
                aerobic_te_message, anaerobic_te, training_load, avg_respiration,
                analysis_text, ftp_effort_avg_hr, ftp_effort_max_hr, interval_data_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                activity_id,
                json.dumps(detail["hr_zones"]),
                detail.get("training_effect"),
                detail.get("training_effect_label"),
                detail.get("aerobic_te_message"),
                detail.get("anaerobic_te"),
                detail.get("training_load"),
                detail.get("avg_respiration"),
                analysis_text,
                detail.get("ftp_effort_avg_hr"),
                detail.get("ftp_effort_max_hr"),
                json.dumps(interval_reps) if interval_reps else None,
            ),
        )


def load_analysis(activity_id: int) -> Optional[dict]:
    with _conn() as con:
        _ensure_analysis_schema(con)
        row = con.execute(
            "SELECT * FROM activity_analyses WHERE activity_id = ?",
            (activity_id,),
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["hr_zones"] = json.loads(d["hr_zones_json"]) if d.get("hr_zones_json") else []
    d["interval_reps"] = json.loads(d["interval_data_json"]) if d.get("interval_data_json") else []
    return d


# ── Claude analysis ──────────────────────────────────────────────────────────

def _fmt_secs(s: int) -> str:
    m, sec = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{sec:02d}s"


def _te_label(label: Optional[str]) -> str:
    if not label:
        return "unknown"
    return label.replace("_", " ").title()


_CYCLING_TYPES = {"road_biking", "cycling", "virtual_ride", "indoor_cycling", "mountain_biking"}
_RUNNING_TYPES = {"running", "trail_running", "treadmill_running"}
_RUCK_TYPES    = {"hiking", "walking", "load_carry", "rucking"}


def _coach_system_prompt(type_key: str, activity_name: str = "") -> str:
    tail = (
        "Be direct, specific, and evidence-based. Reference the numbers. "
        "No bullet markdown — short paragraphs only. Address the athlete as 'you'."
    )
    is_ruck = type_key in _RUCK_TYPES or "load carry" in activity_name.lower()
    if type_key in _CYCLING_TYPES:
        return (
            "You are an experienced cycling coach and endurance specialist reviewing a completed ride. " + tail
        )
    if type_key == "stair_climbing":
        return (
            "You are an experienced cardio interval coach reviewing a completed MaxiClimber session. "
            "The MaxiClimber is a full-body vertical climbing machine (arms AND legs simultaneously) "
            "used here as a structured cardio interval tool — not a strength exercise. "
            "Treat it like a cardio interval session: evaluate HR zone execution, interval quality, "
            "aerobic vs anaerobic balance, and recovery between efforts. " + tail
        )
    if type_key == "strength_training":
        return (
            "You are an experienced strength and conditioning coach reviewing a completed "
            "kettlebell and strength session. " + tail
        )
    if is_ruck:
        return (
            "You are an experienced military fitness and load-carrying coach reviewing a "
            "completed ruck session. " + tail
        )
    if type_key in _RUNNING_TYPES:
        return (
            "You are an experienced running coach reviewing a completed run. " + tail
        )
    return (
        "You are an experienced endurance and conditioning coach reviewing a completed "
        "training session. " + tail
    )


def generate_analysis(activity: dict, detail: dict, companion: Optional[dict] = None) -> str:
    """Call Claude Haiku to analyse the workout and return a short commentary."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _rule_based_analysis(activity, detail)

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    prompt = _build_analysis_prompt(activity, detail, companion=companion)
    type_key = activity.get("type_key", "")
    name = activity.get("name") or ""
    try:
        msg = client.messages.create(
            model=MODEL_SMART,
            max_tokens=500,
            system=_coach_system_prompt(type_key, name),
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
    except Exception:
        return _rule_based_analysis(activity, detail)


def _find_compound_companion(activity: dict, day_acts: list[dict]) -> Optional[dict]:
    """Return the companion activity if this is one half of a compound plan session."""
    act_date = activity.get("date", "")
    if not act_date:
        return None
    d_obj = date.fromisoformat(act_date)
    session = session_for_date_extended(d_obj)
    if not session:
        return None
    _, slabel, _ = session
    compound = COMPOUND_SESSIONS.get(slabel)
    if not compound:
        return None
    act_key = activity.get("type_key")
    if not any(s["garmin_key"] == act_key for s in compound):
        return None
    companion_key = next(s["garmin_key"] for s in compound if s["garmin_key"] != act_key)
    return next(
        (a for a in day_acts if a["activity_id"] != activity["activity_id"]
         and a["type_key"] == companion_key),
        None,
    )


def _build_analysis_prompt(activity: dict, detail: dict, companion: Optional[dict] = None) -> str:
    act_date = activity.get("date", "")
    d_obj = date.fromisoformat(act_date) if act_date else None

    dur_secs = int(activity.get("duration_seconds") or 0)
    dur_fmt = _fmt_secs(dur_secs)
    dist_km = round((activity.get("distance_meters") or 0) / 1000, 1)
    type_key = activity.get("type_key", "")
    avg_speed_kmh = (
        round(dist_km / (dur_secs / 3600), 1)
        if type_key in _CYCLING_TYPES and dur_secs > 0 and dist_km > 0
        else None
    )
    avg_hr = activity.get("avg_hr")
    max_hr = activity.get("max_hr")
    calories = activity.get("calories")
    elev = activity.get("elevation_gain")
    name = activity.get("name") or activity.get("type_key", "ride")

    hr_zones = detail.get("hr_zones", [])
    zone_lines = []
    for z in hr_zones:
        bar = "█" * max(1, z["pct"] // 5)
        bpm_str = f"≥{z['low_bpm']}bpm" if z.get("low_bpm") else f"Zone {z['zone']}"
        zone_lines.append(f"  Z{z['zone']} ({bpm_str}): {bar} {z['pct']}%  {_fmt_secs(z['secs'])}")

    te = detail.get("training_effect")
    te_label = _te_label(detail.get("training_effect_label"))
    tl = detail.get("training_load")
    resp = detail.get("avg_respiration")
    aerobic_msg = (detail.get("aerobic_te_message") or "").replace("_", " ")

    # Planned session context
    plan_line = ""
    compound_lines: list[str] = []
    if d_obj:
        session = session_for_date_extended(d_obj)
        if session:
            stype, slabel, sdur = session
            plan_line = f"\nPlanned workout for this day: {slabel} ({stype}, {sdur}m total)"
            dur_min = int(dur_secs / 60)
            if not COMPOUND_SESSIONS.get(slabel):
                over_pct = (dur_min - sdur) / sdur * 100 if sdur else 0
                if dur_min >= sdur * 0.95 and over_pct <= 25:
                    plan_line += (
                        f"\nNote: actual duration ({dur_min}m) is within normal range of the plan ({sdur}m). "
                        "Do NOT flag this as significantly exceeding or cutting short the plan."
                    )
                elif dur_min < sdur * 0.95:
                    pass  # genuinely short — Claude can comment
                # over 25% is a genuine overrun — no note needed
            if COMPOUND_SESSIONS.get(slabel):
                if companion:
                    comp_dur = int((companion.get("duration_seconds") or 0) / 60)
                    comp_name = companion.get("name") or companion.get("type_key", "")
                    comp_hr = companion.get("avg_hr")
                    comp_cal = companion.get("calories")
                    combined_min = int(dur_secs / 60) + comp_dur
                    compound_lines = [
                        "",
                        "Combined session context: both components were completed today.",
                        f"Companion activity — {comp_name}: {comp_dur}m, avg HR {comp_hr} bpm, {comp_cal} kcal",
                        f"Combined duration: ~{combined_min}m (plan target: {sdur}m)",
                        "Analyse this component in the context of the full combined session.",
                    ]
                else:
                    compound_lines = [
                        "",
                        f"Note: the plan calls for a combined {slabel} session ({sdur}m total).",
                        "Only this component was logged today. Do not flag the duration as short.",
                    ]

    equipment_note = (
        "Equipment note: this 'stair_climbing' activity was performed on a MaxiClimber — "
        "a vertical climbing machine that works arms AND legs simultaneously (not legs-only). "
        "It is used here as a structured cardio interval tool with timed work/rest sets at prescribed HR zones "
        "(Z2–Z3 in standard weeks, Z4 for Norwegian 4×4 protocol in the peak block). "
        "Analyse it as a cardio interval session: focus on HR zone adherence during work intervals, "
        "recovery completeness during rest intervals, and overall aerobic training effect. "
        "Do NOT treat it as a strength or resistance session."
        if type_key == "stair_climbing" else
        "Activity note: 'load carry' and 'rucking' are the same activity — walking or hiking with a weighted pack. "
        "Do not treat them as different session types. A planned ruck and a logged load carry are equivalent."
        if "load carry" in name.lower() else ""
    )

    _ftp_labels = {"FTP Test", "FTP Re-test", "Final FTP Test"}
    _is_ftp = bool(d_obj and session_for_date_extended(d_obj) and session_for_date_extended(d_obj)[1] in _ftp_labels)
    ftp_effort_avg_hr = detail.get("ftp_effort_avg_hr")
    ftp_effort_max_hr = detail.get("ftp_effort_max_hr")
    ftp_effort_line = ""
    if _is_ftp and ftp_effort_avg_hr:
        max_str = f", max HR {int(ftp_effort_max_hr)} bpm" if ftp_effort_max_hr else ""
        ftp_effort_line = (
            f"20-minute test effort (extracted from lap data): "
            f"avg HR {int(ftp_effort_avg_hr)} bpm{max_str}. "
            "This avg HR is the athlete's estimated lactate threshold HR. "
            "Reference it when assessing whether the effort was maximal."
        )
    ftp_note = (
        "Session structure note: this is an FTP test. The standard structure is "
        "~15m warm-up (Z1–Z2), 3m priming effort, 5m Z1 recovery, then a 20-minute all-out effort (target Z4–Z5), "
        "followed by ~17m cool-down (Z1–Z2). The HR zone distribution across the full activity will therefore "
        "show significant Z1–Z2 time from the warm-up and cool-down — this is expected and correct. "
        "Focus your analysis on whether the 20-minute test effort was well-executed: "
        "was max HR high, did the athlete sustain effort into Z4–Z5, and how does the training load reflect the test demand?"
        if _is_ftp else ""
    )

    # Interval rep summary for structured sessions
    interval_reps = detail.get("interval_reps") or []
    interval_lines: list[str] = []
    if interval_reps:
        interval_lines.append("Interval rep data (from lap splits):")
        for rep in interval_reps:
            dur_s = rep.get("duration_secs") or 0
            dur_fmt_rep = f"{dur_s // 60}m{dur_s % 60:02d}s"
            avg = f"avg HR {rep['avg_hr']} bpm" if rep.get("avg_hr") else ""
            mx  = f", max {rep['max_hr']} bpm" if rep.get("max_hr") else ""
            interval_lines.append(f"  {rep['name']} {rep['rep']}: {avg}{mx} ({dur_fmt_rep})")
        if len(interval_reps) >= 2:
            first_hr = interval_reps[0].get("avg_hr") or 0
            last_hr  = interval_reps[-1].get("avg_hr") or 0
            drift = last_hr - first_hr
            sign = "+" if drift >= 0 else ""
            interval_lines.append(
                f"HR drift across reps: {sign}{drift} bpm (rep 1 → rep {len(interval_reps)}) — "
                + ("expected accumulation" if drift > 0 else "HR stayed flat or dropped")
            )

    lines = [
        f"Activity: {name}",
        f"Date: {act_date}",
        f"Duration: {dur_fmt}  Distance: {dist_km} km" + (f"  Avg speed: {avg_speed_kmh} km/h" if avg_speed_kmh else ""),
        f"Avg HR: {avg_hr} bpm  Max HR: {max_hr} bpm",
        f"Calories: {calories}  Elevation gain: {elev} m",
        f"Aerobic training effect: {te} ({te_label}) — {aerobic_msg}",
        f"Training load: {tl}",
        f"Avg respiration: {resp} breaths/min" if resp else "",
        equipment_note,
        ftp_note,
        ftp_effort_line,
        *interval_lines,
        "",
        "Heart rate zone distribution:",
        *zone_lines,
        plan_line,
        *compound_lines,
        "",
        "Please provide:",
        "1. A one-sentence headline: how well was this session executed?",
        "2. Two or three sentences on the HR zone distribution — was it appropriate for this session type?",
        "3. One sentence on the training effect and what it means for fitness adaptation.",
        "4. One sentence on recovery — how demanding was this relatively?",
        "Keep it under 150 words. Plain paragraphs, no headers or bullets.",
    ]
    return "\n".join(l for l in lines if l is not None)


def _rule_based_analysis(activity: dict, detail: dict) -> str:
    te = detail.get("training_effect") or 0
    te_label = _te_label(detail.get("training_effect_label"))
    hr_zones = detail.get("hr_zones", [])

    z2_pct = next((z["pct"] for z in hr_zones if z["zone"] == 2), 0)
    z3_pct = next((z["pct"] for z in hr_zones if z["zone"] == 3), 0)
    high_zone_pct = sum(z["pct"] for z in hr_zones if z["zone"] >= 4)

    if high_zone_pct > 20:
        intensity = "high-intensity session with significant time in zones 4-5"
    elif z3_pct > 40:
        intensity = "moderate-intensity session predominantly in zone 3"
    elif z2_pct > 40:
        intensity = "good aerobic session with solid zone 2 work"
    else:
        intensity = "mixed-intensity session"

    return (
        f"This was a {intensity}. "
        f"Aerobic training effect: {te:.1f} ({te_label}). "
        f"{'Consider more zone 2 work to build aerobic base.' if z2_pct < 20 else 'Zone distribution looks reasonable for this type of session.'}"
    )


# ── Main entry point used by server ─────────────────────────────────────────

def refresh_analyses(api: Any, days: int = 14) -> None:
    """Fetch detail + generate analysis for any unanalysed activities in the window."""
    from .history import load_recent_activities
    activities = load_recent_activities(days=days)
    acts_by_date: dict[str, list[dict]] = {}
    for act in activities:
        acts_by_date.setdefault(act["date"], []).append(act)
    for act in activities:
        act_id = act["activity_id"]
        # Durability extraction is independent of the AI analysis — run it for
        # any long cycling activity not yet measured (≥90 min, lap splits only).
        try:
            from .history import durability_exists, save_durability
            if (act.get("type_key") in _CYCLING_TYPES
                    and (act.get("duration_seconds") or 0) >= 90 * 60
                    and not durability_exists(act_id)):
                dur_row = _extract_durability(api, act)
                if dur_row:
                    save_durability(act_id, dur_row)
        except Exception:
            pass
        # Backfill ftp_tests for already-analysed FTP sessions that pre-date the auto-population logic
        existing = load_analysis(act_id)
        if existing is not None:
            try:
                act_date = act.get("date")
                if act_date:
                    sess = session_for_date_extended(date.fromisoformat(act_date))
                    session_label = sess[1] if sess else None
                    if session_label in _FTP_SESSION_LABELS and existing.get("ftp_effort_avg_hr"):
                        from .history import save_ftp_test, load_ftp_tests
                        d_obj = date.fromisoformat(act_date)
                        if not any(t["date"] == d_obj.isoformat() for t in load_ftp_tests()):
                            save_ftp_test(
                                d_obj.isoformat(), act_id,
                                int(existing["ftp_effort_avg_hr"]),
                                int(existing["ftp_effort_max_hr"]) if existing.get("ftp_effort_max_hr") else None,
                                None,
                            )
            except Exception:
                pass
            continue  # already analysed — nothing more to do
        try:
            companion = _find_compound_companion(act, acts_by_date.get(act["date"], []))
            act_date = act.get("date")
            session_label = None
            if act_date:
                sess = session_for_date_extended(date.fromisoformat(act_date))
                if sess:
                    session_label = sess[1]
            detail = fetch_activity_detail(api, act_id, activity=act, session_label=session_label)
            text = generate_analysis(act, detail, companion=companion)
            save_detail(act_id, detail, text)
            # Auto-populate FTP trend table for FTP test sessions
            if session_label in _FTP_SESSION_LABELS and detail.get("ftp_effort_avg_hr"):
                try:
                    from .history import save_ftp_test, load_ftp_tests
                    d_obj = date.fromisoformat(act_date) if act_date else None
                    if d_obj and not any(t["date"] == d_obj.isoformat() for t in load_ftp_tests()):
                        save_ftp_test(
                            d_obj.isoformat(), act_id,
                            int(detail["ftp_effort_avg_hr"]),
                            int(detail["ftp_effort_max_hr"]) if detail.get("ftp_effort_max_hr") else None,
                            None,
                        )
                except Exception:
                    pass
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("analysis failed for %s: %s", act_id, exc)


def load_analyses_for_activities(activities: list[dict]) -> list[dict]:
    """Return activities enriched with analysis data (hr_zones, analysis_text, etc.)."""
    result = []
    for act in activities:
        a = dict(act)
        analysis = load_analysis(act["activity_id"])
        if analysis:
            a.update(analysis)
        result.append(a)
    return result


def retrieve_relevant_analyses(session_type: str, limit: int = 3) -> list[dict]:
    """Return the most recent past activity analyses matching a plan session type.

    Structured retrieval (no embeddings): joins activity_analyses → activities and
    filters to the Garmin type_keys that satisfy `session_type` (via ACTIVITY_MATCH).
    Used to ground the coach chat — "last time you did this kind of session…".
    Returns compact dicts; empty list if no matches or no analyses yet.
    """
    from .history import ACTIVITY_MATCH

    keys = ACTIVITY_MATCH.get(session_type)
    if not keys:
        return []
    placeholders = ",".join("?" * len(keys))
    with _conn() as con:
        _ensure_analysis_schema(con)
        try:
            rows = con.execute(
                f"""SELECT ac.date AS date, ac.name AS name, ac.type_key AS type_key,
                           ac.avg_hr AS avg_hr, ac.hr_zone_4_sec AS z4, ac.hr_zone_5_sec AS z5,
                           an.training_effect AS training_effect,
                           an.training_effect_label AS training_effect_label,
                           an.training_load AS training_load,
                           an.analysis_text AS analysis_text
                    FROM activity_analyses an
                    JOIN activities ac ON ac.activity_id = an.activity_id
                    WHERE ac.type_key IN ({placeholders})
                      AND an.analysis_text IS NOT NULL AND an.analysis_text != ''
                    ORDER BY ac.date DESC
                    LIMIT ?""",
                (*keys, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    out = []
    for r in rows:
        d = dict(r)
        text = (d.get("analysis_text") or "").strip().replace("\n", " ")
        d["summary"] = (text[:280] + "…") if len(text) > 280 else text
        d["z45_min"] = round(((d.get("z4") or 0) + (d.get("z5") or 0)) / 60)
        out.append(d)
    return out


# ── Missed session recovery suggestions ─────────────────────────────────────

def generate_recovery_suggestion(
    missed_date: date,
    session: tuple,
    upcoming: list[tuple],
    recent_metrics: list[dict],
) -> str:
    """Return coach advice on whether to make up, skip, or adjust after a missed session.

    Cached in text_cache with key 'recovery_{date}'; subsequent calls are instant.
    """
    cache_key = f"recovery_{missed_date.isoformat()}"
    cached = get_cached_text(cache_key)
    if cached:
        return cached

    stype, slabel, sdur = session
    days_left_in_week = 6 - missed_date.weekday()  # Mon=0, Sun=6 → 0 on Sunday

    prompt_lines = [
        f"Missed session: {slabel} ({stype}, planned {sdur}m)",
        f"Day missed: {missed_date.strftime('%A %-d %B %Y')}",
        f"Days remaining in this week after today: {days_left_in_week}",
        "",
    ]

    if upcoming:
        prompt_lines.append("Remaining sessions planned this week:")
        for d, (utype, ulabel, udur) in upcoming:
            prompt_lines.append(f"  {d.strftime('%A')}: {ulabel} ({utype}, {udur}m)")
        prompt_lines.append("")
    else:
        prompt_lines += ["No further sessions planned this week.", ""]

    readiness_lines = []
    for r in recent_metrics:
        hrv = r.get("hrv_last_night")
        sleep = r.get("sleep_score")
        stress = r.get("avg_stress")
        parts = []
        if hrv is not None:
            parts.append(f"HRV {hrv:.0f}ms")
        if sleep is not None:
            parts.append(f"sleep {sleep:.0f}/100")
        if stress is not None:
            parts.append(f"stress {stress:.0f}/100")
        if parts:
            readiness_lines.append(f"  {r['date'].strftime('%-d %b')}: {', '.join(parts)}")

    if readiness_lines:
        prompt_lines += ["Recent readiness (last 3 days):"] + readiness_lines + [""]

    prompt_lines.append(
        "Should the athlete make this session up, skip it, or adjust the rest of the week? "
        "Give a clear recommendation with specific, actionable reasoning."
    )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _rule_based_recovery(stype, days_left_in_week)

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=MODEL_FAST,
            max_tokens=350,
            system=(
                "You are an experienced endurance and strength coach advising an athlete who missed "
                "a planned training session. Give a clear recommendation: make it up, skip it, or "
                "adjust the rest of the week. Be specific and practical. Two short paragraphs "
                "maximum. No bullet points. Address the athlete as 'you'."
            ),
            messages=[{"role": "user", "content": "\n".join(prompt_lines)}],
        )
        text = msg.content[0].text
        set_cached_text(cache_key, text)
        return text
    except Exception:
        return _rule_based_recovery(stype, days_left_in_week)


def _rule_based_recovery(stype: str, days_left: int) -> str:
    if days_left >= 2:
        if stype == "strength":
            return (
                "You still have time to fit this in. A shorter KB + MaxiClimber session — "
                "even 30 minutes — is better than nothing. If fatigue is running high, skip it "
                "and keep the rest of your week on track.\n\n"
                "Consistency matters more than any single session at this stage of the plan."
            )
        elif stype in ("ftp",):
            return (
                "An FTP test requires you to be fresh — don't squeeze it in if you're tired. "
                "Push it to the next day where you have at least one rest day beforehand.\n\n"
                "If the week is too disrupted, skip this test cycle and catch it next scheduled opportunity."
            )
        elif stype in ("bike", "tempo", "long"):
            return (
                "You have days left to reschedule. If you feel good, slot this ride in soon — "
                "consider shortening it slightly if time is tight. One missed aerobic session "
                "won't derail your fitness.\n\n"
                "If energy is low, skip it entirely. Protect the quality of your remaining sessions."
            )
        else:
            return (
                "With days still available, try to fit this in when energy allows. A shortened "
                "version at 60–70% of planned duration still delivers training stimulus.\n\n"
                "If you're run down, skip it and focus on executing the rest of the week well."
            )
    else:
        return (
            "With limited days left this week, it's better to skip this session rather than "
            "cramming it in on a tired body. Forced make-up sessions near week's end often "
            "compromise the next week's training.\n\n"
            "Come back fresh on Monday and stay consistent from there."
        )


# ── Workout descriptions (calendar modal coaching notes) ─────────────────────

# Exact step structure of each session type — mirrors workouts.py
_STEP_SUMMARIES: dict[str, str] = {
    "Easy Spin":        "10m warm-up → Z1–2 easy riding → 10m cool-down",
    "Zone 2 Steady":    "10m warm-up → sustained Z2 main block → 10m cool-down",
    "Recovery Spin":    "10m warm-up → Z1 only (very easy) → 10m cool-down",
    "Structured Z2":    "10m warm-up → 3 × (12m Z2 + 2m easy recovery) → 10m cool-down",
    "Hill Repeats":     "10m warm-up → 5 × (3m Z4–5 hill effort + 3m Z1 descent recovery) → 10m cool-down",
    "Sweetspot Ride":   "15m warm-up → 3 × (15m at 88–93% FTP sweetspot + 5m Z2 recovery) → 10m cool-down",
    "Over-Unders":      "15m warm-up → 2 sets × [4 × (2m over @ 105% FTP + 2m under @ 95% FTP)], 5m Z1 between sets → 10m cool-down",
    "Threshold Ride":   "15m warm-up → 2 × 20m at Z4 (100% FTP) with 5m Z2 recovery → 10m cool-down",
    "Low Cadence Ride": "10m warm-up → 6 × (4m at 60–70 rpm Z3 + 2m Z1 recovery) → 20m Z2 steady → 10m cool-down",
    "Z2 Ride":          "10m warm-up → sustained Z2 steady-state → 10m cool-down",
    "Easy Ride":        "10m warm-up → easy Z1–2 riding (active recovery) → 10m cool-down",
    "Cadence Drills":   "10m warm-up → 5 × (3m at 90–110 rpm + 2m Z2) → 15m Z2 steady → 10m cool-down",
    "Z2 Endurance":     "10m warm-up → sustained Z2 main block → 10m cool-down",
    "Low Cadence":      "10m warm-up → 5 × (4m at 60–70 rpm + 2m Z1 recovery) → 10m Z2 → 10m cool-down",
    "Easy Prep Ride":   "10m warm-up → Z1–2 very easy → 10m cool-down",
    "FTP Test":         "15m warm-up → 3m priming effort → 5m Z1 easy → 20-min all-out effort → 17m cool-down",
    "FTP Re-test":      "15m warm-up → 3m priming effort → 5m Z1 easy → 20-min all-out effort → 17m cool-down",
    "Final FTP Test":   "15m warm-up → 3m priming effort → 5m Z1 easy → 20-min all-out effort → 17m cool-down",
    "Tempo Intervals":  "15m warm-up → 3 × (10m Z4 + 5m Z1 recovery) → 5m cool-down",
    "Long Ride":        "15m warm-up → sustained Z2 main block → 15m cool-down",
    "Long Ride (Easy)": "15m warm-up → easy Z1–2 riding → 15m cool-down",
    "KB + MaxiClimber": "Kettlebell strength work (swings, presses, carries) then MaxiClimber full-body climbing intervals — arms and legs simultaneously. Interval protocol progresses each phase toward Norwegian 4×4 in the peak block.",
    "MaxiClimber":      "MaxiClimber full-body vertical climbing (arms and legs) at easy pace — deload or recovery week session.",
    "Easy MaxiClimber": "Easy-pace MaxiClimber full-body climbing for active recovery — low intensity, focus on movement quality.",
    "Light KB":         "Light kettlebell technique and conditioning work emphasising form and movement quality over load.",
}


def _ensure_workout_desc_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS workout_descriptions (
            label TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            generated_at TEXT DEFAULT (datetime('now'))
        )
    """)


def _load_workout_descs() -> dict[str, str]:
    with _conn() as con:
        _ensure_workout_desc_schema(con)
        rows = con.execute("SELECT label, description FROM workout_descriptions").fetchall()
    return {r["label"]: r["description"] for r in rows}


def prefetch_workout_descriptions(labels: list[str]) -> dict[str, str]:
    """Return {label: coaching_description} for all labels; generate missing ones via Claude."""
    existing = _load_workout_descs()
    missing = [l for l in labels if l not in existing]
    if not missing:
        return existing

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return existing

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    lines = [
        "You are an experienced endurance and conditioning coach. For each workout below write exactly 2 sentences:",
        "Sentence 1: what physiological adaptation this session targets and why it's in the plan.",
        "Sentence 2: the single most important execution tip for getting it right.",
        "Reply ONLY with valid JSON mapping label → two-sentence string. No extra keys or text.",
        "",
        "Workouts:",
    ]
    for label in missing:
        summary = _STEP_SUMMARIES.get(label, label)
        lines.append(f'"{label}": {summary}')

    try:
        msg = client.messages.create(
            model=MODEL_FAST,
            max_tokens=2000,
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )
        import json as _json
        raw = msg.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            raw = raw.rsplit("```", 1)[0].strip()
        result: dict[str, str] = _json.loads(raw)
        with _conn() as con:
            _ensure_workout_desc_schema(con)
            for label, desc in result.items():
                if isinstance(desc, str):
                    con.execute(
                        "INSERT OR REPLACE INTO workout_descriptions (label, description) VALUES (?,?)",
                        (label, desc),
                    )
        existing.update(result)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("workout desc generation failed: %s", exc)

    return existing


# ── Nutrition targets (Claude-calculated kcal per session type+duration) ─────

_SESSION_TYPE_DESC: dict[str, str] = {
    "rest":     "complete rest day",
    "strength": "kettlebell and MaxiClimber strength training",
    "bike":     "Zone 2 steady cycling",
    "tempo":    "tempo intervals cycling (high intensity)",
    "ftp":      "FTP test — maximal 20-minute cycling effort",
    "ruck":     "weighted rucking carrying 8–15 kg pack",
    "long":     "long Zone 2 cycling endurance ride",
}


def _ensure_nutrition_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS nutrition_targets (
            session_key  TEXT PRIMARY KEY,
            kcal         INTEGER,
            protein_g    INTEGER,
            carbs_g      INTEGER,
            fat_g        INTEGER,
            brief        TEXT,
            generated_at TEXT DEFAULT (datetime('now'))
        )
    """)


def _load_nutrition_targets() -> dict[str, dict]:
    with _conn() as con:
        _ensure_nutrition_schema(con)
        rows = con.execute("SELECT * FROM nutrition_targets").fetchall()
    return {r["session_key"]: dict(r) for r in rows}


def prefetch_nutrition_targets(sessions: list[tuple[str, int]], goal: str = "cut") -> dict[str, dict]:
    """Return {f"{goal}_{type}_{dur}": {kcal, protein_g, carbs_g, fat_g, brief}} for every session.

    `goal` switches the energy strategy and is part of the cache key so the two
    training blocks never collide:
      - "cut"     — Block A (12-week reset → Tenerife → charity ride): a
                    lean-mass-sparing fat-loss deficit for a returning 50+ athlete
                    with high body fat.
      - "perform" — Block B (Haute Route build): energy balance, no deliberate
                    deficit, key sessions fully fuelled.
    """
    existing = _load_nutrition_targets()
    missing = [(t, d) for t, d in sessions if f"{goal}_{t}_{d}" not in existing]
    if not missing:
        return existing

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return existing

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    from .history import latest_weight_kg
    weight = latest_weight_kg()
    weight_str = f"~{weight:.0f} kg body weight" if weight else "body weight unknown (assume ~90 kg)"

    if goal == "perform":
        goal_lines = [
            "Goal: ENERGY BALANCE to support a demanding multi-day alpine build — no deliberate "
            "deficit. Fully fuel the long and quality sessions; match intake to the day's load.",
        ]
    else:  # "cut"
        goal_lines = [
            "Goal: a MODERATE, LEAN-MASS-SPARING calorie deficit for steady fat loss (~0.5 kg/week) "
            "in a returning 50+ athlete with high body fat — the deficit is safe here given ample fat "
            "reserves. Keep protein high to protect muscle, concentrate carbohydrate around the long "
            "ride and quality sessions so they are NOT under-fuelled, and take the deficit mainly from "
            "rest/recovery days; keep the long-ride day close to energy balance.",
        ]

    from .nutrition_plan import protein_target_g
    pt = protein_target_g()

    lines = [
        f"You are a sports nutritionist for a male athlete aged 50+, {weight_str}.",
        *goal_lines,
        f"Protein target: at least {pt['low']}–{pt['high']} g/day ({pt['basis']}) to preserve muscle; "
        "distribute ~0.4 g/kg across 4+ meals plus a ~40 g pre-sleep casein/dairy dose.",
        "For each training session below provide TOTAL DAILY nutrition targets (all meals + snacks combined).",
        "Reply ONLY with valid JSON: a dict mapping session_key -> {\"kcal\": int, \"protein_g\": int, \"carbs_g\": int, \"fat_g\": int, \"brief\": \"one-sentence tip\"}",
        "No extra text, no markdown fences.",
        "",
        "Sessions (key: description, duration):",
    ]
    for stype, dur in missing:
        desc = _SESSION_TYPE_DESC.get(stype, stype)
        key = f"{goal}_{stype}_{dur}"
        dur_str = f"{dur} min" if dur > 0 else "no exercise"
        lines.append(f'"{key}": {desc}, {dur_str}')

    try:
        msg = client.messages.create(
            model=MODEL_FAST,
            max_tokens=3000,
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )
        import json as _json
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            raw = raw.rsplit("```", 1)[0].strip()
        result: dict = _json.loads(raw)
        with _conn() as con:
            _ensure_nutrition_schema(con)
            for key, data in result.items():
                if isinstance(data, dict):
                    con.execute(
                        """INSERT OR REPLACE INTO nutrition_targets
                           (session_key, kcal, protein_g, carbs_g, fat_g, brief)
                           VALUES (?,?,?,?,?,?)""",
                        (
                            key,
                            data.get("kcal"),
                            data.get("protein_g"),
                            data.get("carbs_g"),
                            data.get("fat_g"),
                            data.get("brief"),
                        ),
                    )
        existing.update(result)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("nutrition target generation failed: %s", exc)

    return existing


# ── In-session fuelling plans (carbs/hr, fluid, sodium during the ride) ───────

# Endurance session types where in-ride fuelling matters, and the minimum duration
# (minutes) below which fuelling is just water (no plan generated).
_FUEL_TYPES = {"long", "bike", "tempo", "ftp"}
_FUEL_MIN_DURATION = 75


def _ensure_fuelling_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS fuelling_plans (
            session_key      TEXT PRIMARY KEY,
            carbs_g_per_hr   INTEGER,
            total_carbs_g    INTEGER,
            fluid_ml_per_hr  INTEGER,
            sodium_mg_per_hr INTEGER,
            timeline         TEXT,
            brief            TEXT,
            generated_at     TEXT DEFAULT (datetime('now'))
        )
    """)


def _load_fuelling_plans() -> dict[str, dict]:
    with _conn() as con:
        _ensure_fuelling_schema(con)
        rows = con.execute("SELECT * FROM fuelling_plans").fetchall()
    return {r["session_key"]: dict(r) for r in rows}


def fuelling_session_key(stype: str, dur_min: int) -> str:
    """Single source of truth for fuelling_plans cache keys (plan type + planned minutes)."""
    return f"{stype}_{dur_min}"


def prefetch_fuelling_plans(sessions: list[tuple[str, int]], weight_kg: Optional[float] = None) -> dict[str, dict]:
    """Return {f"{type}_{dur}": fuelling_plan} for qualifying endurance sessions.

    Only generates for `_FUEL_TYPES` sessions ≥ `_FUEL_MIN_DURATION` minutes; shorter
    or non-endurance sessions don't need a structured in-ride plan. Mirrors the
    `prefetch_nutrition_targets` cache pattern (per session_key). Scales to rider
    weight: uses the passed `weight_kg`, else the latest measured weight, else
    falls back to 90 kg with a note when no body-comp data is available.
    """
    if weight_kg is None:
        from .history import latest_weight_kg
        weight_kg = latest_weight_kg()
    weight = weight_kg or 90.0
    weight_known = weight_kg is not None

    qualifying = [
        (t, d) for t, d in sessions
        if t in _FUEL_TYPES and d >= _FUEL_MIN_DURATION
    ]
    existing = _load_fuelling_plans()
    missing = [(t, d) for t, d in qualifying if fuelling_session_key(t, d) not in existing]
    if not missing:
        return existing

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return existing

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    weight_note = f"{weight:.0f} kg" + ("" if weight_known else " (assumed — no body-comp data)")
    lines = [
        f"You are a sports nutritionist planning IN-RIDE fuelling for a male cyclist, ~{weight_note}.",
        "These are targets for what to consume DURING the session itself (not daily meals).",
        "Use evidence-based ranges: ~60 g carbs/hr for rides 1–2.5 h, rising to 80–90 g/hr for "
        "longer/harder rides (use a 1:0.8 glucose:fructose mix above ~60 g/hr to raise the "
        "absorption ceiling); 500–750 ml fluid/hr; 300–700 mg sodium/hr depending on duration and "
        "intensity. The gut is trainable — bias the longest sessions toward the high end so the "
        "athlete rehearses event-day fuelling (90+ g/hr). Fuel these endurance sessions fully even "
        "during a weight-loss block: the deficit belongs to rest days, not the key ride.",
        "For each session provide a short hour-by-hour timeline (e.g. '0–60min: 1 bottle + 1 gel; ...').",
        "Reply ONLY with valid JSON: a dict mapping session_key -> "
        "{\"carbs_g_per_hr\": int, \"total_carbs_g\": int, \"fluid_ml_per_hr\": int, "
        "\"sodium_mg_per_hr\": int, \"timeline\": \"short string\", \"brief\": \"one-sentence tip\"}",
        "No extra text, no markdown fences.",
        "",
        "Sessions (key: description, duration):",
    ]
    for stype, dur in missing:
        desc = _SESSION_TYPE_DESC.get(stype, stype)
        key = fuelling_session_key(stype, dur)
        lines.append(f'"{key}": {desc}, {dur} min')

    try:
        msg = client.messages.create(
            model=MODEL_FAST,
            max_tokens=3000,
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )
        import json as _json
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            raw = raw.rsplit("```", 1)[0].strip()
        result: dict = _json.loads(raw)
        with _conn() as con:
            _ensure_fuelling_schema(con)
            for key, data in result.items():
                if isinstance(data, dict):
                    con.execute(
                        """INSERT OR REPLACE INTO fuelling_plans
                           (session_key, carbs_g_per_hr, total_carbs_g, fluid_ml_per_hr,
                            sodium_mg_per_hr, timeline, brief)
                           VALUES (?,?,?,?,?,?,?)""",
                        (
                            key,
                            data.get("carbs_g_per_hr"),
                            data.get("total_carbs_g"),
                            data.get("fluid_ml_per_hr"),
                            data.get("sodium_mg_per_hr"),
                            data.get("timeline"),
                            data.get("brief"),
                        ),
                    )
        existing.update({k: v for k, v in result.items() if isinstance(v, dict)})
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("fuelling plan generation failed: %s", exc)

    return existing


# ── Haute Route per-stage pacing & fuelling plans ────────────────────────────

def generate_hr_stage_plans() -> dict[int, dict]:
    """Return {stage_day: plan_dict} for the 7 Haute Route Alpes stages.

    Cached per stage in text_cache (key hr_stage_plan_v1_{day}, JSON string).
    When any stage is missing and an API key is set, makes ONE batched
    claude-sonnet-4-6 call for all missing stages, grounded in the athlete's
    latest LTHR and estimated FTP. Returns whatever is cached on failure.
    """
    import json as _json
    from .hr_plan import HR_EVENT_STAGES
    from .history import get_cached_text, set_cached_text, load_ftp_tests, latest_estimated_wkg

    plans: dict[int, dict] = {}
    missing: list[dict] = []
    for stage in HR_EVENT_STAGES:
        cached = get_cached_text(f"hr_stage_plan_v1_{stage['day']}")
        if cached:
            try:
                plans[stage["day"]] = _json.loads(cached)
                continue
            except Exception:
                pass
        missing.append(stage)

    if not missing:
        return plans
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return plans

    # Negative cache: after a failed generation, don't re-fire a blocking
    # Sonnet call on every page view — back off for an hour.
    _FAIL_KEY = "hr_stage_plan_fail_until"
    fail_until = get_cached_text(_FAIL_KEY)
    if fail_until:
        try:
            if datetime.now() < datetime.fromisoformat(fail_until):
                return plans
        except ValueError:
            pass

    # Athlete context: LTHR from the most recent FTP test, estimated FTP watts
    lthr_note = "LTHR unknown — express HR caps as % of LTHR"
    try:
        tests = load_ftp_tests()
        if tests and tests[-1].get("ftp_hr"):
            lthr_note = f"LTHR ≈ {tests[-1]['ftp_hr']} bpm (from FTP test {tests[-1]['date']})"
    except Exception:
        pass
    ftp_note = ""
    try:
        wkg = latest_estimated_wkg()
        if wkg:
            ftp_note = f" Estimated FTP ≈ {wkg['est_ftp_w']} W ({wkg['wkg']} W/kg) — estimate only, no power meter."
    except Exception:
        pass

    lines = [
        "Plan pacing and in-ride fuelling for each stage of the Haute Route Alpes "
        "(7-day amateur stage race, timed climbs, untimed descents).",
        f"Athlete: male, 50+, HR-based training (no power meter). {lthr_note}.{ftp_note}",
        "Key stage-race principles: the event is won in the final 3 stages, not the first 2 — "
        "cap effort on day 1–2 climbs; fuel from the first 30 minutes; respect altitude above 2000 m "
        "(HR runs higher for the same effort).",
        "Reply ONLY with valid JSON: a dict mapping stage day number (as string) -> "
        "{\"pacing\": \"2-3 sentence stage pacing strategy\", "
        "\"hr_cap_first_climb\": \"specific HR cap or %LTHR for the first climb\", "
        "\"carbs_g_per_hr\": int, \"total_carbs_g\": int, \"fluid_ml_per_hr\": int, "
        "\"brief\": \"one-sentence key reminder\"}",
        "No extra text, no markdown fences.",
        "",
        "Stages:",
    ]
    for s in missing:
        lines.append(
            f'Day {s["day"]}: {s["label"]} — {s["km"]} km, {s["elev_m"]} m climbing, '
            f'key climb {s["key_climb"]}'
        )

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=MODEL_SMART,
            max_tokens=3000,
            system=(
                "You are an experienced Haute Route coach who has guided many amateur "
                "riders through multi-day alpine stage races. Be specific and practical."
            ),
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            raw = raw.rsplit("```", 1)[0].strip()
        result: dict = _json.loads(raw)
        for day_str, plan in result.items():
            if not isinstance(plan, dict):
                continue
            try:
                day = int(day_str)
            except (TypeError, ValueError):
                continue
            set_cached_text(f"hr_stage_plan_v1_{day}", _json.dumps(plan))
            plans[day] = plan
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("HR stage plan generation failed: %s", exc)
        set_cached_text(_FAIL_KEY, (datetime.now() + timedelta(hours=1)).isoformat())

    return plans


def generate_charity_day_plans() -> dict[int, dict]:
    """Return {day_num: plan_dict} for the two Ghent→Amsterdam charity-ride days.

    Cached per day in text_cache (key charity_day_plan_v1_{day}, JSON string).
    When any day is missing and an API key is set, makes ONE batched
    claude-sonnet-4-6 call for all missing days, grounded in the athlete's
    latest LTHR and estimated FTP. Returns whatever is cached on failure.
    Mirrors generate_hr_stage_plans().
    """
    import json as _json
    from .plan import CHARITY_DAYS
    from .history import get_cached_text, set_cached_text, load_ftp_tests, latest_estimated_wkg

    plans: dict[int, dict] = {}
    missing: list[dict] = []
    for cd in CHARITY_DAYS:
        cached = get_cached_text(f"charity_day_plan_v1_{cd['day']}")
        if cached:
            try:
                plans[cd["day"]] = _json.loads(cached)
                continue
            except Exception:
                pass
        missing.append(cd)

    if not missing:
        return plans
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return plans

    # Negative cache: back off for an hour after a failed generation so we don't
    # re-fire a blocking Sonnet call on every calendar page view.
    _FAIL_KEY = "charity_day_plan_fail_until"
    fail_until = get_cached_text(_FAIL_KEY)
    if fail_until:
        try:
            if datetime.now() < datetime.fromisoformat(fail_until):
                return plans
        except ValueError:
            pass

    # Athlete context: LTHR from the most recent FTP test, estimated FTP watts
    lthr_note = "LTHR unknown — express HR caps as % of LTHR"
    try:
        tests = load_ftp_tests()
        if tests and tests[-1].get("ftp_hr"):
            lthr_note = f"LTHR ≈ {tests[-1]['ftp_hr']} bpm (from FTP test {tests[-1]['date']})"
    except Exception:
        pass
    ftp_note = ""
    try:
        wkg = latest_estimated_wkg()
        if wkg:
            ftp_note = f" Estimated FTP ≈ {wkg['est_ftp_w']} W ({wkg['wkg']} W/kg) — estimate only, no power meter."
    except Exception:
        pass

    lines = [
        "Plan pacing and in-ride fuelling for a 2-day supported charity cycling event "
        "(Ghent → Amsterdam, ~310 km total, flat-to-rolling, group riding).",
        f"Athlete: male, 50+, HR-based training (no power meter). {lthr_note}.{ftp_note}",
        "Critical context: the athlete's LONGEST training ride is ~5 hours, so Day 1 "
        "(190 km) exceeds the longest training ride by roughly 30–40%. Pacing and fuelling "
        "— not fitness — are the levers that determine whether Day 1 succeeds.",
        "Key principles to encode in the plan:",
        "- 2-day carb load beforehand at 8–10 g/kg/day.",
        "- Fuel from hour 1: 80–90 g carbs/hr the whole ride (gut already trained in the build).",
        "- 500–750 ml fluid/hr with 500–800 mg sodium/hr.",
        "- Day 1: ride the first 3 hours strictly below the Z2 ceiling — bank no early fatigue.",
        "- Day 2: legs will be tired from Day 1; start very easy and let them come good.",
        "Reply ONLY with valid JSON: a dict mapping day number (as string) -> "
        "{\"pacing\": \"3-4 sentence pacing strategy for the day\", "
        "\"hr_cap\": \"specific HR cap or %LTHR for the early hours\", "
        "\"carb_load\": \"1-2 sentence pre-event carb-load note for this day\", "
        "\"carbs_g_per_hr\": int, \"fluid_ml_per_hr\": int, \"sodium_mg_per_hr\": int, "
        "\"brief\": \"one-sentence key reminder\"}",
        "No extra text, no markdown fences.",
        "",
        "Days:",
    ]
    for cd in missing:
        lines.append(f'Day {cd["day"]}: {cd["label"]} — {cd["km"]} km')

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=MODEL_SMART,
            max_tokens=2000,
            system=(
                "You are an experienced endurance cycling coach fuelling an amateur "
                "through their first 2-day, 310 km charity ride. Be specific and practical."
            ),
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            raw = raw.rsplit("```", 1)[0].strip()
        result: dict = _json.loads(raw)
        for day_str, plan in result.items():
            if not isinstance(plan, dict):
                continue
            try:
                day = int(day_str)
            except (TypeError, ValueError):
                continue
            set_cached_text(f"charity_day_plan_v1_{day}", _json.dumps(plan))
            plans[day] = plan
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Charity day plan generation failed: %s", exc)
        set_cached_text(_FAIL_KEY, (datetime.now() + timedelta(hours=1)).isoformat())

    return plans
