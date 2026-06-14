"""Boundary tests for the plan-date lookups in plan.py and hr_plan.py."""
from datetime import timedelta

from ai_endurance_coach_over50.history import set_plan_override
from ai_endurance_coach_over50.hr_plan import (
    _HR_PLAN_DAYS,
    HR_PLAN_START,
    HR_TRAINING_WEEKS,
    hr_session_for_date,
)
from ai_endurance_coach_over50.plan import _PLAN_DAYS, PLAN_START, TRAINING_WEEKS, session_for_date


def test_plan_starts_on_monday():
    assert PLAN_START.weekday() == 0
    assert HR_PLAN_START.weekday() == 0


def test_all_weeks_have_seven_days():
    assert all(len(wk) == 7 for wk in TRAINING_WEEKS)
    assert all(len(wk) == 7 for wk in HR_TRAINING_WEEKS)


def test_session_for_date_first_day():
    assert session_for_date(PLAN_START) == TRAINING_WEEKS[0][0]


def test_session_for_date_last_day():
    last = PLAN_START + timedelta(days=_PLAN_DAYS - 1)
    assert session_for_date(last) == TRAINING_WEEKS[-1][6]


def test_session_for_date_outside_window():
    assert session_for_date(PLAN_START - timedelta(days=1)) is None
    assert session_for_date(PLAN_START + timedelta(days=_PLAN_DAYS)) is None


def test_session_for_date_respects_override():
    d = PLAN_START + timedelta(days=1)
    set_plan_override(d.isoformat(), "bike", "Recovery Spin", 30, "test")
    stype, label, dur = session_for_date(d)
    assert (stype, label, dur) == ("bike", "Recovery Spin", 30)


def test_hr_session_for_date_boundaries():
    assert hr_session_for_date(HR_PLAN_START) == HR_TRAINING_WEEKS[0][0]
    last = HR_PLAN_START + timedelta(days=_HR_PLAN_DAYS - 1)
    assert hr_session_for_date(last) == HR_TRAINING_WEEKS[-1][6]
    assert hr_session_for_date(HR_PLAN_START - timedelta(days=1)) is None
    assert hr_session_for_date(HR_PLAN_START + timedelta(days=_HR_PLAN_DAYS)) is None
