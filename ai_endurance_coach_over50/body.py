"""Fetch and parse body composition + blood pressure from Garmin Connect."""
from __future__ import annotations

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)


def fetch_body_composition(api, days: int = 90) -> list[dict]:
    """Return body composition readings for the last `days` days."""
    end = date.today()
    start = end - timedelta(days=days - 1)
    try:
        data = api.get_body_composition(
            start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        )
    except Exception as e:
        logger.debug("Body composition fetch failed: %s", e)
        return []

    if not data or not isinstance(data, dict):
        return []

    logger.info("body_composition keys: %s", list(data.keys()))

    entries = data.get("dateWeightList") or data.get("allWeightMetrics") or []
    if not entries:
        logger.info("body_composition: no entries in response")
        return []

    if entries:
        logger.info("body_composition entry[0] keys: %s", list(entries[0].keys()) if isinstance(entries[0], dict) else entries[0])

    readings = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cal_date = entry.get("calendarDate") or entry.get("date")
        if not cal_date:
            continue

        weight_g = entry.get("weight") or entry.get("weightInGrams")
        weight_kg = round(weight_g / 1000.0, 2) if weight_g else None

        fat_pct = entry.get("bodyFat") or entry.get("bodyFatPercent") or entry.get("percentFat")
        muscle_g = entry.get("muscleMass") or entry.get("muscleMassInGrams")
        muscle_kg = round(muscle_g / 1000.0, 2) if muscle_g else None
        bone_g = entry.get("boneMass") or entry.get("boneMassInGrams")
        bone_kg = round(bone_g / 1000.0, 2) if bone_g else None

        readings.append({
            "date": str(cal_date)[:10],
            "weight_kg": weight_kg,
            "fat_pct": float(fat_pct) if fat_pct is not None else None,
            "muscle_mass_kg": muscle_kg,
            "bone_mass_kg": bone_kg,
            "hydration_pct": entry.get("bodyWater") or entry.get("percentHydration"),
            "visceral_fat": entry.get("visceralFat") or entry.get("visceralFatRating"),
            "bmi": entry.get("bmi"),
            "metabolic_age": entry.get("metabolicAge"),
        })
    return readings


def fetch_blood_pressure(api, days: int = 90) -> list[dict]:
    """Return blood pressure readings for the last `days` days."""
    end = date.today()
    start = end - timedelta(days=days - 1)
    try:
        data = api.get_blood_pressure(
            start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        )
    except Exception as e:
        logger.debug("Blood pressure fetch failed: %s", e)
        return []

    if not data or not isinstance(data, dict):
        return []

    logger.info("blood_pressure keys: %s", list(data.keys()))

    entries = (
        data.get("measurementSummaries")
        or data.get("bloodPressureMeasurements")
        or []
    )
    if not entries:
        logger.info("blood_pressure: no entries in response")
        return []

    if entries:
        logger.info("blood_pressure entry[0] keys: %s", list(entries[0].keys()) if isinstance(entries[0], dict) else entries[0])

    readings = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cal_date = entry.get("calendarDate") or entry.get("date")
        timestamp = (
            entry.get("startTimestampLocal")
            or entry.get("measurementTimestampLocal")
            or cal_date
        )
        systolic = entry.get("systolic") or entry.get("systolicValue")
        diastolic = entry.get("diastolic") or entry.get("diastolicValue")
        pulse = entry.get("pulse") or entry.get("pulseRate")

        if not cal_date or systolic is None or diastolic is None:
            continue

        readings.append({
            "date": str(cal_date)[:10],
            "timestamp_local": str(timestamp),
            "systolic": int(systolic),
            "diastolic": int(diastolic),
            "pulse": int(pulse) if pulse is not None else None,
        })
    return readings


def bp_classification(systolic: int, diastolic: int) -> tuple[str, str]:
    """Return (label, css_colour) for a blood pressure reading."""
    if systolic > 180 or diastolic > 120:
        return "Hypertensive Crisis", "var(--red)"
    if systolic >= 140 or diastolic >= 90:
        return "High — Stage 2", "var(--red)"
    if systolic >= 130 or diastolic >= 80:
        return "High — Stage 1", "var(--orange)"
    if systolic >= 120 and diastolic < 80:
        return "Elevated", "var(--orange)"
    return "Normal", "var(--green)"
