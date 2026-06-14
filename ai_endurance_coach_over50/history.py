"""SQLite persistence, baseline statistics, and z-score calculations."""
from __future__ import annotations

import math
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, fields
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from .metrics import DailyMetrics, TEXT_FIELDS

DB_PATH = Path.home() / ".ai_endurance_coach_over50" / "history.db"

# Garmin type_key values that satisfy each plan session type
ACTIVITY_MATCH: dict[str, set[str]] = {
    "bike":     {"road_biking", "cycling", "virtual_ride", "indoor_cycling", "mountain_biking"},
    "tempo":    {"road_biking", "cycling", "virtual_ride", "indoor_cycling", "mountain_biking"},
    "ftp":      {"road_biking", "cycling", "virtual_ride", "indoor_cycling", "mountain_biking"},
    "long":     {"road_biking", "cycling", "virtual_ride", "indoor_cycling", "mountain_biking"},
    "strength": {"strength_training", "stair_climbing", "fitness_equipment"},
    "ruck":     {"hiking", "walking", "trail_running", "running", "rucking", "load_carry"},
}

NUMERIC_FIELDS = [
    f.name
    for f in fields(DailyMetrics)
    if f.name not in TEXT_FIELDS
]

# Don't score these — context/baselines or timestamp fields, not daily readiness signals
_UNSCORED = {
    "training_load_chronic", "vo2_max", "total_steps", "active_calories",
    "calories_consumed", "calorie_goal", "calorie_goal_adjusted",
    "carbs_consumed", "protein_consumed",
    # acclimation + resting HR — consumed by illness/heat features, not the composite
    "heat_acclimation_pct", "altitude_acclimation", "resting_hr",
    # timestamps — large absolute values destroy z-score baseline
    "sleep_start_ts", "sleep_end_ts",
    # sleep detail — sleep_score already summarises these for the composite
    "deep_sleep_seconds", "light_sleep_seconds", "rem_sleep_seconds",
    "awake_sleep_seconds", "nap_time_seconds",
    "avg_spo2", "avg_respiration", "lowest_respiration", "highest_respiration",
}

SCORED_FIELDS = [f for f in NUMERIC_FIELDS if f not in _UNSCORED]

HIGHER_IS_BETTER = {
    "sleep_score", "sleep_seconds", "hrv_last_night", "hrv_weekly_avg",
    "body_battery_morning", "total_steps", "active_calories",
}
LOWER_IS_BETTER = {
    "avg_stress", "rest_stress", "acwr", "training_load_acute",
}


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # timeout: the FastAPI server (threadpool workers) and the launchd CLI can
    # write concurrently — wait out short lock contention instead of raising
    # "database is locked".
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _ensure_schema(con: sqlite3.Connection) -> None:
    # Base create
    numeric_cols = ", ".join(f"{name} REAL" for name in NUMERIC_FIELDS)
    text_extras = ", ".join(
        f"{name} TEXT" for name in TEXT_FIELDS if name != "date"
    )
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS daily_metrics (
            date TEXT PRIMARY KEY,
            {numeric_cols},
            {text_extras},
            recorded_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Migrate: add any columns missing from older schema versions
    existing = {row[1] for row in con.execute("PRAGMA table_info(daily_metrics)")}
    for name in NUMERIC_FIELDS:
        if name not in existing:
            con.execute(f"ALTER TABLE daily_metrics ADD COLUMN {name} REAL")
    for name in TEXT_FIELDS:
        if name != "date" and name not in existing:
            con.execute(f"ALTER TABLE daily_metrics ADD COLUMN {name} TEXT")


_ACTIVITY_COLS: list[tuple[str, str]] = [
    ("activity_id",            "INTEGER PRIMARY KEY"),
    ("date",                   "TEXT NOT NULL"),
    ("start_time",             "TEXT"),
    ("name",                   "TEXT"),
    ("type_key",               "TEXT"),
    ("duration_seconds",       "REAL"),
    ("distance_meters",        "REAL"),
    ("elevation_gain",         "REAL"),
    ("elevation_loss",         "REAL"),
    ("avg_hr",                 "REAL"),
    ("max_hr",                 "REAL"),
    ("calories",               "REAL"),
    ("avg_speed_ms",           "REAL"),
    ("max_speed_ms",           "REAL"),
    ("moving_duration",        "REAL"),
    ("aerobic_te",             "REAL"),
    ("anaerobic_te",           "REAL"),
    ("training_load",          "REAL"),
    ("training_effect_label",  "TEXT"),
    ("avg_respiration",        "REAL"),
    ("min_temperature",        "REAL"),
    ("max_temperature",        "REAL"),
    ("location_name",          "TEXT"),
    ("vigorous_intensity_min", "INTEGER"),
    ("moderate_intensity_min", "INTEGER"),
    ("hr_zone_1_sec",          "REAL"),
    ("hr_zone_2_sec",          "REAL"),
    ("hr_zone_3_sec",          "REAL"),
    ("hr_zone_4_sec",          "REAL"),
    ("hr_zone_5_sec",          "REAL"),
]


def _ensure_activities_schema(con: sqlite3.Connection) -> None:
    col_defs = ", ".join(f"{name} {typ}" for name, typ in _ACTIVITY_COLS)
    con.execute(f"CREATE TABLE IF NOT EXISTS activities ({col_defs})")
    existing = {row[1] for row in con.execute("PRAGMA table_info(activities)")}
    for name, typ in _ACTIVITY_COLS:
        if name not in existing and name != "activity_id":
            base_type = typ.split()[0]
            con.execute(f"ALTER TABLE activities ADD COLUMN {name} {base_type}")


def get_cached_text(key: str) -> Optional[str]:
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS text_cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                recorded_at TEXT DEFAULT (datetime('now'))
            )
        """)
        row = con.execute("SELECT value FROM text_cache WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_cached_text(key: str, value: str) -> None:
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS text_cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                recorded_at TEXT DEFAULT (datetime('now'))
            )
        """)
        con.execute("INSERT OR REPLACE INTO text_cache (key, value) VALUES (?, ?)", (key, value))


