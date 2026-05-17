"""Garmin Connect API calls → DailyMetrics dataclass."""
from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

# Maps Garmin's internal feedback phrases to display labels
_TRAINING_STATUS_MAP = {
    "OVERREACHING_1": "Overreaching",
    "OVERREACHING_2": "Overreaching",
    "OVERREACHING_3": "Overreaching",
    "PRODUCTIVE":     "Productive",
    "MAINTAINING":    "Maintaining",
    "RECOVERY":       "Recovering",
    "PEAKING":        "Peaking",
    "UNPRODUCTIVE":   "Unproductive",
    "BELOW_EXPECTATIONS": "Below Target",
    "DETRAINING":     "Detraining",
}


@dataclass
class DailyMetrics:
    date: date
    # Sleep
    sleep_score: Optional[float] = None           # 0–100
    sleep_seconds: Optional[float] = None         # total sleep duration
    # HRV
    hrv_last_night: Optional[float] = None        # ms (newer devices only)
    hrv_weekly_avg: Optional[float] = None        # ms
    hrv_status: Optional[str] = None              # BALANCED / UNBALANCED / LOW / POOR
    # Body Battery
    body_battery_morning: Optional[float] = None  # 0–100
    # Stress (lower = better)
    avg_stress: Optional[float] = None            # 0–100
    rest_stress: Optional[float] = None           # 0–100
    # Training status (from get_training_status)
    training_status_label: Optional[str] = None   # human-readable, not scored
    acwr: Optional[float] = None                  # acute:chronic workload ratio
    acwr_status: Optional[str] = None             # OPTIMAL / HIGH / VERY_HIGH / LOW
    training_load_acute: Optional[float] = None   # 7-day acute load
    training_load_chronic: Optional[float] = None # 28-day chronic load (context only)
    vo2_max: Optional[float] = None               # VO2 max (ml/kg/min)


def _safe_get(d: dict, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, default)
        if d is None:
            return default
    return d


def fetch_metrics(api, target_date: date) -> DailyMetrics:
    """Fetch all wellness metrics for target_date; missing endpoints leave fields None."""
    date_str = target_date.strftime("%Y-%m-%d")
    m = DailyMetrics(date=target_date)

    # --- Sleep ---
    try:
        sleep = api.get_sleep_data(date_str)
        dto = sleep.get("dailySleepDTO") or {}
        score = _safe_get(dto, "sleepScores", "overall", "value")
        if score is None:
            score = _safe_get(dto, "sleepScore")
        m.sleep_score = float(score) if score is not None else None
        raw_secs = dto.get("sleepTimeSeconds")
        m.sleep_seconds = float(raw_secs) if raw_secs is not None else None
    except Exception as e:
        logger.debug("Sleep fetch failed: %s", e)

    # --- HRV ---
    try:
        hrv = api.get_hrv_data(date_str)
        summary = (hrv or {}).get("hrvSummary") or {}
        last_night = summary.get("lastNight")
        weekly = summary.get("weeklyAvg")
        m.hrv_last_night = float(last_night) if last_night is not None else None
        m.hrv_weekly_avg = float(weekly) if weekly is not None else None
        m.hrv_status = summary.get("status")
    except Exception as e:
        logger.debug("HRV fetch failed: %s", e)

    # --- Body Battery ---
    try:
        bb = api.get_body_battery(date_str, date_str)
        if bb and isinstance(bb, list):
            values: list[float] = []
            for entry in bb:
                # Prefer the time-series array — last entry is the current level
                ts_array = entry.get("bodyBatteryValuesArray") if isinstance(entry, dict) else None
                if ts_array:
                    for reading in ts_array:
                        if isinstance(reading, (list, tuple)) and len(reading) >= 2 and reading[1] is not None:
                            values.append(float(reading[1]))
                elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                    if entry[1] is not None:
                        values.append(float(entry[1]))
            if values:
                m.body_battery_morning = values[-1]  # most recent reading
    except Exception as e:
        logger.debug("Body Battery fetch failed: %s", e)

    # --- Stress ---
    try:
        stress = api.get_stress_data(date_str)
        avg = stress.get("avgStressLevel") if stress else None
        rest = stress.get("restStressLevel") if stress else None
        m.avg_stress = float(avg) if avg is not None and avg >= 0 else None
        m.rest_stress = float(rest) if rest is not None and rest >= 0 else None
    except Exception as e:
        logger.debug("Stress fetch failed: %s", e)

    # --- Training Status (ACWR, VO2 Max, training status label) ---
    try:
        ts = api.get_training_status(date_str)
        if ts and isinstance(ts, dict):
            # Primary-device training status and ACWR
            latest = _safe_get(ts, "mostRecentTrainingStatus", "latestTrainingStatusData") or {}
            primary = next(
                (v for v in latest.values() if v.get("primaryTrainingDevice")),
                next(iter(latest.values()), {}) if latest else {},
            )
            if primary:
                phrase = primary.get("trainingStatusFeedbackPhrase") or ""
                m.training_status_label = (
                    _TRAINING_STATUS_MAP.get(phrase)
                    or (phrase.replace("_", " ").title() if phrase else None)
                )
                acute = primary.get("acuteTrainingLoadDTO") or {}
                acwr = acute.get("dailyAcuteChronicWorkloadRatio")
                m.acwr = float(acwr) if acwr is not None else None
                m.acwr_status = acute.get("acwrStatus")
                al = acute.get("dailyTrainingLoadAcute")
                cl = acute.get("dailyTrainingLoadChronic")
                m.training_load_acute = float(al) if al is not None else None
                m.training_load_chronic = float(cl) if cl is not None else None

            # VO2 Max
            vo2 = _safe_get(ts, "mostRecentVO2Max", "generic")
            if vo2:
                v = vo2.get("vo2MaxPreciseValue") or vo2.get("vo2MaxValue")
                m.vo2_max = float(v) if v is not None else None
    except Exception as e:
        logger.debug("Training status fetch failed: %s", e)

    return m


