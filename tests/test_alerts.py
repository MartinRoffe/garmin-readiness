"""Tests for the five fatigue-alert conditions in alerts.py.

DB-backed inputs are monkeypatched at the alerts-module level so each
condition is exercised in isolation with synthetic data.
"""
from datetime import date, timedelta

import pytest

import garmin_readiness.alerts as alerts
from garmin_readiness.alerts import _signal_z, check_fatigue_alerts

TODAY = date(2026, 6, 10)


@pytest.fixture(autouse=True)
def quiet_inputs(monkeypatch):
    """Default every data source to 'nothing happening'; tests override."""
    monkeypatch.setattr(alerts, "raw_history", lambda days=14: [])
    monkeypatch.setattr(alerts, "pmc_history", lambda days=6: [])
    monkeypatch.setattr(alerts, "load_activities_by_date", lambda a, b: {})
    monkeypatch.setattr(alerts, "session_for_date", lambda d: None)
    monkeypatch.setattr(alerts, "weekly_monotony_strain", lambda weeks=2: [])


def _alert_types(today=TODAY):
    return {a["type"] for a in check_fatigue_alerts(today)}


def _hrv_rows(values, end=TODAY):
    start = end - timedelta(days=len(values) - 1)
    return [{"date": (start + timedelta(days=i)).isoformat(), "hrv_last_night": v}
            for i, v in enumerate(values)]


# ── 1. HRV_TREND ─────────────────────────────────────────────────────────────

def test_hrv_trend_fires_on_four_descending(monkeypatch):
    monkeypatch.setattr(alerts, "raw_history",
                        lambda days=14: _hrv_rows([60, 55, 50, 45]))
    assert "HRV_TREND" in _alert_types()


def test_hrv_trend_quiet_when_not_monotonic(monkeypatch):
    monkeypatch.setattr(alerts, "raw_history",
                        lambda days=14: _hrv_rows([60, 55, 56, 45]))
    assert "HRV_TREND" not in _alert_types()


# ── 2. TSB_DEEP ──────────────────────────────────────────────────────────────

def test_tsb_deep_fires_after_five_days(monkeypatch):
    hist = [{"tsb": -200}] * 5 + [{"tsb": -100}]
    monkeypatch.setattr(alerts, "pmc_history", lambda days=6: hist)
    assert "TSB_DEEP" in _alert_types()


def test_tsb_deep_quiet_at_four_days(monkeypatch):
    hist = [{"tsb": -200}] * 4 + [{"tsb": -100}] * 2
    monkeypatch.setattr(alerts, "pmc_history", lambda days=6: hist)
    assert "TSB_DEEP" not in _alert_types()


# ── 3. VOLUME_SPIKE ──────────────────────────────────────────────────────────

def _planned_hour_every_day(monkeypatch):
    monkeypatch.setattr(alerts, "session_for_date",
                        lambda d: ("bike", "Z2 Ride", 60))  # 420 min planned


def test_volume_spike_fires_over_120_pct(monkeypatch):
    _planned_hour_every_day(monkeypatch)
    acts = {TODAY.isoformat(): [{"duration_seconds": 540 * 60}]}  # 540 min
    monkeypatch.setattr(alerts, "load_activities_by_date", lambda a, b: acts)
    assert "VOLUME_SPIKE" in _alert_types()


def test_volume_spike_quiet_within_tolerance(monkeypatch):
    _planned_hour_every_day(monkeypatch)
    acts = {TODAY.isoformat(): [{"duration_seconds": 480 * 60}]}  # 114%
    monkeypatch.setattr(alerts, "load_activities_by_date", lambda a, b: acts)
    assert "VOLUME_SPIKE" not in _alert_types()


# ── 4. ILLNESS_RISK ──────────────────────────────────────────────────────────

def _illness_rows(end=TODAY, hrv_today=40.0, sleep_today=70.0):
    """30 baseline days (alternating values → non-zero variance) + today."""
    rows = []
    start = end - timedelta(days=30)
    for i in range(30):
        rows.append({
            "date": (start + timedelta(days=i)).isoformat(),
            "hrv_last_night": 58.0 if i % 2 else 62.0,    # mean 60, pstdev 2
            "sleep_score": 78.0 if i % 2 else 82.0,        # mean 80, pstdev 2
            "resting_hr": None,
            "rest_stress": None,
        })
    rows.append({"date": end.isoformat(), "hrv_last_night": hrv_today,
                 "sleep_score": sleep_today, "resting_hr": None, "rest_stress": None})
    return rows


def test_illness_risk_fires_on_two_of_three(monkeypatch):
    monkeypatch.setattr(alerts, "raw_history", lambda days=14: _illness_rows())
    assert "ILLNESS_RISK" in _alert_types()


def test_illness_risk_quiet_on_single_trigger(monkeypatch):
    rows = _illness_rows(hrv_today=40.0, sleep_today=80.0)  # only HRV depressed
    monkeypatch.setattr(alerts, "raw_history", lambda days=14: rows)
    assert "ILLNESS_RISK" not in _alert_types()


def test_illness_risk_abstains_when_latest_row_is_stale(monkeypatch):
    # Watch hasn't synced: last row is yesterday — must NOT fire with stale data
    rows = _illness_rows(end=TODAY - timedelta(days=1))
    monkeypatch.setattr(alerts, "raw_history", lambda days=14: rows)
    assert "ILLNESS_RISK" not in _alert_types()


def test_signal_z_abstains_below_seven_samples():
    rows = _hrv_rows([60, 61, 62, 63, 40])
    assert _signal_z(rows, "hrv_last_night") is None


def test_signal_z_abstains_on_zero_variance():
    rows = _hrv_rows([60.0] * 10 + [40.0])
    assert _signal_z(rows, "hrv_last_night") is None


# ── 5. MONOTONY_HIGH ─────────────────────────────────────────────────────────

def _monotony_week(monotony, strain=900):
    return [{"week_start": TODAY - timedelta(days=6), "monotony": monotony,
             "strain": strain}]


def test_monotony_fires_above_two(monkeypatch):
    monkeypatch.setattr(alerts, "weekly_monotony_strain",
                        lambda weeks=2: _monotony_week(2.5))
    assert "MONOTONY_HIGH" in _alert_types()


def test_monotony_quiet_at_or_below_two(monkeypatch):
    monkeypatch.setattr(alerts, "weekly_monotony_strain",
                        lambda weeks=2: _monotony_week(1.8))
    assert "MONOTONY_HIGH" not in _alert_types()


def test_monotony_skips_young_week(monkeypatch):
    # Week started yesterday (elapsed < 4 days) — too little data to judge
    weeks = [{"week_start": TODAY - timedelta(days=1), "monotony": 3.0, "strain": 900}]
    monkeypatch.setattr(alerts, "weekly_monotony_strain", lambda weeks=2: weeks)
    assert "MONOTONY_HIGH" not in _alert_types()