def save_advice(target_date: date, text: str) -> None:
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS daily_advice (
                date TEXT PRIMARY KEY,
                advice TEXT NOT NULL,
                recorded_at TEXT DEFAULT (datetime('now'))
            )
        """)
        con.execute(
            "INSERT OR REPLACE INTO daily_advice (date, advice) VALUES (?, ?)",
            (target_date.isoformat(), text),
        )


def delete_advice(target_date: date) -> None:
    with _conn() as con:
        con.execute("DELETE FROM daily_advice WHERE date = ?", (target_date.isoformat(),))


def load_advice(target_date: date) -> Optional[str]:
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS daily_advice (
                date TEXT PRIMARY KEY,
                advice TEXT NOT NULL,
                recorded_at TEXT DEFAULT (datetime('now'))
            )
        """)
        row = con.execute(
            "SELECT advice FROM daily_advice WHERE date = ?",
            (target_date.isoformat(),),
        ).fetchone()
    return row["advice"] if row else None


def save_activities(activities: list[dict]) -> None:
    cols = [name for name, _ in _ACTIVITY_COLS]
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    with _conn() as con:
        _ensure_activities_schema(con)
        for a in activities:
            values = [a.get(name) for name in cols]
            con.execute(
                f"INSERT OR REPLACE INTO activities ({col_list}) VALUES ({placeholders})",
                values,
            )


def load_recent_activities(days: int = 7) -> list[dict]:
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    with _conn() as con:
        _ensure_activities_schema(con)
        rows = con.execute(
            "SELECT * FROM activities WHERE date >= ? ORDER BY start_time DESC",
            (start,),
        ).fetchall()
    return [dict(r) for r in rows]


_ZONE_BIKE_KEYS = {"road_biking", "cycling", "virtual_ride", "indoor_cycling", "mountain_biking"}


def zone_distribution(days: int = 7) -> Optional[dict]:
    """Aggregate HR zone distribution across cycling activities for the last `days` days.

    Returns zone percentages and totals, or None if no zone data is available.
    """
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    placeholders = ",".join("?" * len(_ZONE_BIKE_KEYS))
    with _conn() as con:
        _ensure_activities_schema(con)
        rows = con.execute(
            f"""SELECT hr_zone_1_sec, hr_zone_2_sec, hr_zone_3_sec, hr_zone_4_sec, hr_zone_5_sec
               FROM activities
               WHERE date >= ? AND type_key IN ({placeholders})""",
            (start, *_ZONE_BIKE_KEYS),
        ).fetchall()

    z = [0.0] * 5
    count = 0
    for row in rows:
        vals = [row[f"hr_zone_{i}_sec"] or 0 for i in range(1, 6)]
        if any(v > 0 for v in vals):
            for i, v in enumerate(vals):
                z[i] += v
            count += 1

    total = sum(z)
    if total == 0 or count == 0:
        return None

    return {
        "z1_pct": round(z[0] / total * 100, 1),
        "z2_pct": round(z[1] / total * 100, 1),
        "z3_pct": round(z[2] / total * 100, 1),
        "z4_pct": round(z[3] / total * 100, 1),
        "z5_pct": round(z[4] / total * 100, 1),
        "total_min": round(total / 60),
        "activity_count": count,
    }


