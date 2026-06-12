"""Tests for the z-score / composite maths and small history.py helpers."""
import math
import statistics
from datetime import date, timedelta

from garmin_readiness.history import (
    _stats_from_rows,
    composite_score,
    ftp_retest_due,
    history_for_chart,
    save,
    save_ftp_test,
    weekly_monotony_strain,
    z_score,
)
from garmin_readiness.metrics import DailyMetrics


# ── z_score ──────────────────────────────────────────────────────────────────

def test_z_score_higher_is_better():
    assert z_score(85, 80, 5, "sleep_score") == 1.0


def test_z_score_lower_is_better_sign_flipped():
    # avg_stress above the mean must come out NEGATIVE (worse readiness)
    assert z_score(45, 40, 5, "avg_stress") == -1.0


# ── _stats_from_rows ─────────────────────────────────────────────────────────

def test_stats_skips_fields_with_fewer_than_three_samples():
    rows = [{"sleep_score": 80}, {"sleep_score": 82}]
    rows = [_row(r) for r in rows]
    assert "sleep_score" not in _stats_from_rows(rows)


def test_stats_skips_zero_variance():
    rows = [_row({"sleep_score": 80}) for _ in range(5)]
    assert "sleep_score" not in _stats_from_rows(rows)


def test_stats_population_std():
    values = [70.0, 80.0, 90.0]
    rows = [_row({"sleep_score": v}) for v in values]
    mean, std = _stats_from_rows(rows)["sleep_score"]
    assert mean == 80.0
    assert math.isclose(std, statistics.pstdev(values))


def _row(overrides: dict) -> dict:
    """daily_metrics-row stand-in: every scored field None unless overridden."""
    from garmin_readiness.history import SCORED_FIELDS
    row = {f: None for f in SCORED_FIELDS}
    row.update(overrides)
    return row


# ── composite_score ──────────────────────────────────────────────────────────

def test_composite_score_mean_of_zs():
    m = DailyMetrics(date=date(2026, 6, 1), sleep_score=85, avg_stress=45)
    stats = {"sleep_score": (80.0, 5.0), "avg_stress": (40.0, 5.0)}
    # z(sleep)=+1, z(stress)=-1 → mean 0
    assert composite_score(m, stats) == 0.0


def test_composite_score_none_without_baseline():
    m = DailyMetrics(date=date(2026, 6, 1), sleep_score=85)
    assert composite_score(m, {}) is None


# ── ftp_retest_due ───────────────────────────────────────────────────────────

def test_ftp_retest_empty_table_respects_plan_start_gate():
    today = date(2026, 6, 10)
    assert ftp_retest_due(today, plan_start=today - timedelta(days=10)) is None
    due = ftp_retest_due(today, plan_start=today - timedelta(days=22))
    assert due == {"last_date": None, "age_days": None}
    # without plan_start the empty table never fires
    assert ftp_retest_due(today) is None


def test_ftp_retest_uses_newest_test():
    save_ftp_test("2026-04-01", None, 165, 178)
    save_ftp_test("2026-06-01", None, 168, 180)
    today = date(2026, 6, 10)
    assert ftp_retest_due(today) is None  # newest is 9 days old
    later = date(2026, 7, 20)
    due = ftp_retest_due(later)
    assert due == {"last_date": "2026-06-01", "age_days": 49}


# ── weekly_monotony_strain ───────────────────────────────────────────────────

def test_monotony_none_for_untrained_week():
    weeks = weekly_monotony_strain(weeks=2)
    # no activities at all → loads all zero → stdev 0 → monotony None
    assert all(w["monotony"] is None for w in weeks)


def test_monotony_matches_foster_formula():
    import sqlite3
    from garmin_readiness.history import _conn, _ensure_activities_schema

    today = date.today()
    monday = today - timedelta(days=today.weekday())
    last_monday = monday - timedelta(weeks=1)
    loads = [100, 0, 100, 0, 100, 0, 0]
    with _conn() as con:
        _ensure_activities_schema(con)
        for i, load in enumerate(loads):
            if load:
                con.execute(
                    "INSERT INTO activities (activity_id, date, training_load) VALUES (?,?,?)",
                    (1000 + i, (last_monday + timedelta(days=i)).isoformat(), load),
                )
    wk = next(w for w in weekly_monotony_strain(weeks=2) if w["week_start"] == last_monday)
    expected_monotony = round(statistics.mean(loads) / statistics.pstdev(loads), 2)
    assert wk["weekly_load"] == 300
    assert wk["monotony"] == expected_monotony
    assert wk["strain"] == round(300 * expected_monotony)
    assert wk["days_trained"] == 3


# ── history_for_chart ────────────────────────────────────────────────────────

def test_history_for_chart_empty_db():
    results = history_for_chart(days=7)
    assert len(results) == 8
    assert all(score is None for _, score in results)


def test_history_for_chart_scores_with_baseline():
    today = date.today()
    # 10 days of identical-ish sleep history, then a clearly better night
    for i in range(10, 0, -1):
        save(DailyMetrics(date=today - timedelta(days=i),
                          sleep_score=70 + (i % 3)))  # 70/71/72 → non-zero variance
    save(DailyMetrics(date=today, sleep_score=90))
    results = dict(history_for_chart(days=7))
    assert results[today] is not None
    assert results[today] > 1.0  # well above its baseline
