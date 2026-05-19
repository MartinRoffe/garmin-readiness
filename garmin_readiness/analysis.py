"""Post-training analysis: fetch activity detail from Garmin and generate Claude commentary."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from .history import DB_PATH, _conn
from .plan import session_for_date

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


# ── Fetch detail from Garmin API ─────────────────────────────────────────────

def fetch_activity_detail(api: Any, activity_id: int) -> dict:
    """Return a merged dict of activity summary + HR zones."""
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

    return {
        "training_effect": s.get("trainingEffect"),
        "training_effect_label": s.get("trainingEffectLabel"),
        "aerobic_te_message": s.get("aerobicTrainingEffectMessage"),
        "anaerobic_te": s.get("anaerobicTrainingEffect"),
        "training_load": s.get("activityTrainingLoad"),
        "avg_respiration": s.get("avgRespirationRate"),
        "hr_zones": hr_zones,
    }


def save_detail(activity_id: int, detail: dict, analysis_text: str) -> None:
    with _conn() as con:
        _ensure_analysis_schema(con)
        con.execute(
            """INSERT OR REPLACE INTO activity_analyses
               (activity_id, hr_zones_json, training_effect, training_effect_label,
                aerobic_te_message, anaerobic_te, training_load, avg_respiration,
                analysis_text)
               VALUES (?,?,?,?,?,?,?,?,?)""",
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
    if d.get("hr_zones_json"):
        d["hr_zones"] = json.loads(d["hr_zones_json"])
    else:
        d["hr_zones"] = []
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


def generate_analysis(activity: dict, detail: dict) -> str:
    """Call Claude Haiku to analyse the workout and return a short commentary."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _rule_based_analysis(activity, detail)

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    prompt = _build_analysis_prompt(activity, detail)
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=(
                "You are an experienced cycling coach reviewing a completed training session. "
                "Be direct, specific, and evidence-based. Reference the numbers. "
                "No bullet markdown — short paragraphs only. Address the athlete as 'you'."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
    except Exception:
        return _rule_based_analysis(activity, detail)


def _build_analysis_prompt(activity: dict, detail: dict) -> str:
    act_date = activity.get("date", "")
    d_obj = date.fromisoformat(act_date) if act_date else None

    dur_fmt = _fmt_secs(int(activity.get("duration_seconds") or 0))
    dist_km = round((activity.get("distance_meters") or 0) / 1000, 1)
    avg_hr = activity.get("avg_hr")
    max_hr = activity.get("max_hr")
    calories = activity.get("calories")
    elev = activity.get("elevation_gain")
    name = activity.get("name") or activity.get("type_key", "ride")

    hr_zones = detail.get("hr_zones", [])
    zone_lines = []
    for z in hr_zones:
        bar = "█" * max(1, z["pct"] // 5)
        zone_lines.append(
            f"  Z{z['zone']} (≥{z['low_bpm']}bpm): {bar} {z['pct']}%  {_fmt_secs(z['secs'])}"
        )

    te = detail.get("training_effect")
    te_label = _te_label(detail.get("training_effect_label"))
    tl = detail.get("training_load")
    resp = detail.get("avg_respiration")
    aerobic_msg = (detail.get("aerobic_te_message") or "").replace("_", " ")

    # Planned session for comparison
    plan_line = ""
    if d_obj:
        session = session_for_date(d_obj)
        if session:
            stype, slabel, sdur = session
            plan_line = f"\nPlanned workout for this day: {slabel} ({stype}, {sdur}m)"

    lines = [
        f"Activity: {name}",
        f"Date: {act_date}",
        f"Duration: {dur_fmt}  Distance: {dist_km} km",
        f"Avg HR: {avg_hr} bpm  Max HR: {max_hr} bpm",
        f"Calories: {calories}  Elevation gain: {elev} m",
        f"Aerobic training effect: {te} ({te_label}) — {aerobic_msg}",
        f"Training load: {tl}",
        f"Avg respiration: {resp} breaths/min" if resp else "",
        "",
        "Heart rate zone distribution:",
        *zone_lines,
        plan_line,
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
    for act in activities:
        act_id = act["activity_id"]
        if load_analysis(act_id) is not None:
            continue  # already done
        try:
            detail = fetch_activity_detail(api, act_id)
            text = generate_analysis(act, detail)
            save_detail(act_id, detail, text)
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


# ── Workout descriptions (calendar modal coaching notes) ─────────────────────

# Exact step structure of each session type — mirrors workouts.py
_STEP_SUMMARIES: dict[str, str] = {
    "Easy Spin":        "10m warm-up → Z1–2 easy riding → 10m cool-down",
    "Zone 2 Steady":    "10m warm-up → sustained Z2 main block → 10m cool-down",
    "Recovery Spin":    "10m warm-up → Z1 only (very easy) → 10m cool-down",
    "Structured Z2":    "10m warm-up → 3 × (12m Z2 + 2m easy recovery) → 10m cool-down",
    "Z2 + Hills":       "10m warm-up → 20m Z2 → 4 × (3m Z3–4 hill effort + 3m Z1 recovery) → 6m cool-down",
    "Cadence Drills":   "10m warm-up → 5 × (3m at 90–110 rpm + 2m Z2) → 15m Z2 steady → 10m cool-down",
    "Hilly Z2":         "10m warm-up → Z2 riding on hilly terrain (Z3 accepted on climbs) → 10m cool-down",
    "Z2 Endurance":     "10m warm-up → sustained Z2 main block → 10m cool-down",
    "Low Cadence":      "10m warm-up → 5 × (4m at 60–70 rpm + 2m Z1 recovery) → 10m Z2 → 10m cool-down",
    "Easy Prep Ride":   "10m warm-up → Z1–2 very easy → 10m cool-down",
    "FTP Test":         "15m warm-up → 3m priming effort → 5m Z1 easy → 20-min all-out effort → 17m cool-down",
    "FTP Re-test":      "15m warm-up → 3m priming effort → 5m Z1 easy → 20-min all-out effort → 17m cool-down",
    "Final FTP Test":   "15m warm-up → 3m priming effort → 5m Z1 easy → 20-min all-out effort → 17m cool-down",
    "Tempo Intervals":  "15m warm-up → 3 × (10m Z4 + 5m Z1 recovery) → 5m cool-down",
    "Long Ride":        "15m warm-up → sustained Z2 main block → 15m cool-down",
    "Long Ride (Easy)": "15m warm-up → easy Z1–2 riding → 15m cool-down",
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
        "You are an experienced cycling coach. For each workout below write exactly 2 sentences:",
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
            model="claude-haiku-4-5-20251001",
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


def prefetch_nutrition_targets(sessions: list[tuple[str, int]]) -> dict[str, dict]:
    """Return {f"{type}_{dur}": {kcal, protein_g, carbs_g, fat_g, brief}} for every session."""
    existing = _load_nutrition_targets()
    missing = [(t, d) for t, d in sessions if f"{t}_{d}" not in existing]
    if not missing:
        return existing

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return existing

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    lines = [
        "You are a sports nutritionist for a male athlete aged 50+, ~85 kg body weight.",
        "Goal: support training performance AND maintain a small calorie deficit for ~0.5 kg/week weight loss.",
        "Protein target: 160 g+ on every day.",
        "For each training session below provide TOTAL DAILY nutrition targets (all meals + snacks combined).",
        "Reply ONLY with valid JSON: a dict mapping session_key -> {\"kcal\": int, \"protein_g\": int, \"carbs_g\": int, \"fat_g\": int, \"brief\": \"one-sentence tip\"}",
        "No extra text, no markdown fences.",
        "",
        "Sessions (key: description, duration):",
    ]
    for stype, dur in missing:
        desc = _SESSION_TYPE_DESC.get(stype, stype)
        key = f"{stype}_{dur}"
        dur_str = f"{dur} min" if dur > 0 else "no exercise"
        lines.append(f'"{key}": {desc}, {dur_str}')

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
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