def load_activities_by_date(start: date, end: date) -> dict[str, list[dict]]:
    """Return {date_str: [activity, ...]} for all activities in [start, end]."""
    with _conn() as con:
        _ensure_activities_schema(con)
        rows = con.execute(
            "SELECT * FROM activities WHERE date >= ? AND date <= ? ORDER BY start_time",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    result: dict[str, list[dict]] = {}
    for r in rows:
        d = dict(r)
        result.setdefault(d["date"], []).append(d)
    return result


def save(m: DailyMetrics) -> None:
    with _conn() as con:
        _ensure_schema(con)
        data = asdict(m)
        data["date"] = m.date.isoformat()
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        con.execute(
            f"INSERT OR REPLACE INTO daily_metrics ({cols}) VALUES ({placeholders})",
            list(data.values()),
        )


def load(target_date: date) -> Optional[DailyMetrics]:
    with _conn() as con:
        _ensure_schema(con)
        row = con.execute(
            "SELECT * FROM daily_metrics WHERE date = ?",
            (target_date.isoformat(),),
        ).fetchone()
    if row is None:
        return None
    known = {f.name for f in fields(DailyMetrics)}
    kwargs = {k: row[k] for k in row.keys() if k in known}
    kwargs["date"] = date.fromisoformat(kwargs["date"])
    return DailyMetrics(**kwargs)


def _stats_from_rows(rows) -> dict[str, tuple[float, float]]:
    """{field: (mean, std)} for scored fields across the given daily_metrics rows.

    Uses population std (÷n, matching pstdev elsewhere in the app) — slightly
    tight at small n, but every z-threshold is calibrated against this, so
    don't switch to sample std without recalibrating.
    """
    stats: dict[str, tuple[float, float]] = {}
    for field in SCORED_FIELDS:
        values = [row[field] for row in rows if row[field] is not None]
        if len(values) < 3:
            continue
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = math.sqrt(variance)
        if std > 0:
            stats[field] = (mean, std)
    return stats


def baseline_stats(
    reference_date: date,
    window_days: int = 30,
) -> dict[str, tuple[float, float]]:
    """Returns {field_name: (mean, std)} for scored fields in the window before reference_date."""
    start = (reference_date - timedelta(days=window_days)).isoformat()
    end = (reference_date - timedelta(days=1)).isoformat()

    with _conn() as con:
        _ensure_schema(con)
        rows = con.execute(
            "SELECT * FROM daily_metrics WHERE date >= ? AND date <= ? ORDER BY date",
            (start, end),
        ).fetchall()
    return _stats_from_rows(rows)


def z_score(value: float, mean: float, std: float, field: str) -> float:
    """Signed z-score oriented so positive = better readiness."""
    z = (value - mean) / std
    if field in LOWER_IS_BETTER:
        z = -z
    return z


def composite_score(m: DailyMetrics, stats: dict[str, tuple[float, float]]) -> Optional[float]:
    """Mean z-score across available scored metrics that have a baseline."""
    z_scores = []
    for field in SCORED_FIELDS:
        value = getattr(m, field)
        if value is None or field not in stats:
            continue
        mean, std = stats[field]
        z_scores.append(z_score(value, mean, std, field))
    if not z_scores:
        return None
    return sum(z_scores) / len(z_scores)


def history_for_chart(days: int = 14) -> list[tuple[date, Optional[float]]]:
    """Composite score per day, computed from ONE windowed query (the old
    per-day load() + baseline_stats() pair was ~2(days+1) queries on the
    dashboard hot path)."""
    end = date.today()
    start = end - timedelta(days=days)
    window_start = start - timedelta(days=30)  # covers the oldest day's baseline

    with _conn() as con:
        _ensure_schema(con)
        rows = con.execute(
            "SELECT * FROM daily_metrics WHERE date >= ? AND date <= ? ORDER BY date",
            (window_start.isoformat(), end.isoformat()),
        ).fetchall()
    by_date = {r["date"]: r for r in rows}
    known = {f.name for f in fields(DailyMetrics)}

    results = []
    for i in range(days + 1):
        d = start + timedelta(days=i)
        row = by_date.get(d.isoformat())
        if row is None:
            results.append((d, None))
            continue
        b_start = (d - timedelta(days=30)).isoformat()
        b_end = (d - timedelta(days=1)).isoformat()
        stats = _stats_from_rows([r for r in rows if b_start <= r["date"] <= b_end])
        kwargs = {k: row[k] for k in row.keys() if k in known}
        kwargs["date"] = d
        results.append((d, composite_score(DailyMetrics(**kwargs), stats)))
    return results


def seven_day_composite_trend_csv() -> str:
    """Comma-separated composite σ for the last 7 days (oldest→today), same as email prompt."""
    history = history_for_chart(days=7)
    return ", ".join(f"{v:+.2f}" if v is not None else "—" for _, v in history)


def raw_history(days: int = 14) -> list[dict]:
    """Return a list of dicts (one per day, oldest first) for the last `days` days.

    Each dict has: date (date), hrv_last_night, sleep_score, avg_stress (all may be None).
    Days with no DB row still appear with None values so the sparkline x-axis is continuous.
    """
    end = date.today()
    start = end - timedelta(days=days - 1)
    with _conn() as con:
        _ensure_schema(con)
        rows = con.execute(
            """SELECT date, hrv_last_night, sleep_score, avg_stress, rest_stress,
                      resting_hr, total_steps, active_calories,
                      calories_consumed, calorie_goal, calorie_goal_adjusted,
                      carbs_consumed, protein_consumed
               FROM daily_metrics
               WHERE date >= ? AND date <= ?
               ORDER BY date""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    by_date = {row["date"]: dict(row) for row in rows}
    result = []
    for i in range(days):
        d = start + timedelta(days=i)
        row = by_date.get(d.isoformat(), {})
        result.append({
            "date": d,
            "hrv_last_night":          row.get("hrv_last_night"),
            "sleep_score":             row.get("sleep_score"),
            "avg_stress":              row.get("avg_stress"),
            "rest_stress":             row.get("rest_stress"),
            "resting_hr":              row.get("resting_hr"),
            "total_steps":             row.get("total_steps"),
            "active_calories":         row.get("active_calories"),
            "calories_consumed":       row.get("calories_consumed"),
            "calorie_goal":            row.get("calorie_goal"),
            "calorie_goal_adjusted":   row.get("calorie_goal_adjusted"),
            "carbs_consumed":          row.get("carbs_consumed"),
            "protein_consumed":        row.get("protein_consumed"),
        })
    return result


def sleep_history(days: int = 30) -> list[dict]:
    """Return one dict per day (oldest first) for the last `days` days.

    Hours are pre-computed floats; missing nights appear with None values so
    chart x-axes stay continuous.
    """
    end = date.today()
    start = end - timedelta(days=days - 1)
    with _conn() as con:
        _ensure_schema(con)
        rows = con.execute(
            """SELECT date, sleep_score, sleep_seconds,
                      deep_sleep_seconds, light_sleep_seconds,
                      rem_sleep_seconds, awake_sleep_seconds,
                      nap_time_seconds, sleep_start_ts, sleep_end_ts,
                      avg_spo2, avg_respiration,
                      lowest_respiration, highest_respiration,
                      hrv_last_night
               FROM daily_metrics
               WHERE date >= ? AND date <= ?
               ORDER BY date""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    by_date = {row["date"]: dict(row) for row in rows}

    def _hrs(secs):
        return round(secs / 3600, 2) if secs is not None else None

    result = []
    for i in range(days):
        d = start + timedelta(days=i)
        r = by_date.get(d.isoformat(), {})
        total_secs = r.get("sleep_seconds")
        deep_s  = r.get("deep_sleep_seconds")
        light_s = r.get("light_sleep_seconds")
        rem_s   = r.get("rem_sleep_seconds")
        awake_s = r.get("awake_sleep_seconds")
        result.append({
            "date":              d.isoformat(),
            "label":             d.strftime("%-d %b"),
            "sleep_score":       r.get("sleep_score"),
            "sleep_hours":       _hrs(total_secs),
            "deep_hours":        _hrs(deep_s),
            "light_hours":       _hrs(light_s),
            "rem_hours":         _hrs(rem_s),
            "awake_hours":       _hrs(awake_s),
            "nap_min":           round(r["nap_time_seconds"] / 60) if r.get("nap_time_seconds") else None,
            "spo2":              r.get("avg_spo2"),
            "respiration":       r.get("avg_respiration"),
            "lowest_respiration":  r.get("lowest_respiration"),
            "highest_respiration": r.get("highest_respiration"),
            "hrv":               r.get("hrv_last_night"),
            "deep_pct":          round(deep_s / total_secs * 100) if deep_s and total_secs else None,
            "rem_pct":           round(rem_s  / total_secs * 100) if rem_s  and total_secs else None,
        })
    return result


def _ensure_body_metrics_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS body_metrics (
            date TEXT PRIMARY KEY,
            weight_kg REAL,
            fat_pct REAL,
            muscle_mass_kg REAL,
            bone_mass_kg REAL,
            hydration_pct REAL,
            visceral_fat REAL,
            bmi REAL,
            metabolic_age REAL,
            recorded_at TEXT DEFAULT (datetime('now'))
        )
    """)


def _ensure_blood_pressure_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS blood_pressure (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            timestamp_local TEXT,
            systolic INTEGER,
            diastolic INTEGER,
            pulse INTEGER,
            recorded_at TEXT DEFAULT (datetime('now')),
            UNIQUE(date, timestamp_local)
        )
    """)


def save_body_metrics(readings: list[dict]) -> None:
    with _conn() as con:
        _ensure_body_metrics_schema(con)
        for r in readings:
            con.execute("""
                INSERT OR REPLACE INTO body_metrics
                    (date, weight_kg, fat_pct, muscle_mass_kg, bone_mass_kg,
                     hydration_pct, visceral_fat, bmi, metabolic_age)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                r["date"], r.get("weight_kg"), r.get("fat_pct"),
                r.get("muscle_mass_kg"), r.get("bone_mass_kg"),
                r.get("hydration_pct"), r.get("visceral_fat"),
                r.get("bmi"), r.get("metabolic_age"),
            ))


def load_body_metrics(days: int = 90) -> list[dict]:
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    with _conn() as con:
        _ensure_body_metrics_schema(con)
        rows = con.execute(
            "SELECT * FROM body_metrics WHERE date >= ? ORDER BY date",
            (start,),
        ).fetchall()
    return [dict(r) for r in rows]


def save_blood_pressure(readings: list[dict]) -> None:
    with _conn() as con:
        _ensure_blood_pressure_schema(con)
        for r in readings:
            con.execute("""
                INSERT OR REPLACE INTO blood_pressure
                    (date, timestamp_local, systolic, diastolic, pulse)
                VALUES (?,?,?,?,?)
            """, (
                r["date"], r.get("timestamp_local"),
                r.get("systolic"), r.get("diastolic"), r.get("pulse"),
            ))


def load_blood_pressure(days: int = 90) -> list[dict]:
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    with _conn() as con:
        _ensure_blood_pressure_schema(con)
        rows = con.execute(
            "SELECT * FROM blood_pressure WHERE date >= ? ORDER BY timestamp_local",
            (start,),
        ).fetchall()
    return [dict(r) for r in rows]


def pmc_history(days: int = 90) -> list[dict]:
    """Return daily CTL/ATL/TSB for the last `days` days (oldest first).

    Uses Garmin's pre-computed acute (≈7d ATL) and chronic (≈28d CTL) training
    load values. TSB = CTL − ATL. All values are in Garmin training-load units
    (not Coggan TSS) so absolute thresholds from TrainingPeaks do not apply.
    """
    end = date.today()
    start = end - timedelta(days=days - 1)
    with _conn() as con:
        _ensure_schema(con)
        rows = con.execute(
            """SELECT date, training_load_acute, training_load_chronic
               FROM daily_metrics
               WHERE date >= ? AND date <= ?
               ORDER BY date""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()

    by_date = {row["date"]: row for row in rows}
    result = []
    for i in range(days):
        d = start + timedelta(days=i)
        row = by_date.get(d.isoformat())
        atl = row["training_load_acute"] if row else None
        ctl = row["training_load_chronic"] if row else None
        tsb = round(ctl - atl, 1) if (ctl is not None and atl is not None) else None
        result.append({
            "date": d.isoformat(),
            "label": d.strftime("%-d %b"),
            "atl": round(atl, 1) if atl is not None else None,
            "ctl": round(ctl, 1) if ctl is not None else None,
            "tsb": tsb,
        })
    return result


def vo2_history(days: int = 90) -> list[dict]:
    """Return daily VO2 max readings for the last `days` days (oldest first)."""
    end = date.today()
    start = end - timedelta(days=days - 1)
    with _conn() as con:
        _ensure_schema(con)
        rows = con.execute(
            "SELECT date, vo2_max FROM daily_metrics WHERE date >= ? AND date <= ? ORDER BY date",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    by_date = {row["date"]: row["vo2_max"] for row in rows}
    result = []
    for i in range(days):
        d = start + timedelta(days=i)
        v = by_date.get(d.isoformat())
        result.append({
            "date": d.isoformat(),
            "label": d.strftime("%-d %b"),
            "vo2_max": round(v, 1) if v is not None else None,
        })
    return result


# ── Coach chat & plan overrides ──────────────────────────────────────────────

def _ensure_coach_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS coach_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            proposal TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS plan_overrides (
            date TEXT PRIMARY KEY,
            session_type TEXT,
            label TEXT,
            duration_min INTEGER NOT NULL,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)


def save_coach_message(role: str, content: str, proposal_json: Optional[str] = None) -> None:
    with _conn() as con:
        _ensure_coach_schema(con)
        con.execute(
            "INSERT INTO coach_conversations (role, content, proposal) VALUES (?, ?, ?)",
            (role, content, proposal_json),
        )


def load_coach_history(limit: int = 30) -> list[dict]:
    with _conn() as con:
        _ensure_coach_schema(con)
        rows = con.execute(
            "SELECT id, role, content, proposal, created_at FROM coach_conversations ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def clear_coach_history() -> None:
    with _conn() as con:
        _ensure_coach_schema(con)
        con.execute("DELETE FROM coach_conversations")


def set_plan_override(date_str: str, session_type: str, label: str, duration_min: int, note: str = "") -> None:
    with _conn() as con:
        _ensure_coach_schema(con)
        con.execute(
            """INSERT OR REPLACE INTO plan_overrides (date, session_type, label, duration_min, note)
               VALUES (?, ?, ?, ?, ?)""",
            (date_str, session_type, label, duration_min, note),
        )


def get_plan_override(date_str: str) -> Optional[dict]:
    with _conn() as con:
        _ensure_coach_schema(con)
        row = con.execute(
            "SELECT * FROM plan_overrides WHERE date = ?", (date_str,)
        ).fetchone()
    return dict(row) if row else None


def list_plan_overrides() -> list[dict]:
    with _conn() as con:
        _ensure_coach_schema(con)
        rows = con.execute("SELECT * FROM plan_overrides ORDER BY date").fetchall()
    return [dict(r) for r in rows]


def delete_plan_override(date_str: str) -> None:
    with _conn() as con:
        _ensure_coach_schema(con)
        con.execute("DELETE FROM plan_overrides WHERE date = ?", (date_str,))


# ── Coach memory ──────────────────────────────────────────────────────────────

def _ensure_coach_memory_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS coach_memory (
            id INTEGER PRIMARY KEY,
            memo TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)


def _ensure_rpe_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS session_rpe (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            activity_id INTEGER,
            rpe INTEGER NOT NULL,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)


def save_session_rpe(activity_date: str, activity_id: Optional[int], rpe: int, note: Optional[str] = None) -> None:
    with _conn() as con:
        _ensure_rpe_schema(con)
        if activity_id is not None:
            con.execute(
                "INSERT OR REPLACE INTO session_rpe (date, activity_id, rpe, note) VALUES (?,?,?,?)",
                (activity_date, activity_id, rpe, note),
            )
        else:
            con.execute("DELETE FROM session_rpe WHERE date = ? AND activity_id IS NULL", (activity_date,))
            con.execute(
                "INSERT INTO session_rpe (date, activity_id, rpe, note) VALUES (?,?,?,?)",
                (activity_date, None, rpe, note),
            )


def load_session_rpe(days: int = 14) -> list[dict]:
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    with _conn() as con:
        _ensure_rpe_schema(con)
        rows = con.execute(
            "SELECT * FROM session_rpe WHERE date >= ? ORDER BY date DESC, created_at DESC",
            (start,),
        ).fetchall()
    return [dict(r) for r in rows]


def _ensure_ftp_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS ftp_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            activity_id INTEGER,
            ftp_hr INTEGER,
            ftp_hr_max INTEGER,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)


def save_ftp_test(test_date: str, activity_id: Optional[int], ftp_hr: Optional[int],
                  ftp_hr_max: Optional[int], note: Optional[str] = None) -> None:
    with _conn() as con:
        _ensure_ftp_schema(con)
        con.execute(
            "INSERT OR IGNORE INTO ftp_tests (date, activity_id, ftp_hr, ftp_hr_max, note) VALUES (?,?,?,?,?)",
            (test_date, activity_id, ftp_hr, ftp_hr_max, note),
        )


def load_ftp_tests() -> list[dict]:
    with _conn() as con:
        _ensure_ftp_schema(con)
        rows = con.execute("SELECT * FROM ftp_tests ORDER BY date ASC").fetchall()
    return [dict(r) for r in rows]


def intensity_distribution_by_week(start: date, end: date) -> list[dict]:
    """Aggregate HR zone distribution per ISO week across all cycling activities in [start, end]."""
    placeholders = ",".join("?" * len(_ZONE_BIKE_KEYS))
    with _conn() as con:
        _ensure_activities_schema(con)
        rows = con.execute(
            f"""SELECT strftime('%Y-W%W', date) AS week_label,
                       SUM(hr_zone_1_sec) AS z1, SUM(hr_zone_2_sec) AS z2,
                       SUM(hr_zone_3_sec) AS z3, SUM(hr_zone_4_sec) AS z4,
                       SUM(hr_zone_5_sec) AS z5, COUNT(*) AS activity_count
                FROM activities
                WHERE date >= ? AND date <= ? AND type_key IN ({placeholders})
                GROUP BY week_label
                ORDER BY week_label ASC""",
            (start.isoformat(), end.isoformat(), *_ZONE_BIKE_KEYS),
        ).fetchall()

    result = []
    for row in rows:
        z = [row[f"z{i}"] or 0.0 for i in range(1, 6)]
        total = sum(z)
        if total == 0:
            continue
        result.append({
            "week_label": row["week_label"],
            "z1_pct": round(z[0] / total * 100, 1),
            "z2_pct": round(z[1] / total * 100, 1),
            "z3_pct": round(z[2] / total * 100, 1),
            "z4_pct": round(z[3] / total * 100, 1),
            "z5_pct": round(z[4] / total * 100, 1),
            "total_min": round(total / 60),
            "activity_count": row["activity_count"],
            "z1_sec": z[0], "z2_sec": z[1], "z3_sec": z[2], "z4_sec": z[3], "z5_sec": z[4],
        })
    return result


def _ensure_btb_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS btb_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            day_number INTEGER NOT NULL,
            fatigue_rating INTEGER,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)


def save_btb_note(btb_date: str, day_number: int, fatigue_rating: Optional[int], note: Optional[str] = None) -> None:
    with _conn() as con:
        _ensure_btb_schema(con)
        con.execute("DELETE FROM btb_notes WHERE date = ?", (btb_date,))
        con.execute(
            "INSERT INTO btb_notes (date, day_number, fatigue_rating, note) VALUES (?,?,?,?)",
            (btb_date, day_number, fatigue_rating, note),
        )


_BTB_BIKE_KEYS = {"road_biking", "cycling", "virtual_ride", "indoor_cycling", "mountain_biking"}


def load_btb_summary() -> list[dict]:
    """Find consecutive cycling day pairs and return them with fatigue notes."""
    with _conn() as con:
        _ensure_activities_schema(con)
        _ensure_btb_schema(con)
        placeholders = ",".join("?" * len(_BTB_BIKE_KEYS))
        rows = con.execute(
            f"""SELECT date, AVG(avg_hr) AS avg_hr, SUM(duration_seconds) AS total_secs
                FROM activities
                WHERE type_key IN ({placeholders})
                GROUP BY date
                ORDER BY date DESC""",
            (*_BTB_BIKE_KEYS,),
        ).fetchall()
        btb_notes_rows = con.execute("SELECT * FROM btb_notes ORDER BY date").fetchall()

    btb_notes = {r["date"]: dict(r) for r in btb_notes_rows}
    cycling_dates = [dict(r) for r in rows]  # newest first

    pairs = []
    for i in range(len(cycling_dates) - 1):
        day2 = cycling_dates[i]
        day1 = cycling_dates[i + 1]
        # Check they are consecutive calendar days
        try:
            d2 = date.fromisoformat(day2["date"])
            d1 = date.fromisoformat(day1["date"])
            if (d2 - d1).days != 1:
                continue
        except Exception:
            continue
        n1 = btb_notes.get(day1["date"], {})
        n2 = btb_notes.get(day2["date"], {})
        pairs.append({
            "date1": day1["date"],
            "date2": day2["date"],
            "avg_hr_1": round(day1["avg_hr"]) if day1["avg_hr"] else None,
            "avg_hr_2": round(day2["avg_hr"]) if day2["avg_hr"] else None,
            "dur_min_1": round((day1["total_secs"] or 0) / 60),
            "dur_min_2": round((day2["total_secs"] or 0) / 60),
            "fatigue_rating_1": n1.get("fatigue_rating"),
            "fatigue_rating_2": n2.get("fatigue_rating"),
            "note_1": n1.get("note"),
            "note_2": n2.get("note"),
        })
    return pairs[:10]


# ── Foster monotony & strain ─────────────────────────────────────────────────

def weekly_monotony_strain(weeks: int = 8) -> list[dict]:
    """Foster training monotony & strain, one dict per Mon–Sun week (oldest first).

    daily_load = SUM(activities.training_load) per day; days with no training
    count as 0.0 (monotony is about load *variability* across all 7 days).
    monotony = mean(daily) / pstdev(daily)  (None when stdev == 0)
    strain   = weekly_load * monotony
    Falls back to duration_seconds/60 for trained days with no training_load.
    """
    import statistics

    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    start = this_monday - timedelta(weeks=weeks - 1)
    with _conn() as con:
        _ensure_activities_schema(con)
        rows = con.execute(
            """SELECT date,
                      SUM(training_load) AS load,
                      SUM(duration_seconds) AS secs
               FROM activities
               WHERE date >= ?
               GROUP BY date""",
            (start.isoformat(),),
        ).fetchall()

    daily_load: dict[str, float] = {}
    for r in rows:
        load = r["load"]
        if load is None and r["secs"]:
            load = r["secs"] / 60.0  # fallback: minutes as load proxy
        daily_load[r["date"]] = float(load or 0.0)

    result = []
    for w in range(weeks):
        week_start = start + timedelta(weeks=w)
        days = [week_start + timedelta(days=i) for i in range(7)]
        loads = [daily_load.get(d.isoformat(), 0.0) for d in days]
        # For the current (incomplete) week, only use elapsed days
        if week_start == this_monday:
            elapsed = (today - week_start).days + 1
            loads = loads[:elapsed]
        if not loads:
            continue
        weekly_load = sum(loads)
        mean = statistics.mean(loads)
        stdev = statistics.pstdev(loads)
        monotony = round(mean / stdev, 2) if stdev > 0 else None
        strain = round(weekly_load * monotony) if monotony is not None else None
        result.append({
            "week_start": week_start,
            "label": week_start.strftime("%-d %b"),
            "weekly_load": round(weekly_load),
            "monotony": monotony,
            "strain": strain,
            "days_trained": sum(1 for v in loads if v > 0),
        })
    return result


# ── Durability (late-ride HR drift) ──────────────────────────────────────────

def _ensure_durability_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS activity_durability (
            activity_id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            duration_min INTEGER,
            first_third_hr REAL,
            final_third_hr REAL,
            drift_pct REAL,
            n_laps INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)


def save_durability(activity_id: int, row: dict) -> None:
    with _conn() as con:
        _ensure_durability_schema(con)
        con.execute(
            """INSERT OR REPLACE INTO activity_durability
               (activity_id, date, duration_min, first_third_hr, final_third_hr, drift_pct, n_laps)
               VALUES (?,?,?,?,?,?,?)""",
            (activity_id, row["date"], row.get("duration_min"),
             row.get("first_third_hr"), row.get("final_third_hr"),
             row.get("drift_pct"), row.get("n_laps")),
        )


def load_durability(days: int = 180) -> list[dict]:
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    with _conn() as con:
        _ensure_durability_schema(con)
        rows = con.execute(
            "SELECT * FROM activity_durability WHERE date >= ? ORDER BY date ASC",
            (start,),
        ).fetchall()
    return [dict(r) for r in rows]


def durability_exists(activity_id: int) -> bool:
    with _conn() as con:
        _ensure_durability_schema(con)
        row = con.execute(
            "SELECT 1 FROM activity_durability WHERE activity_id = ?", (activity_id,)
        ).fetchone()
    return row is not None


# ── Estimated W/kg (no power meter — ACSM estimate from VO2max + weight) ─────

def estimated_wkg_history(days: int = 180) -> list[dict]:
    """Estimated FTP watts and W/kg per day with a VO2max reading.

    p_vo2max = (vo2max − 7) × weight_kg / 10.8   (ACSM cycling formula)
    est_ftp_w = 0.80 × p_vo2max
    Weight is carried forward from the most recent body_metrics reading.
    """
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    with _conn() as con:
        _ensure_schema(con)
        _ensure_body_metrics_schema(con)
        vo2_rows = con.execute(
            "SELECT date, vo2_max FROM daily_metrics WHERE date >= ? AND vo2_max IS NOT NULL ORDER BY date",
            (start,),
        ).fetchall()
        weight_rows = con.execute(
            "SELECT date, weight_kg FROM body_metrics WHERE weight_kg IS NOT NULL ORDER BY date",
        ).fetchall()

    weights = [(r["date"], float(r["weight_kg"])) for r in weight_rows]
    result = []
    for r in vo2_rows:
        d_iso = r["date"]
        weight = None
        for wd, wv in weights:
            if wd <= d_iso:
                weight = wv
            else:
                break
        if weight is None or weight <= 0:
            continue
        vo2 = float(r["vo2_max"])
        p_vo2max = (vo2 - 7.0) * weight / 10.8
        est_ftp_w = 0.80 * p_vo2max
        d = date.fromisoformat(d_iso)
        result.append({
            "date": d_iso,
            "label": d.strftime("%-d %b"),
            "vo2_max": vo2,
            "weight_kg": round(weight, 1),
            "est_ftp_w": round(est_ftp_w),
            "wkg": round(est_ftp_w / weight, 2),
        })
    return result


def latest_estimated_wkg() -> Optional[dict]:
    hist = estimated_wkg_history(180)
    return hist[-1] if hist else None


# ── Latest bodyweight / lean mass (single source of truth for nutrition) ─────

def latest_weight_kg() -> Optional[float]:
    """Most recent measured bodyweight from body_metrics, or None if never logged."""
    with _conn() as con:
        _ensure_body_metrics_schema(con)
        row = con.execute(
            "SELECT weight_kg FROM body_metrics WHERE weight_kg IS NOT NULL ORDER BY date DESC LIMIT 1"
        ).fetchone()
    return float(row["weight_kg"]) if row and row["weight_kg"] is not None else None


def latest_lean_mass_kg() -> Optional[float]:
    """Most recent lean (fat-free) mass.

    Prefers the device's `muscle_mass_kg`; otherwise derives it from the most
    recent reading that has both weight and fat %, as weight × (1 − fat%/100).
    Returns None when neither is available.
    """
    with _conn() as con:
        _ensure_body_metrics_schema(con)
        row = con.execute(
            "SELECT muscle_mass_kg FROM body_metrics WHERE muscle_mass_kg IS NOT NULL ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if row and row["muscle_mass_kg"] is not None:
            return float(row["muscle_mass_kg"])
        row = con.execute(
            "SELECT weight_kg, fat_pct FROM body_metrics "
            "WHERE weight_kg IS NOT NULL AND fat_pct IS NOT NULL ORDER BY date DESC LIMIT 1"
        ).fetchone()
    if row and row["weight_kg"] is not None and row["fat_pct"] is not None:
        return round(float(row["weight_kg"]) * (1.0 - float(row["fat_pct"]) / 100.0), 1)
    return None


# ── Heat / altitude acclimation ──────────────────────────────────────────────

def acclimation_latest() -> Optional[dict]:
    """Most recent daily_metrics row (last 14 days) with any acclimation value."""
    start = (date.today() - timedelta(days=13)).isoformat()
    with _conn() as con:
        _ensure_schema(con)
        row = con.execute(
            """SELECT date, heat_acclimation_pct, altitude_acclimation
               FROM daily_metrics
               WHERE date >= ?
                 AND (heat_acclimation_pct IS NOT NULL OR altitude_acclimation IS NOT NULL)
               ORDER BY date DESC LIMIT 1""",
            (start,),
        ).fetchone()
    return dict(row) if row else None


# ── FTP retest due check ─────────────────────────────────────────────────────

def ftp_retest_due(today: date, max_age_days: int = 42,
                   plan_start: Optional[date] = None) -> Optional[dict]:
    """Return {last_date, age_days} when the newest FTP test is older than
    max_age_days. With an empty table, fires once today > plan_start + 21d
    (only when plan_start is provided)."""
    tests = load_ftp_tests()
    if tests:
        last = tests[-1]
        try:
            last_d = date.fromisoformat(last["date"])
        except Exception:
            return None
        age = (today - last_d).days
        if age > max_age_days:
            return {"last_date": last["date"], "age_days": age}
        return None
    if plan_start is not None and today > plan_start + timedelta(days=21):
        return {"last_date": None, "age_days": None}
    return None


# ── Fuelling compliance logs ─────────────────────────────────────────────────

def _ensure_fuelling_log_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS fuelling_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            activity_id INTEGER UNIQUE,
            planned_carbs_g_per_hr REAL,
            actual_carbs_g_per_hr REAL,
            fluid_ok INTEGER,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)


def save_fuelling_log(log_date: str, activity_id: Optional[int],
                      planned_carbs_g_per_hr: Optional[float],
                      actual_carbs_g_per_hr: Optional[float],
                      fluid_ok: bool, note: Optional[str] = None) -> None:
    with _conn() as con:
        _ensure_fuelling_log_schema(con)
        if activity_id is None:
            # UNIQUE(activity_id) doesn't dedupe NULLs — upsert by date instead
            con.execute(
                "DELETE FROM fuelling_logs WHERE date = ? AND activity_id IS NULL",
                (log_date,),
            )
        con.execute(
            """INSERT OR REPLACE INTO fuelling_logs
               (date, activity_id, planned_carbs_g_per_hr, actual_carbs_g_per_hr, fluid_ok, note)
               VALUES (?,?,?,?,?,?)""",
            (log_date, activity_id, planned_carbs_g_per_hr,
             actual_carbs_g_per_hr, 1 if fluid_ok else 0, note),
        )


def load_fuelling_logs(days: int = 90) -> list[dict]:
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    with _conn() as con:
        _ensure_fuelling_log_schema(con)
        rows = con.execute(
            "SELECT * FROM fuelling_logs WHERE date >= ? ORDER BY date DESC",
            (start,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_coach_memory() -> Optional[dict]:
    with _conn() as con:
        _ensure_coach_memory_schema(con)
        row = con.execute("SELECT memo, updated_at FROM coach_memory WHERE id = 1").fetchone()
    return dict(row) if row else None


def set_coach_memory(memo: str) -> None:
    with _conn() as con:
        _ensure_coach_memory_schema(con)
        con.execute(
            "INSERT OR REPLACE INTO coach_memory (id, memo) VALUES (1, ?)",
            (memo,),
        )
