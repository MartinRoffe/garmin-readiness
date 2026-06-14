"""Value formatting helpers and activity enrichment for display."""
from __future__ import annotations

from typing import Optional


FIELD_LABELS: dict[str, tuple[str, str]] = {
    # field: (display name, unit)
    "sleep_score":           ("Sleep Score",          "/100"),
    "sleep_seconds":         ("Sleep Duration",       ""),
    "hrv_last_night":        ("HRV Last Night",       " ms"),
    "hrv_weekly_avg":        ("HRV Weekly Avg",       " ms"),
    "body_battery_morning":  ("Body Battery",         "/100"),
    "avg_stress":            ("Avg Stress",           "/100"),
    "rest_stress":           ("Rest Stress",          "/100"),
    "acwr":                  ("Acute:Chronic Ratio",  ""),
    "training_load_acute":   ("Acute Load (7d)",      ""),
    "training_load_chronic": ("Chronic Load (28d)",   ""),
    "vo2_max":               ("VO2 Max",              " ml/kg/min"),
}


def fmt_value(field: str, value: Optional[float]) -> str:
    if value is None:
        return "—"
    if field == "sleep_seconds":
        h, rem = divmod(int(value), 3600)
        m = rem // 60
        return f"{h}h {m:02d}m"
    if field in ("sleep_score", "body_battery_morning", "avg_stress", "rest_stress"):
        return f"{value:.0f}"
    if field in ("hrv_last_night", "hrv_weekly_avg", "vo2_max"):
        return f"{value:.1f}"
    if field == "acwr":
        return f"{value:.2f}"
    if field in ("training_load_acute", "training_load_chronic"):
        return f"{value:.0f}"
    return f"{value:.1f}"


def fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def fmt_distance(meters: Optional[float]) -> str:
    if meters is None or meters == 0:
        return ""
    return f"{meters / 1000:.1f} km"


def fmt_pace(seconds: Optional[float], meters: Optional[float], type_key: str) -> str:
    if not seconds or not meters or meters == 0:
        return ""
    if "biking" in type_key or "cycling" in type_key:
        kmh = (meters / 1000) / (seconds / 3600)
        return f"{kmh:.1f} km/h"
    if "running" in type_key or "hiking" in type_key or "walking" in type_key:
        min_per_km = (seconds / 60) / (meters / 1000)
        mins = int(min_per_km)
        secs = int((min_per_km - mins) * 60)
        return f"{mins}:{secs:02d}/km"
    return ""


def fmt_speed(seconds: Optional[float], meters: Optional[float]) -> str:
    """Return avg speed in km/h, or empty string if data missing."""
    if not seconds or not meters or meters == 0:
        return ""
    return f"{(meters / 1000) / (seconds / 3600):.1f} km/h"


_RUCK_TYPE_KEYS = {"hiking", "walking", "trail_running", "running", "load_carry"}


def enrich_activity(a: dict) -> dict:
    from .metrics import _TYPE_ICONS, _TYPE_LABELS
    type_key = a.get("type_key", "")
    secs = a.get("duration_seconds")
    meters = a.get("distance_meters")
    is_ruck = type_key in _RUCK_TYPE_KEYS or "load carry" in (a.get("name") or "").lower()
    return {
        **a,
        "icon":          _TYPE_ICONS.get(type_key, "🏅"),
        "type_label":    _TYPE_LABELS.get(type_key, type_key.replace("_", " ").title()),
        "duration_fmt":  fmt_duration(secs),
        "distance_fmt":  fmt_distance(meters),
        "pace_fmt":      fmt_pace(secs, meters, type_key),
        "speed_fmt":     fmt_speed(secs, meters) if is_ruck else "",
    }


def readiness_label(z: Optional[float]) -> tuple[str, str]:
    """Returns (label, css_colour_class) for the composite z-score."""
    if z is None:
        return "Building baseline…", "text-zinc-400"
    if z >= 1.0:
        return "Above Average", "text-emerald-400"
    if z >= 0.25:
        return "Good", "text-green-400"
    if z >= -0.25:
        return "Average", "text-yellow-400"
    if z >= -1.0:
        return "Below Average", "text-orange-400"
    return "Low", "text-red-400"