TEXT_FIELDS = {"date", "hrv_status", "training_status_label", "acwr_status"}


_TYPE_LABELS: dict[str, str] = {
    "running":              "Running",
    "trail_running":        "Trail Run",
    "treadmill_running":    "Treadmill Run",
    "road_biking":          "Road Cycling",
    "cycling":              "Cycling",
    "indoor_cycling":       "Indoor Cycling",
    "mountain_biking":      "MTB",
    "swimming":             "Swimming",
    "open_water_swimming":  "Open Water",
    "strength_training":    "Strength",
    "cardio_training":      "Cardio",
    "hiking":               "Hiking",
    "walking":              "Walking",
    "yoga":                 "Yoga",
    "multi_sport":          "Multi-Sport",
    "rowing":               "Rowing",
    "indoor_rowing":        "Indoor Rowing",
    "elliptical":           "Elliptical",
    "stair_climbing":       "Stair Climb",
}

_TYPE_ICONS: dict[str, str] = {
    "running":              "🏃",
    "trail_running":        "🏃",
    "treadmill_running":    "🏃",
    "road_biking":          "🚴",
    "cycling":              "🚴",
    "indoor_cycling":       "🚴",
    "mountain_biking":      "🚵",
    "swimming":             "🏊",
    "open_water_swimming":  "🏊",
    "strength_training":    "🏋️",
    "cardio_training":      "❤️",
    "hiking":               "⛰️",
    "walking":              "🚶",
    "yoga":                 "🧘",
    "multi_sport":          "⚡",
    "rowing":               "🚣",
    "indoor_rowing":        "🚣",
    "elliptical":           "🔄",
    "stair_climbing":       "🪜",
}


def fetch_activities(api, days: int = 7) -> list[dict]:
    """Return raw activity dicts for the last `days` days."""
    from datetime import date, timedelta
    end = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    try:
        raw = api.get_activities_by_date(start, end) or []
    except Exception as e:
        logger.debug("Activities fetch failed: %s", e)
        return []

    results = []
    for a in raw:
        start_local = a.get("startTimeLocal", "")
        act_date = start_local[:10] if start_local else ""
        type_key = (a.get("activityType") or {}).get("typeKey", "")
        results.append({
            "activity_id":      a.get("activityId"),
            "date":             act_date,
            "start_time":       start_local,
            "name":             a.get("activityName", ""),
            "type_key":         type_key,
            "type_label":       _TYPE_LABELS.get(type_key, type_key.replace("_", " ").title()),
            "icon":             _TYPE_ICONS.get(type_key, "🏅"),
            "duration_seconds": a.get("duration"),
            "distance_meters":  a.get("distance"),
            "elevation_gain":   a.get("elevationGain"),
            "avg_hr":           a.get("averageHR"),
            "max_hr":           a.get("maxHR"),
            "calories":         a.get("calories"),
            "avg_speed_ms":     a.get("averageSpeed"),
        })
    return results


def available_count(m: DailyMetrics) -> int:
    """Count non-null numeric fields — used to detect empty/failed fetches."""
    return sum(
        1 for f in fields(m)
        if f.name not in TEXT_FIELDS and getattr(m, f.name) is not None
    )
