"""Sync Withings measurements to Garmin Connect via FIT file upload."""
from __future__ import annotations

import calendar
import logging
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

WITHINGS_CONFIG = Path.home() / ".garmin_readiness" / "withings"


def _to_unix(d: date) -> int:
    return calendar.timegm(d.timetuple())


def sync_withings_to_garmin(api: Any, days: int = 7) -> bool:
    """Fetch recent Withings measurements and upload FIT files to Garmin Connect.

    Requires withings-sync to be installed: pip install withings-sync
    On first run, Withings OAuth requires an interactive browser step —
    run this manually once from the terminal before scheduling.

    Returns True if any data was uploaded.
    """
    try:
        from withings_sync.withings2 import WithingsAccount
        from withings_sync.fit import FitEncoderWeight, FitEncoderBloodPressure
    except ImportError:
        logger.warning("withings-sync not installed — run: pip install withings-sync")
        return False

    WITHINGS_CONFIG.mkdir(parents=True, exist_ok=True)
    config_folder = str(WITHINGS_CONFIG)

    try:
        withings = WithingsAccount(config_folder=config_folder)
    except Exception as e:
        logger.warning("Withings auth failed: %s", e)
        return False

    end = date.today()
    start = end - timedelta(days=days)

    try:
        groups = withings.get_measurements(
            startdate=_to_unix(start),
            enddate=_to_unix(end),
        )
        height = withings.get_height()
    except Exception as e:
        logger.warning("Withings fetch failed: %s", e)
        return False

    if not groups:
        logger.debug("No Withings measurements in the last %d days", days)
        return False

    weight_records = []
    bp_records = []

    for group in groups:
        dt = group.get_datetime()
        if group.get_weight():
            w = group.get_weight()
            bmi = round(w / (height ** 2), 1) if height else None
            weight_records.append({
                "date_time": dt,
                "weight": w,
                "fat_ratio": group.get_fat_ratio(),
                "muscle_mass": group.get_muscle_mass(),
                "bone_mass": group.get_bone_mass(),
                "percent_hydration": None,
                "bmi": bmi,
            })
        if group.get_diastolic_blood_pressure():
            bp_records.append({
                "date_time": dt,
                "diastolic_blood_pressure": group.get_diastolic_blood_pressure(),
                "systolic_blood_pressure": group.get_systolic_blood_pressure(),
                "heart_pulse": group.get_heart_pulse(),
            })

    synced = False

    if weight_records:
        fit = FitEncoderWeight()
        fit.write_file_info()
        fit.write_file_creator()
        for r in weight_records:
            fit.write_device_info(timestamp=r["date_time"])
            fit.write_weight_scale(
                timestamp=r["date_time"],
                weight=r["weight"],
                percent_fat=r["fat_ratio"],
                percent_hydration=r["percent_hydration"],
                bone_mass=r["bone_mass"],
                muscle_mass=r["muscle_mass"],
                bmi=r["bmi"],
            )
        fit.finish()
        if _upload_fit(api, fit):
            logger.info("Withings weight FIT uploaded (%d records)", len(weight_records))
            synced = True

    if bp_records:
        fit = FitEncoderBloodPressure()
        fit.write_file_info()
        fit.write_file_creator()
        for r in bp_records:
            fit.write_device_info(timestamp=r["date_time"])
            fit.write_blood_pressure(
                timestamp=r["date_time"],
                diastolic_blood_pressure=r["diastolic_blood_pressure"],
                systolic_blood_pressure=r["systolic_blood_pressure"],
                heart_rate=r["heart_pulse"],
            )
        fit.finish()
        if _upload_fit(api, fit):
            logger.info("Withings BP FIT uploaded (%d records)", len(bp_records))
            synced = True

    if synced:
        withings.set_lastsync()

    return synced


def _upload_fit(api: Any, fit_encoder) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".fit", delete=False) as tmp:
        tmp.write(fit_encoder.getvalue())
        tmp_path = tmp.name
    try:
        api.upload_activity(tmp_path)
        return True
    except Exception as e:
        logger.warning("FIT upload to Garmin failed: %s", e)
        return False
    finally:
        os.unlink(tmp_path)
