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

DB_PATH = Path.home() / ".garmin_readiness" / "history.db"

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

# Don't score these — they're context/baselines, not daily readiness signals
_UNSCORED = {"training_load_chronic", "vo2_max"}

SCORED_FIELDS = [f for f in NUMERIC_FIELDS if f not in _UNSCORED]

HIGHER_IS_BETTER = {
    "sleep_score", "sleep_seconds", "hrv_last_night", "hrv_weekly_avg",
    "body_battery_morning",
}
LOWER_IS_BETTER = {
    "avg_stress", "rest_stress", "acwr", "training_load_acute",
}


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
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
    end = date.today()
    start = end - timedelta(days=days)
    results = []
    for i in range(days + 1):
        d = start + timedelta(days=i)
        m = load(d)
        if m is None:
            results.append((d, None))
            continue
        stats = baseline_stats(d)
        results.append((d, composite_score(m, stats)))
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
            """SELECT date, hrv_last_night, sleep_score, avg_stress
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
            "hrv_last_night": row.get("hrv_last_night"),
            "sleep_score": row.get("sleep_score"),
            "avg_stress": row.get("avg_stress"),
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
                INSERT OR IGNORE INTO blood_pressure
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
