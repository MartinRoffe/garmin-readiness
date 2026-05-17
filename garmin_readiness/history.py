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
    "ruck":     {"hiking", "walking", "trail_running", "running"},
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


def _ensure_activities_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS activities (
            activity_id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            start_time TEXT,
            name TEXT,
            type_key TEXT,
            duration_seconds REAL,
            distance_meters REAL,
            elevation_gain REAL,
            avg_hr REAL,
            max_hr REAL,
            calories REAL,
            avg_speed_ms REAL
        )
    """)


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
    with _conn() as con:
        _ensure_activities_schema(con)
        for a in activities:
            con.execute(
                """INSERT OR REPLACE INTO activities
                   (activity_id, date, start_time, name, type_key,
                    duration_seconds, distance_meters, elevation_gain,
                    avg_hr, max_hr, calories, avg_speed_ms)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    a["activity_id"],
                    a["date"],
                    a["start_time"],
                    a["name"],
                    a["type_key"],
                    a["duration_seconds"],
                    a["distance_meters"],
                    a["elevation_gain"],
                    a["avg_hr"],
                    a["max_hr"],
                    a["calories"],
                    a["avg_speed_ms"],
                ),
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
