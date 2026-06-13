"""Tests for the HRV traffic light and session modulation in modulation.py."""
from datetime import date, timedelta

import pytest

import garmin_readiness.modulation as modulation
from garmin_readiness.metrics import DailyMetrics
from garmin_readiness.modulation import hrv_traffic_light, session_modulation

TODAY = date(2026, 6, 10)


def _baseline_rows(end=TODAY, include_today=False):
    """30 days alternating 58/62 ms → mean 60, pstdev 2."""
    rows = []
    start = end - timedelta(days=30)
    for i in range(30):
        rows.append({"date": (start + timedelta(days=i)).isoformat(),
                     "hrv_last_night": 58.0 if i % 2 else 62.0})
    if include_today:
        rows.append({"date": end.isoformat(), "hrv_last_night": 999.0})
    return rows


def _metrics(hrv):
    return DailyMetrics(date=TODAY, hrv_last_night=hrv)


@pytest.fixture(autouse=True)
def patched_history(monkeypatch):
    monkeypatch.setattr(modulation, "raw_history", lambda days=31: _baseline_rows())
    monkeypatch.setattr(modulation, "get_plan_override", lambda d: None)
    monkeypatch.setattr(modulation, "session_for_date_extended", lambda d: None)


# ── hrv_traffic_light ────────────────────────────────────────────────────────

def test_red_when_hrv_far_below_baseline():
    light = hrv_traffic_light(_metrics(40.0), comp_z=None)  # z = -10
    assert light["status"] == "red"


def test_amber_when_hrv_moderately_below():
    light = hrv_traffic_light(_metrics(58.0), comp_z=None)  # z = -1.0
    assert light["status"] == "amber"


def test_green_when_hrv_normal():
    light = hrv_traffic_light(_metrics(60.0), comp_z=None)  # z = 0
    assert light["status"] == "green"


def test_unknown_without_history_or_composite():
    light = hrv_traffic_light(DailyMetrics(date=TODAY), comp_z=None)
    assert light["status"] == "unknown"


def test_baseline_excludes_todays_own_row(monkeypatch):
    # A 999 ms row for today must not pollute the baseline (it duplicates m)
    monkeypatch.setattr(modulation, "raw_history",
                        lambda days=31: _baseline_rows(include_today=True))
    light = hrv_traffic_light(_metrics(40.0), comp_z=None)
    assert light["status"] == "red"
    assert light["hrv_z"] == pytest.approx(-10.0)


def test_composite_backstop_turns_amber():
    light = hrv_traffic_light(_metrics(60.0), comp_z=-0.8)  # HRV fine, composite low
    assert light["status"] == "amber"


# ── session_modulation ───────────────────────────────────────────────────────

def _light(status):
    return {"status": status, "hrv_z": None, "ratio": None, "reason": "test"}


def test_red_swaps_to_recovery_spin(monkeypatch):
    monkeypatch.setattr(modulation, "session_for_date_extended",
                        lambda d: ("tempo", "Tempo Intervals", 75))
    mod = session_modulation(TODAY, _metrics(40.0), None, light=_light("red"))
    assert (mod["session_type"], mod["label"], mod["duration_min"]) == ("bike", "Recovery Spin", 30)


def test_amber_keeps_duration_drops_intensity(monkeypatch):
    monkeypatch.setattr(modulation, "session_for_date_extended",
                        lambda d: ("tempo", "Tempo Intervals", 75))
    mod = session_modulation(TODAY, _metrics(58.0), None, light=_light("amber"))
    assert (mod["session_type"], mod["label"], mod["duration_min"]) == ("bike", "Zone 2 Steady", 75)


def test_green_returns_none(monkeypatch):
    monkeypatch.setattr(modulation, "session_for_date_extended",
                        lambda d: ("tempo", "Tempo Intervals", 75))
    assert session_modulation(TODAY, _metrics(60.0), None, light=_light("green")) is None


def test_existing_override_suppresses_suggestion(monkeypatch):
    monkeypatch.setattr(modulation, "session_for_date_extended",
                        lambda d: ("tempo", "Tempo Intervals", 75))
    monkeypatch.setattr(modulation, "get_plan_override",
                        lambda d: {"label": "already decided"})
    assert session_modulation(TODAY, _metrics(40.0), None, light=_light("red")) is None


def test_rest_day_amber_shows_pill_without_swap(monkeypatch):
    monkeypatch.setattr(modulation, "session_for_date_extended",
                        lambda d: ("rest", "Rest", 0))
    mod = session_modulation(TODAY, _metrics(58.0), None, light=_light("amber"))
    assert mod is not None
    assert "label" not in mod  # no swap proposed, just the status pill


# ── Haute Route plan fallback ────────────────────────────────────────────────
# session_for_date_extended is stubbed to None by the autouse fixture, so these
# exercise the hr_session_for_date fallback path.

def test_hr_amber_vo2_swaps_to_z2_endurance(monkeypatch):
    monkeypatch.setattr(modulation, "hr_session_for_date",
                        lambda d: ("vo2", "VO2 Intervals 5×3 min", 60))
    mod = session_modulation(TODAY, _metrics(58.0), None, light=_light("amber"))
    assert (mod["session_type"], mod["label"], mod["duration_min"]) == ("endurance", "Z2 Endurance", 60)


def test_hr_amber_back_to_back_swaps_to_easy_long(monkeypatch):
    monkeypatch.setattr(modulation, "hr_session_for_date",
                        lambda d: ("back_to_back", "Back-to-Back Day 1", 240))
    mod = session_modulation(TODAY, _metrics(58.0), None, light=_light("amber"))
    assert (mod["session_type"], mod["label"], mod["duration_min"]) == ("long", "Long Ride (Easy)", 240)


def test_hr_red_swaps_to_recovery_type(monkeypatch):
    # HR vocabulary: red uses type "recovery", not the 12-week plan's "bike"
    monkeypatch.setattr(modulation, "hr_session_for_date",
                        lambda d: ("sweetspot", "Low Cadence Sweetspot", 90))
    mod = session_modulation(TODAY, _metrics(40.0), None, light=_light("red"))
    assert (mod["session_type"], mod["label"], mod["duration_min"]) == ("recovery", "Recovery Spin", 30)


def test_hr_amber_recovery_session_shows_pill_only(monkeypatch):
    monkeypatch.setattr(modulation, "hr_session_for_date",
                        lambda d: ("recovery", "Strength + Core", 60))
    mod = session_modulation(TODAY, _metrics(58.0), None, light=_light("amber"))
    assert mod is not None
    assert "label" not in mod


def test_hr_amber_already_easy_shows_pill_only(monkeypatch):
    monkeypatch.setattr(modulation, "hr_session_for_date",
                        lambda d: ("endurance", "Z2 Easy", 45))
    mod = session_modulation(TODAY, _metrics(58.0), None, light=_light("amber"))
    assert mod is not None
    assert "label" not in mod
