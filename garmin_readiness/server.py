from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import anthropic as _anthropic
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from pydantic import BaseModel, Field

from .alerts import check_fatigue_alerts
from .analysis import generate_recovery_suggestion, load_analyses_for_activities, prefetch_fuelling_plans, prefetch_nutrition_targets, prefetch_workout_descriptions, refresh_analyses, retrieve_relevant_analyses
from .client import get_api
from .display import FIELD_LABELS, fmt_value, readiness_label, enrich_activity
from .plan import (PLAN_START as _PLAN_START, build_calendar_weeks, build_camp_weeks,
                   build_combined_event_weeks, COMPOUND_SESSIONS,
                   CAMP_GRID_WORKOUTS, EVENT_PREP_DAYS, TENERIFE_DAYS, session_for_date,
                   CAMP_START, CAMP_END)
from .hr_plan import (HR_PHASES, HR_PLAN_START, HR_TRAINING_WEEKS,
                      build_hr_calendar_weeks, build_hr_event_weeks,
                      HR_EVENT_START, HR_EVENT_END, HR_HEAT_PROTOCOL)
from .mersea_routes import MERSEA_TARGET_DATE
from .report import generate_advice, generate_body_analysis, generate_dashboard_explainer, generate_pmc_analysis, generate_pmc_explainer, generate_sleep_analysis
from .body import bp_classification, fetch_body_composition, fetch_blood_pressure
from .history import (
    ACTIVITY_MATCH,
    acclimation_latest,
    baseline_stats,
    clear_coach_history,
    composite_score,
    delete_advice,
    delete_plan_override,
    estimated_wkg_history,
    ftp_retest_due,
    get_coach_memory,
    get_plan_override,
    history_for_chart,
    intensity_distribution_by_week,
    latest_estimated_wkg,
    list_plan_overrides,
    load,
    load_activities_by_date,
    load_body_metrics,
    load_blood_pressure,
    load_btb_summary,
    load_coach_history,
    load_durability,
    load_ftp_tests,
    load_fuelling_logs,
    load_recent_activities,
    load_session_rpe,
    pmc_history,
    save_btb_note,
    save_fuelling_log,
    save_session_rpe,
    weekly_monotony_strain,
    vo2_history,
    zone_distribution,
    raw_history,
    save,
    save_activities,
    save_body_metrics,
    save_blood_pressure,
    save_coach_message,
    set_coach_memory,
    set_plan_override,
    seven_day_composite_trend_csv,
    sleep_history,
    z_score,
    get_cached_text,
    set_cached_text,
)
from .metrics import DailyMetrics, available_count, fetch_metrics, fetch_activities, TEXT_FIELDS
from .llm import MODEL_FAST, MODEL_SMART

load_dotenv()

logger = logging.getLogger(__name__)

# In-process AI-text caches. Routes are plain `def` (threadpool workers), so
# guard check-and-generate with a lock — also stops two concurrent page loads
# firing duplicate billable Claude calls for the same date.
_advice_cache: dict[str, str] = {}
_pmc_cache: dict[str, str] = {}
_ai_cache_lock = threading.Lock()

_BIKE_TYPE_KEYS = {"road_biking", "cycling", "virtual_ride", "indoor_cycling", "mountain_biking"}
_HARD_LABELS = {"Tempo Intervals", "FTP Test", "FTP Re-test"}
_HARD_SESSION_TYPES = {"tempo", "ftp", "long"}

QUALITY_BIKE_LABELS = {
    "Tempo Intervals", "Hill Repeats", "Sweetspot Ride", "Over-Unders",
    "Threshold Ride", "FTP Test", "FTP Re-test", "Final FTP Test",
}

def _week_completion() -> dict[str, Any]:
    """Return week completion stats for the dashboard card."""
    today = date.today()
    mon = today - timedelta(days=today.weekday())
    plan_min = 0
    for i in range(7):
        session = session_for_date(mon + timedelta(days=i))
        if session:
            stype, _, dur = session
            if stype != "rest" and dur:
                plan_min += dur
    if plan_min == 0:
        return {}
    acts = load_activities_by_date(mon, today - timedelta(days=1))
    done_min = 0
    for day_acts in acts.values():
        for a in day_acts:
            if any(a["type_key"] in keys for keys in ACTIVITY_MATCH.values()):
                done_min += int((a.get("duration_seconds", 0) or 0) / 60)
    pct = int(done_min / plan_min * 100)
    return {
        "plan_min_fmt": _fmt_min(plan_min),
        "done_min_fmt": _fmt_min(done_min),
        "pct": pct,
        "day_of_week": today.weekday() + 1,  # 1=Mon … 7=Sun
        "bar_filled": min(pct, 100),
    }


_OVERRIDE_ICONS = {
    "bike": "🚴", "tempo": "🚴", "ftp": "🚴", "long": "🚴",
    "strength": "🏋️", "ruck": "🎒", "rest": "—",
}


def _apply_overrides(weeks: list[dict]) -> list[dict]:
    """Patch type/label/dur_min/dur_fmt/icon for any day with a plan override."""
    overrides = {o["date"]: o for o in list_plan_overrides()}
    if not overrides:
        return weeks
    for week in weeks:
        for day in week["days"]:
            key = day["date"].isoformat()
            if key not in overrides:
                continue
            ov = overrides[key]
            dur = ov["duration_min"]
            day["dur_min"] = dur
            day["dur_fmt"] = _fmt_min(dur)
            if ov.get("session_type"):
                day["type"]  = ov["session_type"]
                day["icon"]  = _OVERRIDE_ICONS.get(ov["session_type"], "📋")
                if ov["session_type"] != "ruck":
                    day["ruck_spec"]      = None
                    day["mersea_build"]   = False
                if ov["session_type"] not in ("strength",):
                    day["kb_spec"]        = None
                    day["maxi_intervals"] = None
                day["sub_sessions"] = None
            if ov.get("label"):
                day["label"] = ov["label"]
    return weeks


def _calendar_weeks() -> list[dict]:
    return _apply_overrides(build_calendar_weeks())


def _build_calendar_ctx() -> dict[str, Any]:
    return {
        "weeks": _calendar_weeks(),
        "today": date.today(),
        "plan_start": _PLAN_START,
        "camp_weeks": build_camp_weeks(),
        "combined_event_weeks": build_combined_event_weeks(),
    }

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.filters["format_thousands"] = lambda v: f"{int(v):,}" if v is not None else "—"

_security = HTTPBasic(auto_error=False)

def _require_auth(credentials: Optional[HTTPBasicCredentials] = Depends(_security)) -> None:
    expected_user = os.getenv("DASHBOARD_USER", "")
    expected_pass = os.getenv("DASHBOARD_PASSWORD", "")
    if not expected_user or not expected_pass:
        return  # auth not configured — open access (local-only use)
    if credentials is None or not (
        secrets.compare_digest(credentials.username.encode(), expected_user.encode())
        and secrets.compare_digest(credentials.password.encode(), expected_pass.encode())
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )


app = FastAPI(
    title="Daily Readiness",
    docs_url=None,
    redoc_url=None,
    dependencies=[Depends(_require_auth)],
)

_UNSCORED = {"training_load_chronic", "vo2_max"}

_BADGE_STYLES: dict[str, str] = {
    # HRV status
    "BALANCED":   "border-emerald-600 text-emerald-300 bg-emerald-900/30",
    "UNBALANCED": "border-yellow-600 text-yellow-300 bg-yellow-900/30",
    "LOW":        "border-orange-600 text-orange-300 bg-orange-900/30",
    "POOR":       "border-red-600 text-red-300 bg-red-900/30",
    # Training status
    "PRODUCTIVE":    "border-emerald-600 text-emerald-300 bg-emerald-900/30",
    "PEAKING":       "border-emerald-600 text-emerald-300 bg-emerald-900/30",
    "MAINTAINING":   "border-blue-600 text-blue-300 bg-blue-900/30",
    "RECOVERING":    "border-blue-600 text-blue-300 bg-blue-900/30",
    "UNPRODUCTIVE":  "border-yellow-600 text-yellow-300 bg-yellow-900/30",
    "DETRAINING":    "border-orange-600 text-orange-300 bg-orange-900/30",
    "OVERREACHING":  "border-red-600 text-red-300 bg-red-900/30",
    "BELOW TARGET":  "border-yellow-600 text-yellow-300 bg-yellow-900/30",
    # ACWR
    "OPTIMAL":    "border-emerald-600 text-emerald-300 bg-emerald-900/30",
    "HIGH":       "border-orange-600 text-orange-300 bg-orange-900/30",
    "VERY HIGH":  "border-red-600 text-red-300 bg-red-900/30",
}
_DEFAULT_BADGE = "border-slate-600 text-slate-300 bg-slate-800/50"


def _badge_cls(text: str) -> str:
    return _BADGE_STYLES.get(text.upper(), _DEFAULT_BADGE)


def _value_colour(z: Optional[float]) -> str:
    if z is None:
        return "text-white"
    if z >= 1.0:
        return "text-emerald-400"
    if z >= 0.25:
        return "text-green-400"
    if z >= -0.25:
        return "text-yellow-400"
    if z >= -1.0:
        return "text-orange-400"
    return "text-red-400"


def _activity_context_blurb(activities: list[dict]) -> str:
    if not activities:
        return "No workouts cached — use force refresh to load from Garmin."
    n = len(activities)
    latest = activities[0]
    title = (latest.get("name") or latest.get("type_label") or "Activity").strip()
    d = latest.get("date") or ""
    tail = f" ({d[5:].replace('-', ' ')})" if len(d) >= 10 else ""
    if n == 1:
        return f"1 workout in last 7 days · latest: {title}{tail}"
    return f"{n} workouts in last 7 days · latest: {title}{tail}"


def _build_context(target: date, force_fetch: bool = False) -> dict[str, Any]:
    api = None
    # Load or fetch
    if force_fetch:
        email = os.getenv("GARMIN_EMAIL", "")
        password = os.getenv("GARMIN_PASSWORD", "")
        if email and password:
            api = get_api(email, password)
            m = fetch_metrics(api, target)
            save(m)
        else:
            m = load(target) or DailyMetrics(date=target)
    else:
        m = load(target) or DailyMetrics(date=target)

    stats = baseline_stats(target)
    comp_z = composite_score(m, stats)
    comp_label, comp_colour = readiness_label(comp_z)

    # Status badges
    badges: list[tuple[str, str]] = []
    if m.hrv_status:
        text = f"HRV {m.hrv_status.title()}"
        badges.append((text, _badge_cls(m.hrv_status)))
    if m.training_status_label:
        text = f"Training {m.training_status_label}"
        badges.append((text, _badge_cls(m.training_status_label)))
    if m.acwr is not None and m.acwr_status:
        status_text = m.acwr_status.replace("_", " ").title()
        text = f"ACWR {m.acwr:.2f} · {status_text}"
        badges.append((text, _badge_cls(status_text)))

    # Metric rows
    metric_rows = []
    for field, (label_str, unit) in FIELD_LABELS.items():
        value = getattr(m, field)
        val_str = fmt_value(field, value)
        context_only = field in _UNSCORED

        if field == "acwr" and m.acwr_status and value is not None:
            badge = m.acwr_status.replace("_", " ").title()
            unit = f" [{badge}]"

        if field in stats and value is not None:
            mean, std = stats[field]
            z = z_score(value, mean, std, field)
            avg_str = fmt_value(field, mean)
            col = _value_colour(z)
        else:
            z = None
            avg_str = "—"
            col = "text-white"

        metric_rows.append({
            "label": label_str,
            "value": val_str,
            "unit": unit if value is not None else "",
            "avg": avg_str,
            "z_val": z,
            "value_colour": col,
            "context_only": context_only,
        })

    # Chart data — last 14 days
    history = history_for_chart(days=14)
    chart_labels = [d.strftime("%d %b") for d, _ in history]
    chart_values = [round(v, 3) if v is not None else None for _, v in history]

    # Sparklines — last 14 days of key recovery metrics
    spark_rows = raw_history(14)
    sparklines = {
        "hrv":    [r["hrv_last_night"] for r in spark_rows],
        "sleep":  [r["sleep_score"]    for r in spark_rows],
        "stress": [r["avg_stress"]     for r in spark_rows],
        "labels": [r["date"].strftime("%-d %b") for r in spark_rows],
    }

    # Activities — last 7 days, fetch fresh if force_fetch
    if force_fetch:
        email_addr = os.getenv("GARMIN_EMAIL", "")
        password = os.getenv("GARMIN_PASSWORD", "")
        if email_addr and password:
            try:
                if api is None:
                    api = get_api(email_addr, password)
                acts_raw = fetch_activities(api, days=7)
                save_activities(acts_raw)
            except Exception:
                pass
    activities = [enrich_activity(a) for a in load_recent_activities(days=7)]

    date_key = target.isoformat()
    with _ai_cache_lock:
        if force_fetch:
            # Pop in-process cache so advice is re-read from SQLite, but keep the
            # SQLite row — re-generating advice on every refresh causes inconsistency.
            _advice_cache.pop(date_key, None)
        if date_key not in _advice_cache:
            _advice_cache[date_key] = generate_advice(m, stats, comp_z)
        advice_text = _advice_cache[date_key]

    # Today's planned session
    _SESSION_ICONS = {
        "bike": "🚴", "tempo": "🚴", "ftp": "🚴", "long": "🚴",
        "strength": "🏋️", "ruck": "🎒",
    }
    _session = session_for_date(target)
    if _session and _session[0] != "rest":
        _stype, _slabel, _sdur = _session
        today_plan: Optional[dict] = {
            "type":     _stype,
            "label":    _slabel,
            "dur_fmt":  _fmt_min(_sdur),
            "icon":     _SESSION_ICONS.get(_stype, "📋"),
            "compound": COMPOUND_SESSIONS.get(_slabel),
            "note":     get_cached_text(f"session_note_{target.isoformat()}"),
        }
    else:
        today_plan = None

    # Readiness-adjusted swap suggestion
    swap_suggestion = None
    if (today_plan and today_plan["type"] in _HARD_SESSION_TYPES
            and comp_z is not None and comp_z < -0.5):
        days_left = 6 - target.weekday()
        for offset in range(1, days_left + 1):
            candidate = target + timedelta(days=offset)
            csess = session_for_date(candidate)
            if csess and csess[0] == "bike" and csess[1] not in _HARD_LABELS:
                swap_suggestion = {
                    "from_label": today_plan["label"],
                    "to_date_str": candidate.strftime("%-d %b"),
                    "to_label": csess[1],
                    "severity": "high" if comp_z < -1.0 else "moderate",
                }
                break
        if not swap_suggestion:
            swap_suggestion = {
                "from_label": today_plan["label"],
                "to_date_str": None,
                "to_label": None,
                "severity": "high" if comp_z < -1.0 else "moderate",
            }

    # Event readiness tracker
    _EVENT_DATE = date(2026, 9, 13)
    _PLAN_DAYS  = 84
    _days_into  = (target - _PLAN_START).days
    _ws = _week_summary()
    if target >= _PLAN_START and (_EVENT_DATE - target).days > 0:
        _week_pct = _ws.get("pct") if _ws else None
        if _week_pct is None:
            _on_label, _on_col = "No data yet", "text-zinc-500"
        elif _week_pct >= 80:
            _on_label, _on_col = "On track", "text-emerald-400"
        elif _week_pct >= 50:
            _on_label, _on_col = "Slightly behind", "text-yellow-400"
        else:
            _on_label, _on_col = "Behind", "text-red-400"
        event_tracker: Optional[dict] = {
            "week_num":        min(12, max(1, _days_into // 7 + 1)),
            "plan_pct":        min(100, max(0, int(_days_into / _PLAN_DAYS * 100))),
            "days_to_event":   (_EVENT_DATE - target).days,
            "on_track_label":  _on_label,
            "on_track_colour": _on_col,
        }
    else:
        event_tracker = None

    # Fatigue alerts
    fatigue_alerts = check_fatigue_alerts(target)

    # HRV traffic light + session modulation suggestion
    traffic_light = None
    modulation = None
    try:
        from .modulation import hrv_traffic_light, session_modulation
        traffic_light = hrv_traffic_light(m, comp_z)
        modulation = session_modulation(target, m, comp_z, light=traffic_light)
    except Exception:
        pass

    # FTP retest prompt: last test stale → suggest a slot via the override flow
    ftp_retest = None
    try:
        due = ftp_retest_due(target, plan_start=_PLAN_START)
        if due:
            slot = None
            for offset in range(1, 11):
                cand = target + timedelta(days=offset)
                csess = session_for_date(cand)
                if csess and csess[0] in ("tempo", "ftp", "bike"):
                    slot = {"date": cand.isoformat(),
                            "date_str": cand.strftime("%a %-d %b"),
                            "current_label": csess[1]}
                    break
            ftp_retest = {**due, "slot": slot}
    except Exception:
        pass

    # Weekly briefing (Monday only)
    weekly_briefing: Optional[str] = None
    is_monday = target.weekday() == 0
    if is_monday:
        week_sessions = []
        for i in range(7):
            d = target + timedelta(days=i)
            sess = session_for_date(d)
            if sess and sess[0] != "rest":
                day_name = d.strftime("%a")
                week_sessions.append((day_name, sess[0], sess[1], sess[2]))
        _pmc1 = pmc_history(days=1)
        _pmc_today = _pmc1[-1] if _pmc1 else {}
        try:
            from .report import generate_weekly_briefing
            weekly_briefing = generate_weekly_briefing(week_sessions, _pmc_today, comp_z)
        except Exception:
            pass

    # Nutrition snapshot for readiness tab
    nutrition_today = None
    if m.calories_consumed is not None:
        nutrition_today = {
            "calories": int(m.calories_consumed),
            "tdee":     int(m.calorie_goal_adjusted) if m.calorie_goal_adjusted else None,
            "goal":     int(m.calorie_goal) if m.calorie_goal else None,
            "carbs":    round(m.carbs_consumed) if m.carbs_consumed is not None else None,
            "protein":  round(m.protein_consumed) if m.protein_consumed is not None else None,
        }
        if nutrition_today["tdee"] and nutrition_today["calories"]:
            nutrition_today["balance"] = nutrition_today["tdee"] - nutrition_today["calories"]

    return {
        "date": date_key,
        "date_long": target.strftime("%A, %-d %B %Y"),
        "comp_z": comp_z,
        "comp_label": comp_label,
        "comp_colour": comp_colour,
        "badges": badges,
        "acwr": m.acwr,
        "metrics": metric_rows,
        "chart_labels": chart_labels,
        "chart_values": chart_values,
        "baseline_count": len(stats),
        "activities": activities,
        "trend_note": seven_day_composite_trend_csv(),
        "activity_blurb": _activity_context_blurb(activities),
        "advice": advice_text,
        "week_summary": _ws,
        "metric_explainer": generate_dashboard_explainer(),
        "sparklines": sparklines,
        "today_plan": today_plan,
        "swap_suggestion": swap_suggestion,
        "event_tracker": event_tracker,
        "fatigue_alerts": fatigue_alerts,
        "traffic_light": traffic_light,
        "modulation": modulation,
        "ftp_retest": ftp_retest,
        "weekly_briefing": weekly_briefing,
        "is_monday": is_monday,
        "nutrition_today": nutrition_today,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, date: Optional[str] = None, msg: Optional[str] = None,
                    n: Optional[int] = None):
    target = date_fromisoformat_safe(date) if date else _today()
    ctx = _build_context(target)
    ctx["flash_msg"] = msg
    ctx["flash_n"] = n
    return TEMPLATES.TemplateResponse(request=request, name="dashboard.html", context=ctx)


@app.get("/send-email", response_class=RedirectResponse)
def send_email_now():
    from pathlib import Path
    today = _today()
    sentinel = Path.home() / ".garmin_readiness" / f"sent_{today.isoformat()}"
    if sentinel.exists():
        return RedirectResponse(url="/?msg=already_sent", status_code=303)

    email_addr = os.getenv("GARMIN_EMAIL", "")
    password = os.getenv("GARMIN_PASSWORD", "")
    m = None
    if email_addr and password:
        try:
            api = get_api(email_addr, password)
            raw = fetch_metrics(api, today)
            if raw:
                save(raw)
                m = raw
        except Exception:
            pass
    if m is None:
        m = load(today)

    if m is None or (m.sleep_score is None and m.body_battery_morning is None):
        return RedirectResponse(url="/?msg=no_data", status_code=303)

    # Sentinel before send: a crash after SMTP delivery must not allow a
    # duplicate; on a clean failure the sentinel is removed so retry works.
    sentinel.touch()
    try:
        from .report import run_report
        run_report(m, dry_run=False)
        return RedirectResponse(url="/?msg=sent", status_code=303)
    except Exception as e:
        sentinel.unlink(missing_ok=True)
        logger.error("send-email failed: %s", e)
        return RedirectResponse(url="/?msg=error", status_code=303)


@app.get("/sync-workouts", response_class=RedirectResponse)
async def sync_workouts_now():
    """Re-upload and re-schedule all plan cycling workouts to Garmin, applying any
    coach plan overrides. Manual trigger only (button / CLI). Outward-facing — mutates
    the athlete's Garmin Connect calendar."""
    from fastapi.concurrency import run_in_threadpool
    from .workouts import upload_and_schedule

    email_addr = os.getenv("GARMIN_EMAIL", "")
    password = os.getenv("GARMIN_PASSWORD", "")
    if not (email_addr and password):
        return RedirectResponse(url="/?msg=no_garmin", status_code=303)

    try:
        api = get_api(email_addr, password)
        summary = await run_in_threadpool(upload_and_schedule, api)
        n = summary.get("scheduled", 0)
        return RedirectResponse(url=f"/?msg=synced&n={n}", status_code=303)
    except Exception as e:
        logger.error("sync-workouts failed: %s", e)
        return RedirectResponse(url="/?msg=sync_error", status_code=303)


def _merge_compound_activities(activities: list[dict]) -> list[dict]:
    """Collapse compound session pairs (e.g. KB + MaxiClimber) into one card."""
    # Build a reverse lookup: garmin type_key → compound session label
    key_to_label: dict[str, str] = {}
    for label, subs in COMPOUND_SESSIONS.items():
        for sub in subs:
            key_to_label[sub["garmin_key"]] = label

    # Index activities by (date, compound_label) to find pairs
    compound_groups: dict[tuple[str, str], list[dict]] = {}
    non_compound: list[dict] = []
    for act in activities:
        label = key_to_label.get(act.get("type_key", ""))
        if label:
            key = (act.get("date", ""), label)
            compound_groups.setdefault(key, []).append(act)
        else:
            non_compound.append(act)

    merged: list[dict] = []
    for (act_date, label), group in compound_groups.items():
        if len(group) == 1:
            non_compound.append(group[0])
            continue
        # Primary = the one with analysis_text (prefer strength_training)
        subs = COMPOUND_SESSIONS[label]
        primary_key = subs[0]["garmin_key"]
        primary = next((a for a in group if a.get("type_key") == primary_key), group[0])
        others = [a for a in group if a["activity_id"] != primary["activity_id"]]

        combined = dict(primary)
        combined["name"] = label
        # Sum duration and calories
        total_secs = sum(a.get("duration_seconds") or 0 for a in group)
        combined["duration_seconds"] = total_secs
        from .display import fmt_duration
        combined["duration_fmt"] = fmt_duration(total_secs)
        combined["calories"] = sum((a.get("calories") or 0) for a in group)
        # Attach companion HR zones for template rendering
        # Build ordered zone sections (one per sub-session) for the template
        acts_by_key = {a["type_key"]: a for a in group}
        combined["zone_sections"] = [
            {"label": sub["label"], "zones": acts_by_key.get(sub["garmin_key"], {}).get("hr_zones", [])}
            for sub in subs
        ]
        merged.append(combined)

    # Restore original order (newest first)
    all_acts = non_compound + merged
    all_acts.sort(key=lambda a: a.get("start_time") or a.get("date", ""), reverse=True)
    return all_acts


@app.get("/analysis", response_class=HTMLResponse)
def analysis_view(request: Request):
    activities_raw = load_recent_activities(days=14)
    activities = load_analyses_for_activities(
        [enrich_activity(a) for a in activities_raw]
    )
    activities = _merge_compound_activities(activities)
    rpe_rows = load_session_rpe(30)
    rpe_by_activity = {str(r["activity_id"]): r for r in rpe_rows if r.get("activity_id") is not None}

    # Fuelling compliance: attach the cached in-ride plan + any logged actuals
    # to qualifying endurance rides (bike types, ≥75 min planned sessions).
    try:
        from .analysis import _load_fuelling_plans, fuelling_session_key
        from .plan import session_for_date_extended
        _BIKE_TYPES = {"road_biking", "cycling", "virtual_ride", "indoor_cycling", "mountain_biking"}
        fuel_plans = _load_fuelling_plans()
        fuel_logs = {str(r["activity_id"]): r for r in load_fuelling_logs(90)
                     if r.get("activity_id") is not None}
        for a in activities:
            if a.get("type_key") not in _BIKE_TYPES:
                continue
            if (a.get("duration_seconds") or 0) < 75 * 60:
                continue
            try:
                sess = session_for_date_extended(date.fromisoformat(a["date"]))
            except Exception:
                sess = None
            if sess and sess[0] != "rest" and sess[2]:
                plan = fuel_plans.get(fuelling_session_key(sess[0], sess[2]))
                if plan:
                    a["planned_fuel"] = plan
            a["fuel_log"] = fuel_logs.get(str(a.get("activity_id")))
    except Exception:
        pass

    return TEMPLATES.TemplateResponse(
        request=request,
        name="analysis.html",
        context={
            "activities": activities,
            "zone_dist": zone_distribution(days=7),
            "rpe_by_activity": rpe_by_activity,
        },
    )


@app.post("/log-rpe")
async def log_rpe_endpoint(request: Request, _=Depends(_require_auth)):
    body = await request.json()
    save_session_rpe(body["date"], body.get("activity_id"), body["rpe"], body.get("note"))
    return JSONResponse({"ok": True})


class _FuellingLogRequest(BaseModel):
    date: str
    activity_id: Optional[int] = None
    planned_carbs_g_per_hr: Optional[float] = Field(None, ge=0, le=300)
    actual_carbs_g_per_hr: Optional[float] = Field(None, ge=0, le=300)
    fluid_ok: bool = False
    note: Optional[str] = None


@app.post("/log-fuelling")
async def log_fuelling_endpoint(body: _FuellingLogRequest, _=Depends(_require_auth)):
    try:
        date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    save_fuelling_log(
        body.date,
        body.activity_id,
        body.planned_carbs_g_per_hr,
        body.actual_carbs_g_per_hr,
        body.fluid_ok,
        body.note,
    )
    return JSONResponse({"ok": True})


@app.get("/api/ftp-tests")
async def api_ftp_tests(_=Depends(_require_auth)):
    return JSONResponse(load_ftp_tests())


@app.post("/log-btb")
async def log_btb_endpoint(request: Request, _=Depends(_require_auth)):
    body = await request.json()
    save_btb_note(body["date"], body.get("day_number", 1), body.get("fatigue_rating"), body.get("note"))
    return JSONResponse({"ok": True})


@app.get("/btb-summary")
async def btb_summary_view(_=Depends(_require_auth)):
    return JSONResponse(load_btb_summary())


@app.get("/analysis-refresh", response_class=RedirectResponse)
def analysis_refresh():
    email_addr = os.getenv("GARMIN_EMAIL", "")
    password = os.getenv("GARMIN_PASSWORD", "")
    if email_addr and password:
        api = get_api(email_addr, password)
        activities_raw = load_recent_activities(days=14)
        if activities_raw:
            try:
                from .metrics import fetch_activities
                acts_raw = fetch_activities(api, days=14)
                save_activities(acts_raw)
            except Exception:
                pass
        try:
            refresh_analyses(api, days=14)
        except Exception:
            pass
    return RedirectResponse(url="/analysis", status_code=303)


@app.get("/performance", response_class=HTMLResponse)
async def performance_view(request: Request):
    history = pmc_history(days=90)
    today_entry = history[-1] if history else {}
    date_key = date.today().isoformat()
    with _ai_cache_lock:
        if date_key not in _pmc_cache:
            m_today = load(date.today()) or DailyMetrics(date=date.today())
            stats_today = baseline_stats(date.today())
            comp_z_today = composite_score(m_today, stats_today)
            _pmc_cache[date_key] = generate_pmc_analysis(history, m_today, comp_z_today)
        pmc_analysis_text = _pmc_cache[date_key]

    plan_acts = load_activities_by_date(_PLAN_START, date.today())
    z2_points: list[dict] = []
    for date_str, acts in sorted(plan_acts.items()):
        for act in acts:
            if act["type_key"] in _BIKE_TYPE_KEYS and act.get("avg_hr"):
                d = date.fromisoformat(date_str)
                plan_sess = session_for_date(d)
                sess_label = plan_sess[1] if plan_sess else (act.get("name") or "Bike")
                z2_points.append({
                    "date": date_str,
                    "avg_hr": round(act["avg_hr"]),
                    "label": sess_label,
                    "hard": sess_label in _HARD_LABELS,
                })

    proj_data: list[dict] = []
    event_ctl: Optional[float] = None
    _ctl_now = today_entry.get("ctl")
    _atl_now = today_entry.get("atl")
    if _ctl_now is not None and _atl_now is not None:
        proj_data, event_ctl = _ctl_projection(_ctl_now, _atl_now)

    # Per-activity training load for bar chart (last 60 days)
    load_acts = load_activities_by_date(date.today() - timedelta(days=60), date.today())
    load_chart_data = []
    _BIKE_KEYS = {"road_biking", "cycling", "virtual_ride", "indoor_cycling", "mountain_biking"}
    _RUCK_KEYS = {"hiking", "walking", "rucking", "load_carry"}
    _STR_KEYS  = {"strength_training", "stair_climbing", "fitness_equipment"}
    for date_str in sorted(load_acts.keys()):
        for a in load_acts[date_str]:
            if not a.get("training_load"):
                continue
            tk = a.get("type_key", "")
            colour = (
                "rgba(96,165,250,0.75)"  if tk in _BIKE_KEYS else
                "rgba(163,230,53,0.75)"  if tk in _RUCK_KEYS else
                "rgba(167,139,250,0.75)" if tk in _STR_KEYS  else
                "rgba(251,146,60,0.75)"
            )
            load_chart_data.append({
                "label":  date.fromisoformat(date_str).strftime("%-d %b"),
                "load":   round(a["training_load"], 1),
                "name":   a.get("name") or a.get("type_key", ""),
                "colour": colour,
            })

    # Z2 cardiac drift trend: server-side regression on easy rides only
    easy_points = [p for p in z2_points if not p.get("hard")]
    z2_trend_line: list[dict] = []
    z2_drift_annotation: Optional[str] = None
    if len(easy_points) >= 3:
        fit = _ols([p["avg_hr"] for p in easy_points])
        if fit:
            slope, intercept = fit
            n = len(easy_points)
            z2_trend_line = [
                {"date": easy_points[0]["date"], "hr": round(intercept, 1)},
                {"date": easy_points[-1]["date"], "hr": round(slope * (n - 1) + intercept, 1)},
            ]
            drop = round(intercept - (slope * (n - 1) + intercept), 1)
            if slope < 0:
                z2_drift_annotation = f"−{abs(drop):.1f} bpm since {easy_points[0]['date']}"
            else:
                z2_drift_annotation = "No improvement yet"

    # Durability: late-ride HR drift trend (≥90 min rides)
    durability_points = load_durability(180)
    durability_trend: list[dict] = []
    if len(durability_points) >= 3:
        fit = _ols([p["drift_pct"] for p in durability_points])
        if fit:
            slope, intercept = fit
            n = len(durability_points)
            durability_trend = [
                {"date": durability_points[0]["date"], "v": round(intercept, 2)},
                {"date": durability_points[-1]["date"], "v": round(slope * (n - 1) + intercept, 2)},
            ]

    # Estimated W/kg + monotony + acclimation
    wkg_history = estimated_wkg_history(180)
    monotony_weeks = weekly_monotony_strain(8)
    acclimation = acclimation_latest()

    # Taper scenario simulator (presets over the final 14 days)
    taper_scenarios: list[dict] = []
    if _ctl_now is not None and _atl_now is not None:
        try:
            taper_scenarios = _taper_scenarios(_ctl_now, _atl_now)
        except Exception:
            pass

    # Intensity distribution by week
    zone_dist_by_week = intensity_distribution_by_week(_PLAN_START, date.today())
    zone_dist_block = _block_zone_totals(zone_dist_by_week)

    return TEMPLATES.TemplateResponse(
        request=request,
        name="performance.html",
        context={
            "history": history,
            "today": today_entry,
            "pmc_analysis": pmc_analysis_text,
            "pmc_explainer": generate_pmc_explainer(),
            "z2_points": z2_points,
            "z2_trend_line": z2_trend_line,
            "z2_drift_annotation": z2_drift_annotation,
            "proj_data": proj_data,
            "event_ctl": event_ctl,
            "load_chart_data": load_chart_data,
            "event_date_label": _PLAN_EVENT_DATE.strftime("%-d %b %Y"),
            "camp_start_label": date(2026, 8, 13).strftime("%-d %b"),
            "camp_end_label":   date(2026, 8, 27).strftime("%-d %b"),
            "event_prep_label": date(2026, 8, 31).strftime("%-d %b"),
            "vo2_history": vo2_history(days=90),
            "zone_dist_by_week": zone_dist_by_week,
            "zone_dist_block": zone_dist_block,
            "durability_points": durability_points,
            "durability_trend": durability_trend,
            "wkg_history": wkg_history,
            "monotony_weeks": monotony_weeks,
            "acclimation": acclimation,
            "taper_scenarios": taper_scenarios,
        },
    )


def _ols(ys: list[float]) -> Optional[tuple[float, float]]:
    """Ordinary least squares on (index, value). Returns (slope, intercept) or None."""
    n = len(ys)
    if n < 2:
        return None
    xs = list(range(n))
    sx, sy = sum(xs), sum(ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sx2 = sum(x * x for x in xs)
    denom = n * sx2 - sx * sx
    if not denom:
        return None
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


_PLAN_EVENT_DATE = date(2026, 9, 13)

# CTL delta per training minute by session type.
# Calibrated from week-1 observed data: Easy Spin 60min→+15, Zone2 60min→+26,
# KB+MaxiClimber 45min→+33, Ruck+KB 105min→+33. Rest days average -3.
_CTL_PER_MIN: dict[str, float] = {
    "bike":     0.32,   # easy spin / Z2
    "long":     0.40,   # Z2 long ride
    "tempo":    0.58,   # tempo effort
    "ftp":      0.78,   # threshold
    "strength": 0.70,   # KB / strength (high EPOC)
    "ruck":     0.30,   # hiking / ruck
}
_CTL_REST_DECLINE = -3.5


_TENERIFE_BY_DATE: dict = {}   # populated lazily below
_EVENT_PREP_BY_DATE: dict = {}

def _build_lookup_dicts() -> None:
    global _TENERIFE_BY_DATE, _EVENT_PREP_BY_DATE
    # Intensity → session type mapping for Tenerife days
    _intensity_type = {"easy": "bike", "medium": "bike", "hard": "long"}
    for day in TENERIFE_DAYS:
        intensity = day.get("intensity", "rest")
        stype = _intensity_type.get(intensity)
        if stype:
            km = day.get("km", 0) or 0
            elev = day.get("elev_m", 0) or 0
            # Duration estimate: flat km at 25 km/h + climbing at 700 m/h
            dur_min = int((km / 25 + elev / 700) * 60)
            _TENERIFE_BY_DATE[day["date"]] = (stype, day["label"], max(dur_min, 30))
    for day in EVENT_PREP_DAYS:
        _EVENT_PREP_BY_DATE[day["date"]] = (day["type"], day["label"], day["dur_min"])
    for day in CAMP_GRID_WORKOUTS.values():
        pass  # handled via session_for_date for the pre/post camp days

_build_lookup_dicts()


def _session_for_projection(d) -> tuple[str, str, int] | None:
    """Return (type, label, dur_min) for any plan day — 12-week plan, camp, or event prep."""
    sess = session_for_date(d)
    if sess:
        return sess
    if d in _TENERIFE_BY_DATE:
        return _TENERIFE_BY_DATE[d]
    if d in _EVENT_PREP_BY_DATE:
        return _EVENT_PREP_BY_DATE[d]
    # CAMP_GRID_WORKOUTS (pre/post camp activation rides)
    cg = CAMP_GRID_WORKOUTS.get(d)
    if cg:
        return (cg["type"], cg["label"], cg["dur_min"])
    return None


def _ctl_projection(current_ctl: float, current_atl: float,
                    modifier=None) -> tuple[list[dict], float]:
    """Project CTL/ATL/TSB from today to event day using all plan sessions including Tenerife camp.

    Uses additive deltas calibrated against observed week-1 data rather than
    the standard Coggan EMA, because Garmin's CTL units don't follow the
    standard TSS-based scale. A soft ceiling (diminishing returns above CTL 300)
    prevents runaway growth.

    `modifier` (optional) is applied to each (date, session_tuple) before the
    rate maths: return a replacement tuple, or None to treat the day as rest.
    Used by the taper scenario simulator.
    """
    import math as _math
    today = date.today()
    days_ahead = (_PLAN_EVENT_DATE - today).days
    if days_ahead <= 0:
        return [], round(current_ctl, 1)

    ctl = current_ctl
    atl = current_atl
    result = []
    for i in range(1, days_ahead + 1):
        d = today + timedelta(days=i)
        sess = _session_for_projection(d)
        if modifier is not None and sess is not None:
            sess = modifier(d, sess)
        if sess and sess[0] != "rest":
            stype, _, dur_min = sess
            rate = _CTL_PER_MIN.get(stype, 0.35)
            ceiling = (300 / max(ctl, 300)) ** 2
            delta = rate * (dur_min or 0) * ceiling
            atl_delta = rate * (dur_min or 0)
            atl = max(0.0, atl * _math.exp(-1 / 7) + atl_delta)
        else:
            delta = _CTL_REST_DECLINE
            atl = max(0.0, atl * _math.exp(-1 / 7))
        ctl = max(0.0, ctl + delta)
        tsb = round(ctl - atl, 1)
        result.append({
            "label": d.strftime("%-d %b"),
            "ctl":   round(ctl, 1),
            "atl":   round(atl, 1),
            "tsb":   tsb,
        })
    return result, round(result[-1]["ctl"], 1) if result else round(current_ctl, 1)


def _taper_scenarios(current_ctl: float, current_atl: float) -> list[dict]:
    """Three preset what-if projections over the final 14 days before the event.

    Turns the TSB projection from a chart into a decision tool: target landing
    zone on event morning is roughly TSB −5 to +15.
    """
    taper_start = _PLAN_EVENT_DATE - timedelta(days=14)
    final_week = _PLAN_EVENT_DATE - timedelta(days=7)

    scenarios = []

    # 1. As planned
    series, ctl_event = _ctl_projection(current_ctl, current_atl)
    if not series:
        return []
    scenarios.append({"name": "As planned", "series": series,
                      "tsb_event": series[-1]["tsb"], "ctl_event": ctl_event})

    # 2. Drop the first quality session (tempo/ftp) inside the final 14 days
    dropped = {"done": False}

    def _drop_quality(d, sess):
        if (not dropped["done"] and d >= taper_start
                and sess and sess[0] in ("tempo", "ftp")):
            dropped["done"] = True
            return None
        return sess

    series2, ctl2 = _ctl_projection(current_ctl, current_atl, modifier=_drop_quality)
    scenarios.append({"name": "Drop one quality session", "series": series2,
                      "tsb_event": series2[-1]["tsb"] if series2 else None,
                      "ctl_event": ctl2})

    # 3. Halve final-week volume
    def _halve_final_week(d, sess):
        if d >= final_week and sess and sess[0] != "rest":
            stype, label, dur = sess
            return (stype, label, max(15, (dur or 0) // 2))
        return sess

    series3, ctl3 = _ctl_projection(current_ctl, current_atl, modifier=_halve_final_week)
    scenarios.append({"name": "Halve final-week volume", "series": series3,
                      "tsb_event": series3[-1]["tsb"] if series3 else None,
                      "ctl_event": ctl3})

    return scenarios


def _block_zone_totals(weeks: list[dict]) -> dict:
    """Aggregate zone distribution across all weeks to block-level percentages."""
    totals = [0.0] * 5
    for w in weeks:
        for i in range(1, 6):
            totals[i - 1] += w.get(f"z{i}_sec", 0.0)
    total = sum(totals)
    if total == 0:
        return {}
    return {
        "z1_pct": round(totals[0] / total * 100, 1),
        "z2_pct": round(totals[1] / total * 100, 1),
        "z3_pct": round(totals[2] / total * 100, 1),
        "z4_pct": round(totals[3] / total * 100, 1),
        "z5_pct": round(totals[4] / total * 100, 1),
    }


_BIKE_TYPES = {"bike", "tempo", "ftp", "long"}

# CTL rates for Haute Route plan session types.
# Reuses calibrated values from _CTL_PER_MIN where keys overlap.
_HR_CTL_PER_MIN: dict[str, float] = {
    "endurance":    0.32,   # Z2 steady (same as "bike")
    "recovery":     0.25,   # recovery spin / easy core
    "sweetspot":    0.45,   # sweetspot intervals
    "tempo":        0.58,   # tempo / under-overs (same as "tempo")
    "vo2":          0.65,   # VO2max intervals
    "long":         0.40,   # long ride (same as "long")
    "back_to_back": 0.40,   # multi-hour back-to-back days
    "ftp":          0.78,   # threshold test (same as "ftp")
    "gym":          0.55,   # gym strength session
}


def _hr_ctl_projection(starting_ctl: float) -> list[dict]:
    """Project CTL across all 46 HR plan weeks, returning one point per week (Sunday)."""
    ctl = starting_ctl
    result = []
    for wk_idx, sessions in enumerate(HR_TRAINING_WEEKS):
        week_num = wk_idx + 1
        for stype, _, dur_min in sessions:
            if stype != "rest":
                rate = _HR_CTL_PER_MIN.get(stype, 0.35)
                ceiling = (300 / max(ctl, 300)) ** 2
                ctl = max(0.0, ctl + rate * dur_min * ceiling)
            else:
                ctl = max(0.0, ctl + _CTL_REST_DECLINE)
        week_end = HR_PLAN_START + timedelta(weeks=wk_idx, days=6)
        result.append({
            "label":    week_end.strftime("%-d %b"),
            "ctl":      round(ctl, 1),
            "week":     week_num,
        })
    return result

# Map Garmin type_key → display session type for pre-plan activity cells
_TYPE_KEY_SESSION: dict[str, str] = {
    "road_biking": "bike", "cycling": "bike", "virtual_ride": "bike",
    "indoor_cycling": "bike", "mountain_biking": "bike",
    "strength_training": "strength", "stair_climbing": "strength", "fitness_equipment": "strength",
    "hiking": "ruck", "walking": "ruck", "trail_running": "ruck", "running": "ruck",
}

_PRE_PLAN_WEEKS = 4


def _fmt_dur(seconds: float) -> str:
    m = int(seconds / 60)
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h{m % 60:02d}m" if m % 60 else f"{m // 60}h"


def _fmt_min(minutes: int) -> str:
    if minutes == 0:
        return "—"
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h{m:02d}m" if m else f"{h}h"


def _build_preplan_weeks(acts_by_date: dict) -> list[dict]:
    today = date.today()
    start = _PLAN_START - timedelta(weeks=_PRE_PLAN_WEEKS)
    start -= timedelta(days=start.weekday())
    weeks = []
    d = start
    while d < _PLAN_START:
        wk_days = []
        done_min = 0
        for i in range(7):
            day_date = d + timedelta(days=i)
            if day_date >= _PLAN_START:
                break
            day_acts = acts_by_date.get(day_date.isoformat(), [])
            primary = max(day_acts, key=lambda a: a.get("duration_seconds") or 0) if day_acts else None
            if primary:
                stype = _TYPE_KEY_SESSION.get(primary["type_key"], "rest")
                dur_fmt = _fmt_dur(primary.get("duration_seconds") or 0)
                label = primary.get("name") or stype.title()
                extra = len(day_acts) - 1
                actual_min = int(sum(a.get("duration_seconds", 0) or 0 for a in day_acts) / 60)
                done_min += actual_min
            else:
                stype, dur_fmt, label, extra, actual_min = "rest", "", "", 0, 0
            wk_days.append({
                "date": day_date,
                "day_num": day_date.day,
                "month_abbr": day_date.strftime("%b"),
                "is_today": day_date == today,
                "type": stype,
                "label": label,
                "dur_fmt": dur_fmt,
                "extra": extra,
                "actual_min": actual_min,
            })
        weeks.append({"start": d, "days": wk_days, "done_min_fmt": _fmt_min(done_min)})
        d += timedelta(weeks=1)
    return weeks


@app.get("/calendar", response_class=HTMLResponse)
async def calendar_view(request: Request):
    ctx = _build_calendar_ctx()
    _CLICKABLE_TYPES = _BIKE_TYPES | {"strength"}
    session_labels = {
        day["label"]
        for week in ctx["weeks"]
        for day in week["days"]
        if day["type"] in _CLICKABLE_TYPES
    }
    session_labels |= {d["label"] for d in EVENT_PREP_DAYS if d["type"] in _BIKE_TYPES}
    session_labels |= {v["label"] for v in CAMP_GRID_WORKOUTS.values() if v["type"] in _BIKE_TYPES}
    ctx["workout_descs"] = prefetch_workout_descriptions(list(session_labels))

    # Pre-plan history (4 weeks before plan start)
    pre_start = _PLAN_START - timedelta(weeks=_PRE_PLAN_WEEKS)
    pre_start -= timedelta(days=pre_start.weekday())
    preplan_acts = load_activities_by_date(pre_start, _PLAN_START - timedelta(days=1))
    ctx["preplan_weeks"] = _build_preplan_weeks(preplan_acts)

    # Load all activities across the plan window and mark completion + actual durations
    plan_end = ctx["weeks"][-1]["days"][-1]["date"]
    acts_by_date = load_activities_by_date(_PLAN_START, plan_end)
    today = date.today()
    for week in ctx["weeks"]:
        plan_min = sum(d["dur_min"] for d in week["days"] if d["type"] != "rest")
        done_min = 0
        for day in week["days"]:
            stype = day["type"]
            if stype == "rest" or day["date"] > today:
                day["completed"] = None
                day["actual_min"] = None
                for sub in (day.get("sub_sessions") or []):
                    sub["completed"] = None
                    sub["actual_min"] = None
            else:
                day_acts = acts_by_date.get(day["date"].isoformat(), [])
                if day.get("sub_sessions"):
                    for sub in day["sub_sessions"]:
                        sub_matched = [a for a in day_acts if a["type_key"] == sub["garmin_key"]]
                        sub["completed"] = bool(sub_matched)
                        sub["actual_min"] = (
                            int(sum(a.get("duration_seconds", 0) or 0 for a in sub_matched) / 60)
                            if sub_matched else None
                        )
                    day["completed"] = any(s["completed"] for s in day["sub_sessions"])
                    actual = sum(s["actual_min"] or 0 for s in day["sub_sessions"])
                    day["actual_min"] = actual if actual else None
                    done_min += actual
                else:
                    valid_keys = ACTIVITY_MATCH.get(stype, set())
                    matched = [a for a in day_acts if a["type_key"] in valid_keys]
                    day["completed"] = bool(matched)
                    actual = int(sum(a.get("duration_seconds", 0) or 0 for a in matched) / 60)
                    day["actual_min"] = actual if matched else None
                    done_min += actual
        week["plan_min_fmt"] = _fmt_min(plan_min)
        week["done_min_fmt"] = _fmt_min(done_min) if done_min else None
        week["completion_pct"] = int(done_min / plan_min * 100) if plan_min and done_min else None
        week["days_hit"]   = sum(1 for d in week["days"] if d.get("completed") == True)
        week["days_total"] = sum(1 for d in week["days"] if d["type"] != "rest" and d["date"] <= today)

    # Current streak: consecutive completed (or rest) days up to and including today
    all_plan_days = [d for week in ctx["weeks"] for d in week["days"]]
    current_streak = 0
    for day in reversed(all_plan_days):
        if day["date"] > today:
            continue
        if day["type"] == "rest" or day.get("completed") == True:
            current_streak += 1
        else:
            break
    ctx["current_streak"] = current_streak

    # Interference flags: quality bike session within 24h of strength
    _STRENGTH_KEYS = {"strength_training", "stair_climbing"}
    for week in ctx["weeks"]:
        for day in week["days"]:
            if day["label"] not in QUALITY_BIKE_LABELS:
                continue
            date_str = day["date"].isoformat()
            prev_date_str = (day["date"] - timedelta(days=1)).isoformat()
            same_day_acts = acts_by_date.get(date_str, [])
            prev_day_acts = acts_by_date.get(prev_date_str, [])
            if any(a["type_key"] in _STRENGTH_KEYS for a in same_day_acts + prev_day_acts):
                day["interference"] = True
                day["interference_note"] = "Strength logged within 24h of quality bike session"

    # Back-to-back consecutive cycling day pairs
    btb_pairs = load_btb_summary()
    yesterday = (today - timedelta(days=1)).isoformat()
    btb_log_available = bool(acts_by_date.get(yesterday)) and any(
        a["type_key"] in _BIKE_TYPE_KEYS for a in acts_by_date.get(yesterday, [])
    )
    ctx["btb_pairs"] = btb_pairs
    ctx["btb_log_available"] = btb_log_available

    # Build single unified weeks list (plan → camp → event prep) with phase tags.
    # Plan weeks are reused after completion tracking, so their day dicts already have
    # completed/actual_min populated.
    unified: list[dict] = []
    for w in ctx["weeks"]:
        unified.append({**w, "phase": "plan", "phase_start": w["week_num"] == 1})
    for i, w in enumerate(ctx["camp_weeks"]):
        unified.append({**w, "phase": "camp", "phase_start": i == 0})
    for i, w in enumerate(ctx["combined_event_weeks"]):
        unified.append({**w, "phase": "event_prep", "phase_start": i == 0})
    ctx["unified_weeks"] = unified

    # Per-day pacing & fuelling plans for the two charity-ride days (AI, cached).
    charity_plans: list[dict] = []
    try:
        from .analysis import generate_charity_day_plans
        from .plan import CHARITY_DAYS
        plans = generate_charity_day_plans()
        for cd in CHARITY_DAYS:
            plan = plans.get(cd["day"])
            if plan:
                charity_plans.append({**cd, "plan": plan})
    except Exception:
        pass
    ctx["charity_plans"] = charity_plans

    return TEMPLATES.TemplateResponse(request=request, name="calendar.html", context=ctx)


def _plan_completion_stats() -> dict:
    """Compute per-week plan vs actual completion stats for the training page."""
    today = date.today()
    weeks_data = _calendar_weeks()
    plan_end = weeks_data[-1]["days"][-1]["date"]
    acts_by_date = load_activities_by_date(_PLAN_START, min(today, plan_end))

    completion_weeks = []
    total_plan_sessions = total_done_sessions = 0
    total_plan_min = total_done_min = 0

    for week in weeks_data:
        wk_start: date = week["start"]
        wk_end: date = wk_start + timedelta(days=6)

        # Date range label
        if wk_start.month == wk_end.month:
            date_range = f"{wk_start.day}–{wk_end.day} {wk_start.strftime('%b')}"
        else:
            date_range = f"{wk_start.strftime('%-d %b')}–{wk_end.strftime('%-d %b')}"

        plan_sessions = plan_min = done_sessions = done_min = 0
        day_statuses = []

        for day in week["days"]:
            d: date = day["date"]
            stype = day["type"]
            is_future = d > today
            is_rest = stype == "rest"

            status = "rest" if is_rest else ("future" if is_future else "pending")
            completed = None

            if not is_rest and not is_future:
                plan_sessions += 1
                plan_min += day["dur_min"]
                day_acts = acts_by_date.get(d.isoformat(), [])
                if day.get("sub_sessions"):
                    matched = any(
                        any(a["type_key"] == sub["garmin_key"] for a in day_acts)
                        for sub in day["sub_sessions"]
                    )
                    actual = int(sum(a.get("duration_seconds", 0) or 0 for a in day_acts) / 60)
                else:
                    valid_keys = ACTIVITY_MATCH.get(stype, set())
                    matched_acts = [a for a in day_acts if a["type_key"] in valid_keys]
                    matched = bool(matched_acts)
                    actual = int(sum(a.get("duration_seconds", 0) or 0 for a in matched_acts) / 60)
                completed = matched
                if matched:
                    done_sessions += 1
                    done_min += actual
                status = "done" if matched else "missed"

            day_statuses.append({
                "type": stype,
                "date": d,
                "status": status,
                "is_today": d == today,
            })

        total_plan_sessions += plan_sessions
        total_done_sessions += done_sessions
        total_plan_min += plan_min
        total_done_min += done_min

        if wk_start > today:
            wk_status = "future"
        elif wk_end >= today:
            wk_status = "current"
        else:
            wk_status = "past"

        completion_weeks.append({
            "week_num": week["week_num"],
            "date_range": date_range,
            "plan_sessions": plan_sessions,
            "done_sessions": done_sessions,
            "plan_min": plan_min,
            "done_min": done_min,
            "pct": int(done_min / plan_min * 100) if plan_min else 0,
            "status": wk_status,
            "days": day_statuses,
        })

    overall_pct = int(total_done_min / total_plan_min * 100) if total_plan_min else 0
    return {
        "completion_weeks": completion_weeks,
        "total_plan_sessions": total_plan_sessions,
        "total_done_sessions": total_done_sessions,
        "total_plan_min": total_plan_min,
        "total_done_min": total_done_min,
        "overall_pct": overall_pct,
    }


@app.get("/training", response_class=HTMLResponse)
async def training_plan(request: Request):
    ctx = _plan_completion_stats()
    return TEMPLATES.TemplateResponse(request=request, name="training.html", context=ctx)


_BIKE_SESSION_TYPES = {"bike", "tempo", "ftp", "long"}


@app.get("/compliance", response_class=HTMLResponse)
async def compliance_view(request: Request):
    ctx = _plan_completion_stats()

    # Per-discipline breakdown from day statuses
    by_type: dict[str, dict] = {
        "bike":     {"label": "Bike", "icon": "🚴", "plan": 0, "done": 0},
        "strength": {"label": "Strength", "icon": "🏋️", "plan": 0, "done": 0},
        "ruck":     {"label": "Ruck", "icon": "🎒", "plan": 0, "done": 0},
    }
    for wk in ctx["completion_weeks"]:
        for day in wk["days"]:
            if day["status"] not in ("done", "missed"):
                continue
            bucket = "bike" if day["type"] in _BIKE_SESSION_TYPES else day["type"]
            if bucket not in by_type:
                continue
            by_type[bucket]["plan"] += 1
            if day["status"] == "done":
                by_type[bucket]["done"] += 1
    for bt in by_type.values():
        bt["pct"] = int(bt["done"] / bt["plan"] * 100) if bt["plan"] else 0
    ctx["by_type"] = list(by_type.values())

    # Current streak (consecutive done/rest days working backwards from today)
    streak = 0
    all_days = [d for wk in ctx["completion_weeks"] for d in wk["days"]]
    for day in reversed(all_days):
        if day["status"] == "future":
            continue
        if day["status"] in ("done", "rest"):
            streak += 1
        else:
            break
    ctx["streak"] = streak

    # Cumulative adherence % per week (None for future weeks)
    cum_plan = cum_done = 0
    cumulative: list[Optional[int]] = []
    for wk in ctx["completion_weeks"]:
        if wk["status"] == "future":
            cumulative.append(None)
        else:
            cum_plan += wk["plan_min"]
            cum_done += wk["done_min"]
            cumulative.append(int(cum_done / cum_plan * 100) if cum_plan else 0)
    ctx["cumulative_pcts"] = cumulative

    return TEMPLATES.TemplateResponse(request=request, name="compliance.html", context=ctx)


@app.get("/nutrition", response_class=HTMLResponse)
async def nutrition_plan(request: Request):
    today = _today()
    days_since_start = (today - _PLAN_START).days
    cycle_week = max(0, days_since_start // 7) % 4  # 0-indexed: 0=w1, 1=w2, 2=w3, 3=w4

    recent = raw_history(3)
    today_nut = next((r for r in reversed(recent) if r.get("calories_consumed") is not None), None)

    return TEMPLATES.TemplateResponse(
        request=request,
        name="nutrition.html",
        context={
            "today": today.isoformat(),
            "cycle_week": cycle_week,
            "cal_today":     int(today_nut["calories_consumed"])    if today_nut and today_nut.get("calories_consumed")    else None,
            "tdee_today":    int(today_nut["calorie_goal_adjusted"]) if today_nut and today_nut.get("calorie_goal_adjusted") else None,
            "carbs_today":   round(today_nut["carbs_consumed"])     if today_nut and today_nut.get("carbs_consumed")       else None,
            "protein_today": round(today_nut["protein_consumed"])   if today_nut and today_nut.get("protein_consumed")     else None,
        },
    )


@app.get("/tenerife", response_class=HTMLResponse)
async def tenerife_view(request: Request):
    return TEMPLATES.TemplateResponse(request=request, name="tenerife.html", context={})


@app.get("/haute-route", response_class=HTMLResponse)
def haute_route_view(request: Request):
    today = date.today()
    history = pmc_history(days=1)
    today_pmc = history[-1] if history else {}
    ctl_now = today_pmc.get("ctl")
    atl_now = today_pmc.get("atl")

    hr_proj_data: list[dict] = []
    hr_start_ctl: Optional[float] = None
    hr_event_ctl: Optional[float] = None

    if ctl_now is not None:
        if today < _PLAN_EVENT_DATE:
            # Before charity ride: project through the remaining 12-week plan, then apply rest gap
            _, event_ctl = _ctl_projection(ctl_now, atl_now or ctl_now)
            gap_days = (HR_PLAN_START - _PLAN_EVENT_DATE).days  # 22
            hr_start_ctl = max(0.0, event_ctl + _CTL_REST_DECLINE * gap_days)
        elif today < HR_PLAN_START:
            # Between charity ride and HR plan start: apply remaining rest days
            gap_days = (HR_PLAN_START - today).days
            hr_start_ctl = max(0.0, ctl_now + _CTL_REST_DECLINE * gap_days)
        else:
            hr_start_ctl = ctl_now

        hr_proj_data = _hr_ctl_projection(hr_start_ctl)
        hr_event_ctl = hr_proj_data[-1]["ctl"] if hr_proj_data else None

    stage_plans: dict = {}
    try:
        from .analysis import generate_hr_stage_plans
        stage_plans = generate_hr_stage_plans()
    except Exception:
        pass

    ctx = {
        "active_tab":    "haute_route",
        "phases":        HR_PHASES,
        "weeks":         build_hr_calendar_weeks(),
        "event_weeks":   build_hr_event_weeks(),
        "event_start":   HR_EVENT_START,
        "event_end":     HR_EVENT_END,
        "hr_proj_data":  hr_proj_data,
        "hr_start_ctl":  round(hr_start_ctl, 1) if hr_start_ctl is not None else None,
        "hr_event_ctl":  round(hr_event_ctl, 1) if hr_event_ctl is not None else None,
        "heat_protocol": HR_HEAT_PROTOCOL,
        "stage_plans":   stage_plans,
    }
    return TEMPLATES.TemplateResponse(request=request, name="hr_calendar.html", context=ctx)



def _week_summary() -> Optional[dict]:
    """Per-day training breakdown for the current Mon–Sun week."""
    today = date.today()
    mon = today - timedelta(days=today.weekday())
    sun = mon + timedelta(days=6)

    acts_by_date = load_activities_by_date(mon, min(sun, today))

    plan_min_total = 0
    done_min_total = 0
    day_rows = []

    for i in range(7):
        d = mon + timedelta(days=i)
        is_today = d == today
        is_future = d > today

        session = session_for_date(d)
        stype = session[0] if session else "rest"
        slabel = session[1] if session else "Rest"
        sdur = session[2] if session else 0

        if stype != "rest" and sdur:
            plan_min_total += sdur

        actual_min = None
        completed = None
        if not is_future and stype != "rest":
            day_acts = acts_by_date.get(d.isoformat(), [])
            compound = COMPOUND_SESSIONS.get(slabel)
            if compound:
                matched = [a for a in day_acts
                           if any(a["type_key"] == s["garmin_key"] for s in compound)]
            else:
                valid_keys = ACTIVITY_MATCH.get(stype, set())
                matched = [a for a in day_acts if a["type_key"] in valid_keys]
            completed = bool(matched)
            if matched:
                actual_min = int(sum(a.get("duration_seconds", 0) or 0 for a in matched) / 60)
                done_min_total += actual_min

        readiness = None
        if not is_future and not is_today:
            m = load(d)
            if m:
                stats = baseline_stats(d)
                readiness = composite_score(m, stats)

        day_rows.append({
            "date": d,
            "day_name": d.strftime("%a"),
            "type": stype,
            "label": slabel,
            "dur_min": sdur,
            "actual_min": actual_min,
            "completed": completed,
            "is_today": is_today,
            "is_future": is_future,
            "readiness": readiness,
        })

    readiness_vals = [r["readiness"] for r in day_rows if r["readiness"] is not None]
    avg_readiness = sum(readiness_vals) / len(readiness_vals) if readiness_vals else None

    # Last week total training minutes for comparison
    last_mon = mon - timedelta(weeks=1)
    last_acts = load_activities_by_date(last_mon, mon - timedelta(days=1))
    last_done_min = sum(
        int((a.get("duration_seconds", 0) or 0) / 60)
        for d_acts in last_acts.values()
        for a in d_acts
        if any(a["type_key"] in keys for keys in ACTIVITY_MATCH.values())
    )

    days_into = (today - _PLAN_START).days
    week_num = max(1, days_into // 7 + 1) if today >= _PLAN_START else None
    pct = int(done_min_total / plan_min_total * 100) if plan_min_total else 0

    return {
        "days": day_rows,
        "plan_min_fmt": _fmt_min(plan_min_total),
        "done_min_fmt": _fmt_min(done_min_total) if done_min_total else "0m",
        "pct": pct,
        "bar_filled": min(pct, 100),
        "avg_readiness": avg_readiness,
        "last_done_fmt": _fmt_min(last_done_min) if last_done_min else "0m",
        "week_num": week_num,
        "week_start": mon,
    }


def _body_context() -> dict[str, Any]:
    body_rows = load_body_metrics(days=180)
    bp_rows = load_blood_pressure(days=90)

    # Latest body metrics
    latest_body = body_rows[-1] if body_rows else None
    # Latest BP (most recent timestamp)
    latest_bp = bp_rows[-1] if bp_rows else None

    bp_class_label, bp_class_colour = None, None
    if latest_bp and latest_bp.get("systolic") and latest_bp.get("diastolic"):
        bp_class_label, bp_class_colour = bp_classification(
            latest_bp["systolic"], latest_bp["diastolic"]
        )

    # Chart series: one point per day (latest reading wins)
    weight_by_date: dict[str, Optional[float]] = {}
    fat_by_date: dict[str, Optional[float]] = {}
    muscle_by_date: dict[str, Optional[float]] = {}
    hydration_by_date: dict[str, Optional[float]] = {}
    for r in body_rows:
        d = r["date"]
        if r.get("weight_kg") is not None:
            weight_by_date[d] = r["weight_kg"]
        if r.get("fat_pct") is not None:
            fat_by_date[d] = r["fat_pct"]
        if r.get("muscle_mass_kg") is not None:
            muscle_by_date[d] = r["muscle_mass_kg"]
        if r.get("hydration_pct") is not None:
            hydration_by_date[d] = r["hydration_pct"]

    weight_dates = sorted(weight_by_date)
    weight_values = [weight_by_date[d] for d in weight_dates]
    fat_dates = sorted(fat_by_date)
    fat_values = [fat_by_date[d] for d in fat_dates]
    muscle_dates = sorted(muscle_by_date)
    muscle_values = [muscle_by_date[d] for d in muscle_dates]
    hydration_dates = sorted(hydration_by_date)
    hydration_values = [hydration_by_date[d] for d in hydration_dates]

    # BP chart: one point per reading
    bp_dates = [r["date"] for r in bp_rows]
    bp_sys = [r.get("systolic") for r in bp_rows]
    bp_dia = [r.get("diastolic") for r in bp_rows]

    # Tick labels — abbreviated dates
    def _short(ds: list[str]) -> list[str]:
        from datetime import date as _date
        out = []
        for s in ds:
            try:
                d = _date.fromisoformat(s)
                out.append(d.strftime("%-d %b"))
            except Exception:
                out.append(s)
        return out

    pmc_today = pmc_history(days=1)[-1] if pmc_history(days=1) else {}
    recent_metrics = raw_history(14)
    body_analysis = generate_body_analysis(body_rows, latest_body or {}, pmc_today, recent_metrics)

    # Calorie intake from food log (last 14 days of data)
    con_vals = [r.get("calories_consumed")     for r in recent_metrics if r.get("calories_consumed")     is not None]
    adj_vals = [r.get("calorie_goal_adjusted") for r in recent_metrics if r.get("calorie_goal_adjusted") is not None]
    cal_ctx: dict = {}
    if con_vals:
        cal_ctx["avg_consumed"]   = round(sum(con_vals) / len(con_vals))
        cal_ctx["avg_tdee"]       = round(sum(adj_vals) / len(adj_vals)) if adj_vals else None
        cal_ctx["days_logged"]    = len(con_vals)
        if cal_ctx["avg_tdee"]:
            cal_ctx["avg_deficit"] = cal_ctx["avg_tdee"] - cal_ctx["avg_consumed"]
    # Today's specific values (most recent row with data)
    today_nut = next((r for r in reversed(recent_metrics) if r.get("calories_consumed") is not None), None)
    if today_nut:
        cal_ctx["today_consumed"] = today_nut.get("calories_consumed")
        cal_ctx["today_tdee"]     = today_nut.get("calorie_goal_adjusted")
        cal_ctx["today_goal"]     = today_nut.get("calorie_goal")
        if today_nut.get("carbs_consumed") is not None:
            cal_ctx["today_carbs"] = round(today_nut["carbs_consumed"])
        if today_nut.get("protein_consumed") is not None:
            cal_ctx["today_protein"] = round(today_nut["protein_consumed"])
    # 14-day macro averages
    carbs_vals   = [r["carbs_consumed"]   for r in recent_metrics if r.get("carbs_consumed")   is not None]
    protein_vals = [r["protein_consumed"] for r in recent_metrics if r.get("protein_consumed") is not None]
    if carbs_vals:
        cal_ctx["avg_carbs"]   = round(sum(carbs_vals) / len(carbs_vals))
    if protein_vals:
        cal_ctx["avg_protein"] = round(sum(protein_vals) / len(protein_vals))

    # Calorie chart (last 14 days) — convert date objects to ISO strings for _short()
    _cal_rows   = [r for r in recent_metrics if r.get("calories_consumed") is not None]
    _cal_isos   = [r["date"].isoformat() if hasattr(r["date"], "isoformat") else str(r["date"]) for r in _cal_rows]
    cal_values  = [r["calories_consumed"] for r in _cal_rows]
    tdee_values = [r.get("calorie_goal_adjusted") for r in _cal_rows]
    cal_labels  = _short(_cal_isos)

    est_wkg = None
    try:
        est_wkg = latest_estimated_wkg()
    except Exception:
        pass

    return {
        "latest_body": latest_body,
        "latest_bp": latest_bp,
        "est_wkg": est_wkg,
        "bp_class_label": bp_class_label,
        "bp_class_colour": bp_class_colour,
        "weight_dates": _short(weight_dates),
        "weight_values": weight_values,
        "fat_dates": _short(fat_dates),
        "fat_values": fat_values,
        "muscle_dates": _short(muscle_dates),
        "muscle_values": muscle_values,
        "hydration_dates": _short(hydration_dates),
        "hydration_values": hydration_values,
        "bp_dates": _short(bp_dates),
        "bp_sys": bp_sys,
        "bp_dia": bp_dia,
        "has_body": bool(body_rows),
        "has_bp": bool(bp_rows),
        "body_analysis": body_analysis,
        "cal_ctx": cal_ctx,
        "cal_dates": cal_labels,
        "cal_values": cal_values,
        "tdee_values": tdee_values,
    }


@app.get("/body", response_class=HTMLResponse)
async def body_view(request: Request, msg: Optional[str] = None):
    ctx = _body_context()
    ctx["flash_msg"] = msg
    return TEMPLATES.TemplateResponse(request=request, name="body.html", context=ctx)


@app.get("/body-refresh", response_class=RedirectResponse)
def body_refresh():
    email_addr = os.getenv("GARMIN_EMAIL", "")
    password = os.getenv("GARMIN_PASSWORD", "")
    if email_addr and password:
        try:
            api = get_api(email_addr, password)
            body_readings = fetch_body_composition(api, days=90)
            if body_readings:
                save_body_metrics(body_readings)
            bp_readings = fetch_blood_pressure(api, days=90)
            if bp_readings:
                save_blood_pressure(bp_readings)
        except Exception:
            pass
    return RedirectResponse(url="/body", status_code=303)


@app.get("/withings-sync", response_class=RedirectResponse)
def withings_sync():
    """Push Withings measurements to Garmin Connect, then refresh body data from Garmin."""
    email_addr = os.getenv("GARMIN_EMAIL", "")
    password = os.getenv("GARMIN_PASSWORD", "")
    msg = "error"
    if email_addr and password:
        try:
            api = get_api(email_addr, password)
            from .withings import sync_withings_to_garmin
            synced = sync_withings_to_garmin(api, days=30)
            body_readings = fetch_body_composition(api, days=90)
            if body_readings:
                save_body_metrics(body_readings)
            bp_readings = fetch_blood_pressure(api, days=90)
            if bp_readings:
                save_blood_pressure(bp_readings)
            msg = "synced" if synced else "no_data"
        except Exception:
            logger.exception("Withings sync failed")
    return RedirectResponse(url=f"/body?msg={msg}", status_code=303)


@app.get("/sleep", response_class=HTMLResponse)
async def sleep_view(request: Request):
    data = sleep_history(30)

    # Last night (most recent non-None sleep_score row)
    last = next((d for d in reversed(data) if d["sleep_score"] is not None), None)

    # 7-day and 30-day averages for summary cards
    def _avg(key, rows):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    recent = [d for d in data if d["sleep_score"] is not None][-7:]
    avgs_7 = {k: _avg(k, recent) for k in ("sleep_score", "sleep_hours", "deep_pct", "rem_pct", "spo2", "hrv", "respiration")}
    avgs_30 = {k: _avg(k, data) for k in ("sleep_score", "sleep_hours", "deep_pct", "rem_pct", "spo2", "hrv", "respiration")}

    analysis = generate_sleep_analysis(data, avgs_7, avgs_30)

    return TEMPLATES.TemplateResponse(request=request, name="sleep.html", context={
        "request":    request,
        "data":       data,
        "last":       last,
        "avgs_7":     avgs_7,
        "avgs_30":    avgs_30,
        "analysis":   analysis,
        "has_stages": any(d["deep_hours"] is not None for d in data),
        "has_spo2":   any(d["spo2"] is not None for d in data),
        "has_resp":   any(d["respiration"] is not None for d in data),
        "has_hrv":    any(d["hrv"] is not None for d in data),
    })


@app.get("/nutrition-test")
def nutrition_test():
    """Debug endpoint: return raw Garmin nutrition API responses for today."""
    import json as _json
    email_addr = os.getenv("GARMIN_EMAIL", "")
    password   = os.getenv("GARMIN_PASSWORD", "")
    today_str  = date.today().isoformat()
    out: dict = {"date": today_str}
    if not (email_addr and password):
        out["error"] = "GARMIN_EMAIL / GARMIN_PASSWORD not set"
        return JSONResponse(out)
    try:
        api = get_api(email_addr, password)
        for method in ("get_nutrition_daily_food_log",
                        "get_nutrition_daily_meals",
                        "get_nutrition_daily_settings"):
            try:
                out[method] = getattr(api, method)(today_str)
            except Exception as exc:
                out[method] = {"error": str(exc)}
    except Exception as exc:
        out["error"] = f"API init failed: {exc}"
    return JSONResponse(out)


@app.get("/refresh", response_class=RedirectResponse)
def refresh(date: Optional[str] = None):
    target = date_fromisoformat_safe(date) if date else _today()
    _build_context(target, force_fetch=True)
    redirect_url = f"/?date={target.isoformat()}"
    return RedirectResponse(url=redirect_url, status_code=303)


@app.get("/recovery-suggestion")
def recovery_suggestion_view(date: Optional[str] = None):
    if not date:
        raise HTTPException(status_code=400, detail="date parameter required")
    target = date_fromisoformat_safe(date)
    session = session_for_date(target)
    if not session:
        raise HTTPException(status_code=404, detail="no plan session for this date")

    # Remaining non-rest sessions this week (after the missed day, up to Sunday)
    upcoming: list[tuple] = []
    for i in range(1, 7 - target.weekday()):
        d = target + timedelta(days=i)
        s = session_for_date(d)
        if s and s[0] != "rest":
            upcoming.append((d, s))

    recent = raw_history(3)
    text = generate_recovery_suggestion(target, session, upcoming, recent)
    return JSONResponse({"suggestion": text})


def _today() -> date:
    from datetime import date as _date
    return _date.today()


def date_fromisoformat_safe(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return _today()


# ── AI Coach chat ─────────────────────────────────────────────────────────────

_COACH_SYSTEM = (
    "You are an experienced endurance coach working with an amateur athlete preparing for "
    "a 2-day charity cycling event (Ghent to Amsterdam, ~310 km total, 13–14 Sep 2026). "
    "The athlete is 50+, training 6+ hours/week mixing cycling, kettlebells, rucking, and MaxiClimber. "
    "They also have a longer-term goal: Haute Route Alpes 2027 (7 stages, ~900 km, ~25,000 m elevation).\n\n"
    "You have access to their live Garmin data in the context block below. "
    "Use it to give specific, evidence-based advice referencing actual numbers.\n\n"
    "Response style: direct and concise (2–4 short paragraphs). Use **bold** for key numbers/points.\n\n"
    "When you recommend modifying a planned session's duration, call the propose_plan_change tool — "
    "a confirmation card will appear for the athlete to review. After the tool call, briefly explain "
    "the proposed change in your text (do not say 'above' or 'below' — just refer to 'the proposal card').\n\n"
    "Training plan context: 12-week plan runs 18 May – 9 Aug 2026, followed by Tenerife cycling camp "
    "(13–27 Aug) and event prep block (Aug 31 – Sep 12). Builds from Zone 2 base to back-to-back long "
    "rides simulating the 2-day event. Key sessions: Zone 2 rides, FTP tests (wks 3/7/12), hill repeats "
    "and tempo from wk 5, progressive rucking (Mersea Coastal Spur build in wks 9–10), KB + MaxiClimber strength.\n\n"
    "PMC note: Garmin TSB units differ from Coggan TSS. Rough bands: "
    "fresh > −50, moderate load −50 to −150, heavy load −150 to −250, very high fatigue < −250."
)

_COACH_TOOL = {
    "name": "propose_plan_change",
    "description": (
        "Propose changing a planned session — duration, type, or both. The athlete must confirm "
        "before the change is applied. Use session_type and new_label when swapping to a different "
        "activity type (e.g. ruck → bike ride). Omit them when only the duration is changing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "date":         {"type": "string",  "description": "Session date (YYYY-MM-DD)"},
            "duration_min": {"type": "integer", "description": "New duration in minutes"},
            "reason":       {"type": "string",  "description": "Why this change is recommended (1–2 sentences)"},
            "session_type": {"type": "string",  "description": "New session type only if swapping activity type. One of: bike, long, tempo, ftp, strength, ruck, rest"},
            "new_label":    {"type": "string",  "description": "New session label only if swapping activity type, e.g. 'Z2 Ride', 'Easy Ride'"},
        },
        "required": ["date", "duration_min", "reason"],
    },
}


def _build_coach_context() -> str:
    today = date.today()
    history = pmc_history(days=7)
    today_pmc = history[-1] if history else {}
    m = load(today) or DailyMetrics(date=today)
    stats = baseline_stats(today)
    comp_z = composite_score(m, stats)
    from .modulation import hrv_traffic_light, session_modulation
    traffic_light = hrv_traffic_light(m, comp_z)
    modulation = session_modulation(today, m, comp_z, light=traffic_light)

    # Show all remaining sessions across the full plan + Tenerife camp + event prep.
    upcoming_lines = []
    next_session_type: Optional[str] = None  # first upcoming non-rest session — drives RAG

    # 12-week training plan sessions
    for i in range(90):
        d = today + timedelta(days=i)
        sess = session_for_date(d)
        if sess is None:
            break
        stype, label, dur = sess
        if stype == "rest":
            continue
        if next_session_type is None:
            next_session_type = stype
        ov = get_plan_override(d.isoformat())
        if ov:
            dur = ov["duration_min"]
            label = f"{label} [MODIFIED]"
        upcoming_lines.append(f"  {d.strftime('%a %d %b')} ({d.isoformat()}): {label} ({dur}min) [{stype}]")

    # Camp grid workouts (Aug 10–11, Aug 28–30 — pre/post Tenerife buffer days)
    for camp_date, s in sorted(CAMP_GRID_WORKOUTS.items()):
        if camp_date >= today:
            upcoming_lines.append(
                f"  {camp_date.strftime('%a %d %b')} ({camp_date.isoformat()}): "
                f"{s['label']} ({s['dur_min']}min) [{s['type']}]"
            )

    # Tenerife cycling camp (Aug 13–27)
    if today <= CAMP_END:
        upcoming_lines.append("  --- Tenerife Cycling Camp (13–27 Aug) ---")
        for day in TENERIFE_DAYS:
            d = day["date"]
            if d >= today:
                km = day.get("km", 0)
                elev = day.get("elev_m", 0)
                detail = f"{km}km, {elev}m elev" if km else "travel/rest"
                upcoming_lines.append(
                    f"  {d.strftime('%a %d %b')} ({d.isoformat()}): "
                    f"{day['label']} — {detail} [{day['intensity']}]"
                )

    # Event prep days (Aug 31 – Sep 6)
    event_prep_future = [ep for ep in EVENT_PREP_DAYS if ep["date"] >= today]
    if event_prep_future:
        upcoming_lines.append("  --- Event Prep (Ghent to Amsterdam charity ride, 13–14 Sep 2026) ---")
        for ep in event_prep_future:
            upcoming_lines.append(
                f"  {ep['date'].strftime('%a %d %b')} ({ep['date'].isoformat()}): "
                f"{ep['label']} ({ep['dur_min']}min) [{ep['type']}]"
            )

    # Haute Route 46-week plan (Oct 2026 – Aug 2027): show next 8 weeks of sessions
    from .hr_plan import HR_PLAN_START as _HR_START, hr_session_for_date as _hr_sess
    if today >= _HR_START or (_HR_START - today).days <= 56:
        hr_upcoming: list[str] = []
        for i in range(56):
            d = today + timedelta(days=i)
            sess = _hr_sess(d)
            if sess is None:
                continue
            stype, label, dur = sess
            if stype == "rest":
                continue
            hr_upcoming.append(
                f"  {d.strftime('%a %d %b')} ({d.isoformat()}): {label} ({dur}min) [{stype}]"
            )
        if hr_upcoming:
            upcoming_lines.append("  --- Haute Route 2027 Plan (next 8 weeks) ---")
            upcoming_lines.extend(hr_upcoming)

    recent_acts = load_recent_activities(days=14)
    act_lines = []
    for a in recent_acts[:12]:
        dur_min = int((a.get("duration_seconds") or 0) / 60)
        parts = [f"{a['date']}: {a.get('name') or a.get('type_key')} — {dur_min}min"]
        if a.get("avg_hr"):
            parts.append(f"avg HR {int(a['avg_hr'])}bpm")
        if a.get("aerobic_te") is not None:
            te_label = (a.get("training_effect_label") or "").replace("_", " ").title()
            parts.append(f"TE {a['aerobic_te']:.1f} {te_label}".strip())
        if a.get("training_load") is not None:
            parts.append(f"load {int(a['training_load'])}")
        z45 = int(((a.get("hr_zone_4_sec") or 0) + (a.get("hr_zone_5_sec") or 0)) / 60)
        if z45 > 0:
            parts.append(f"Z4+5 {z45}min")
        act_lines.append("  " + ", ".join(parts))

    overrides = list_plan_overrides()
    ov_lines = [f"  {o['date']}: {o['label']} → {o['duration_min']}min ({o['note']})" for o in overrides]

    # Body composition context
    body_rows = load_body_metrics(days=180)
    body_parts: list[str] = []
    if body_rows:
        latest_b = body_rows[-1]
        def _bf(v, dp=1): return f"{v:.{dp}f}" if v is not None else "—"
        body_parts += [
            "## Body Composition (latest reading)",
            f"Weight: {_bf(latest_b.get('weight_kg'))} kg  |  "
            f"Body fat: {_bf(latest_b.get('fat_pct'))}%  |  "
            f"Muscle mass: {_bf(latest_b.get('muscle_mass_kg'))} kg",
            f"Visceral fat: {_bf(latest_b.get('visceral_fat'), 0)}  |  "
            f"Hydration: {_bf(latest_b.get('hydration_pct'))}%  |  "
            f"BMI: {_bf(latest_b.get('bmi'))}  |  "
            f"Metabolic age: {_bf(latest_b.get('metabolic_age'), 0)}",
        ]
        # Weight trend: first vs last reading
        weight_rows = [r for r in body_rows if r.get("weight_kg") is not None]
        if len(weight_rows) >= 2:
            first_w, last_w = weight_rows[0]["weight_kg"], weight_rows[-1]["weight_kg"]
            n_weeks = max(1, (len(weight_rows)) / 7)
            rate = (last_w - first_w) / n_weeks
            from datetime import date as _date
            weeks_to_tenerife = max(0, (_date(2026, 8, 13) - today).days // 7)
            projected = last_w + rate * weeks_to_tenerife
            body_parts += [
                f"Trend: {first_w:.1f} kg ({weight_rows[0]['date']}) → {last_w:.1f} kg ({weight_rows[-1]['date']}) "
                f"= {rate:+.2f} kg/week",
                f"Projected weight at Tenerife (13 Aug, {weeks_to_tenerife} weeks): {projected:.1f} kg",
            ]
        # Calorie intake from Garmin food log
        history_14 = raw_history(14)
        con_vals = [r.get("calories_consumed")     for r in history_14 if r.get("calories_consumed")     is not None]
        adj_vals = [r.get("calorie_goal_adjusted") for r in history_14 if r.get("calorie_goal_adjusted") is not None]
        if con_vals:
            avg_consumed = round(sum(con_vals) / len(con_vals))
            avg_tdee     = round(sum(adj_vals) / len(adj_vals)) if adj_vals else None
            body_parts += ["## Calorie & Macro Intake (Garmin food log)"]
            body_parts.append(f"Avg consumed (last {len(con_vals)} days): {avg_consumed:,} kcal/day")
            if avg_tdee:
                deficit = avg_tdee - avg_consumed
                body_parts.append(f"Avg TDEE: {avg_tdee:,} kcal  |  Avg deficit: {deficit:+,} kcal/day")
            carbs_vals   = [r["carbs_consumed"]   for r in history_14 if r.get("carbs_consumed")   is not None]
            protein_vals = [r["protein_consumed"] for r in history_14 if r.get("protein_consumed") is not None]
            if carbs_vals:
                avg_c = round(sum(carbs_vals) / len(carbs_vals))
                avg_p = round(sum(protein_vals) / len(protein_vals)) if protein_vals else None
                macro_line = f"Avg carbs: {avg_c}g/day"
                if avg_p:
                    macro_line += f"  |  Avg protein: {avg_p}g/day"
                body_parts.append(macro_line)
            # Today's macros
            today_nut_c = next((r for r in reversed(history_14) if r.get("calories_consumed") is not None), None)
            if today_nut_c:
                today_c = int(today_nut_c["calories_consumed"])
                today_carbs_c = round(today_nut_c["carbs_consumed"]) if today_nut_c.get("carbs_consumed") is not None else None
                today_prot_c  = round(today_nut_c["protein_consumed"]) if today_nut_c.get("protein_consumed") is not None else None
                today_tdee_c  = int(today_nut_c["calorie_goal_adjusted"]) if today_nut_c.get("calorie_goal_adjusted") else None
                parts = [f"Today logged: {today_c:,} kcal"]
                if today_tdee_c:
                    parts.append(f"TDEE {today_tdee_c:,} ({today_tdee_c - today_c:+,})")
                if today_carbs_c is not None:
                    parts.append(f"carbs {today_carbs_c}g")
                if today_prot_c is not None:
                    parts.append(f"protein {today_prot_c}g")
                body_parts.append("  |  ".join(parts))

        # Inject cached AI advisor text if available
        cached_body = get_cached_text(f"body_analysis_v1_{today.isoformat()}")
        if cached_body:
            body_parts += ["", "Coach's body composition analysis (from Body tab):", cached_body]
        body_parts.append("")

    # Recent RPE logs
    rpe_rows = load_session_rpe(7)
    rpe_parts: list[str] = []
    if rpe_rows:
        rpe_parts = ["## Recent RPE Logs (last 7 days)"]
        for r in rpe_rows:
            rpe_str = f"RPE {r['rpe']}/5"
            note_str = f" — {r['note']}" if r.get("note") else ""
            rpe_parts.append(f"  {r['date']}: {rpe_str}{note_str}")

    # Fuelling compliance logs
    fuel_parts: list[str] = []
    try:
        fuel_rows = load_fuelling_logs(90)
        if fuel_rows:
            fuel_parts = ["## Fuelling Compliance (recent logged rides)"]
            for r in fuel_rows[:5]:
                planned = f"planned {r['planned_carbs_g_per_hr']:.0f}g/h" if r.get("planned_carbs_g_per_hr") else "no plan"
                actual = f"actual {r['actual_carbs_g_per_hr']:.0f}g/h" if r.get("actual_carbs_g_per_hr") is not None else "actual not given"
                fluid = "fluid ok" if r.get("fluid_ok") else "fluid short"
                note_str = f" — {r['note']}" if r.get("note") else ""
                fuel_parts.append(f"  {r['date']}: {planned} → {actual}, {fluid}{note_str}")
    except Exception:
        pass

    # Back-to-back training history
    btb_rows = load_btb_summary()
    btb_parts: list[str] = []
    if btb_rows:
        btb_parts = ["## Back-to-Back Training History (most recent pairs)"]
        for pair in btb_rows[:5]:
            hr1 = f"avg HR {pair['avg_hr_1']}bpm" if pair.get("avg_hr_1") else ""
            hr2 = f"avg HR {pair['avg_hr_2']}bpm" if pair.get("avg_hr_2") else ""
            fat1 = f"fatigue {pair['fatigue_rating_1']}/5" if pair.get("fatigue_rating_1") else ""
            fat2 = f"fatigue {pair['fatigue_rating_2']}/5" if pair.get("fatigue_rating_2") else ""
            d1_parts = ", ".join(filter(None, [hr1, fat1]))
            d2_parts = ", ".join(filter(None, [hr2, fat2]))
            btb_parts.append(
                f"  Day 1: {pair['date1']} ({d1_parts or 'no data'})  →  "
                f"Day 2: {pair['date2']} ({d2_parts or 'no data'})"
            )

    # Sleep history (7-day pattern with stage breakdown)
    sleep_history_rows = raw_history(8)
    sleep_parts: list[str] = []
    sleep_hist_lines: list[str] = []
    for r in sleep_history_rows:
        if r.get("sleep_score") is None:
            continue
        score = int(r["sleep_score"])
        total_h = round((r.get("sleep_seconds") or 0) / 3600, 1)
        deep_m  = int((r.get("deep_sleep_seconds")  or 0) / 60)
        rem_m   = int((r.get("rem_sleep_seconds")   or 0) / 60)
        light_m = int((r.get("light_sleep_seconds") or 0) / 60)
        stage_str = f"deep {deep_m}m / REM {rem_m}m / light {light_m}m" if deep_m or rem_m else ""
        line = f"  {r['date']}: score {score}  {total_h}h total"
        if stage_str:
            line += f"  ({stage_str})"
        sleep_hist_lines.append(line)
    if sleep_hist_lines:
        sleep_parts = ["## Sleep History (last 7 days)", *sleep_hist_lines]

    # Fatigue alerts
    alert_parts: list[str] = []
    try:
        alerts = check_fatigue_alerts(today)
        if alerts:
            alert_parts = ["## Active Fatigue Alerts"]
            for a in alerts:
                alert_parts.append(f"  [{a['severity']}] {a['type']}: {a['message']}")
    except Exception:
        pass

    # HRV traffic light + modulation
    tl_parts: list[str] = []
    tl_status = traffic_light.get("status", "unknown")
    tl_reason = traffic_light.get("reason", "")
    hrv_z_str = f"z={traffic_light['hrv_z']:+.2f}" if traffic_light.get("hrv_z") is not None else ""
    tl_parts = [
        "## HRV Traffic Light",
        f"  Status: {tl_status.upper()}  {hrv_z_str}  — {tl_reason}",
    ]
    if modulation and modulation.get("label"):
        tl_parts.append(
            f"  Suggested swap: {modulation['planned_label']} → {modulation['label']} "
            f"({modulation['duration_min']}min) — {modulation.get('headline', '')}"
        )

    # FTP test history
    ftp_parts: list[str] = []
    ftp_rows = load_ftp_tests()
    if ftp_rows:
        ftp_parts = ["## FTP Test History (LTHR)"]
        for r in ftp_rows[-4:]:
            ftp_parts.append(
                f"  {r['date']}: LTHR {r['ftp_hr']}bpm"
                + (f" (max {r['ftp_hr_max']}bpm)" if r.get("ftp_hr_max") else "")
            )

    # Durability drift (late-ride HR drift, last 5 rides ≥ 90 min)
    dur_parts: list[str] = []
    dur_rows = load_durability(90)
    if dur_rows:
        dur_parts = ["## Durability (late-ride HR drift, rides ≥ 90 min)"]
        for r in dur_rows[-5:]:
            dur_parts.append(
                f"  {r['date']}: first-third HR {r['first_third_hr']:.0f}bpm "
                f"→ final-third {r['final_third_hr']:.0f}bpm  drift {r['drift_pct']:+.1f}%"
            )

    # Foster monotony / strain (last 6 weeks)
    monotony_parts: list[str] = []
    try:
        mono_rows = weekly_monotony_strain(6)
        if mono_rows:
            monotony_parts = ["## Training Monotony & Strain (Foster, last 6 weeks)"]
            for r in mono_rows:
                mono_str = f"{r['monotony']:.2f}" if r.get("monotony") is not None else "—"
                strain_str = f"{r['strain']:.0f}" if r.get("strain") is not None else "—"
                monotony_parts.append(
                    f"  {r['week_label']}: load {r['weekly_load']:.0f}  monotony {mono_str}  strain {strain_str}"
                )
    except Exception:
        pass

    # Blood pressure (latest)
    bp_parts: list[str] = []
    bp_rows = load_blood_pressure(90)
    if bp_rows:
        bp = bp_rows[-1]
        bp_parts = [
            "## Blood Pressure (latest)",
            f"  {bp['date']}: {bp.get('systolic')}/{bp.get('diastolic')} mmHg  "
            f"pulse {bp.get('pulse')}bpm"
        ]

    # Estimated W/kg (no power meter)
    wkg_parts: list[str] = []
    wkg = latest_estimated_wkg()
    if wkg:
        wkg_parts = [
            "## Estimated FTP / W/kg (ACSM formula, no power meter)",
            f"  {wkg['date']}: VO2max {wkg['vo2_max']} ml/kg/min  "
            f"est. FTP {wkg['est_ftp_w']:.0f}W  {wkg['wkg']:.2f} W/kg  "
            f"(weight {wkg['weight_kg']:.1f} kg)"
        ]

    # Plan compliance summary (12-week plan)
    compliance_parts: list[str] = []
    try:
        comp_stats = _plan_completion_stats()
        total_p = comp_stats.get("total_plan_sessions", 0)
        total_d = comp_stats.get("total_done_sessions", 0)
        total_pm = comp_stats.get("total_plan_min", 0)
        total_dm = comp_stats.get("total_done_min", 0)
        if total_p:
            pct = int(total_d / total_p * 100)
            compliance_parts = [
                "## Plan Compliance (12-week plan, elapsed weeks)",
                f"  Sessions: {total_d}/{total_p} ({pct}%)  |  "
                f"Volume: {total_dm//60}h{total_dm%60:02d}m of {total_pm//60}h{total_pm%60:02d}m planned",
            ]
    except Exception:
        pass

    # Nutrition plan — today's prescribed meals
    from .nutrition_plan import nutrition_coach_context
    nutrition_ctx = nutrition_coach_context(_PLAN_START, today)

    parts = [
        f"Today: {today.strftime('%A %d %B %Y')}",
        "",
        "## Training Load (PMC)",
        f"CTL (fitness): {today_pmc.get('ctl')}  |  ATL (fatigue): {today_pmc.get('atl')}  |  TSB (form): {today_pmc.get('tsb')}",
        "",
        "## Today's Readiness",
        f"Composite z-score: {f'{comp_z:+.2f}σ' if comp_z is not None else 'n/a'}",
        f"HRV: {m.hrv_last_night}  |  Sleep score: {m.sleep_score}  |  Body battery (AM): {m.body_battery_morning}  "
        f"|  Avg stress: {m.avg_stress}  |  Resting HR: {m.resting_hr}  |  VO2max: {m.vo2_max}",
        *tl_parts,
        "",
        *([*alert_parts, ""] if alert_parts else []),
        *([*sleep_parts, ""] if sleep_parts else []),
        *body_parts,
        *([*bp_parts, ""] if bp_parts else []),
        *([*wkg_parts, ""] if wkg_parts else []),
        *([*ftp_parts, ""] if ftp_parts else []),
        *([*dur_parts, ""] if dur_parts else []),
        *([*monotony_parts, ""] if monotony_parts else []),
        *([*compliance_parts, ""] if compliance_parts else []),
        nutrition_ctx,
        "",
        "## Upcoming Plan Sessions (full remaining plan)",
        *upcoming_lines,
        "",
        "## Recent Activities (last 14 days)",
        *(act_lines or ["  None recorded"]),
        *([" ", *rpe_parts] if rpe_parts else []),
        *([" ", *fuel_parts] if fuel_parts else []),
        *([" ", *btb_parts] if btb_parts else []),
    ]
    if ov_lines:
        parts += ["", "## Active Plan Overrides", *ov_lines]

    memo = get_coach_memory()
    if memo:
        parts += ["", "## Coach Memory (cross-session context)", memo["memo"]]

    # Ground the coach in the athlete's own past sessions in the same discipline as the next
    # one up. NB: retrieval is by discipline (cycling/strength/rucking), not workout sub-type —
    # bike/tempo/ftp/long all map to the same cycling activities, so don't imply sub-type match.
    if next_session_type:
        _DISCIPLINE = {
            "bike": "cycling", "tempo": "cycling", "ftp": "cycling", "long": "cycling",
            "strength": "strength", "ruck": "rucking / load-carry",
        }
        discipline = _DISCIPLINE.get(next_session_type, next_session_type)
        past = retrieve_relevant_analyses(next_session_type, limit=3)
        if past:
            rag_lines = [
                "", f"## Relevant Past Sessions (your recent {discipline} sessions)",
                f"These are your most recent {discipline} sessions — NOT necessarily the same "
                "workout type as the one coming up. Reference them for context and cite specific "
                "dates/numbers, but do not claim they were the same session type.",
            ]
            for p in past:
                hdr = f"  {p['date']} — {p.get('name') or p.get('type_key')}"
                stats = []
                if p.get("avg_hr"):
                    stats.append(f"avg HR {int(p['avg_hr'])}")
                if p.get("training_effect") is not None:
                    stats.append(f"TE {p['training_effect']:.1f} {_te_clean(p.get('training_effect_label'))}".strip())
                if p.get("training_load") is not None:
                    stats.append(f"load {int(p['training_load'])}")
                if p.get("z45_min"):
                    stats.append(f"Z4+5 {p['z45_min']}min")
                if stats:
                    hdr += " (" + ", ".join(stats) + ")"
                rag_lines.append(hdr)
                if p.get("summary"):
                    rag_lines.append(f"    {p['summary']}")
            parts += rag_lines

    return "\n".join(parts)


def _te_clean(label: Optional[str]) -> str:
    return (label or "").replace("_", " ").title()


def _call_coach(messages: list[dict], api_key: str) -> tuple[str, Optional[dict]]:
    context = _build_coach_context()
    system = _COACH_SYSTEM + f"\n\n## Current Context\n{context}"

    client = _anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=MODEL_SMART,
        max_tokens=800,
        system=system,
        tools=[_COACH_TOOL],
        messages=messages,
    )

    text_parts: list[str] = []
    proposal: Optional[dict] = None
    tool_call = None

    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use" and block.name == "propose_plan_change":
            tool_call = block
            proposal = dict(block.input)

    if tool_call and response.stop_reason == "tool_use":
        followup_messages = messages + [
            {"role": "assistant", "content": response.content},
            {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "content": "Proposal ready for athlete confirmation.",
                }],
            },
        ]
        response2 = client.messages.create(
            model=MODEL_SMART,
            max_tokens=300,
            system=system,
            tools=[_COACH_TOOL],
            messages=followup_messages,
        )
        for block in response2.content:
            if block.type == "text":
                text_parts.append(block.text)

    if proposal:
        try:
            d = date.fromisoformat(proposal["date"])
            sess = session_for_date(d)
            ov = get_plan_override(proposal["date"])
            current_dur = ov["duration_min"] if ov else (sess[2] if sess else None)
            # Prefer coach-proposed type/label (swap); fall back to existing plan session
            proposal["session_type"]  = proposal.pop("session_type", None) or (sess[0] if sess else None)
            proposal["session_label"] = proposal.pop("new_label", None)    or (sess[1] if sess else None)
            proposal["current_duration_min"] = current_dur
        except Exception:
            proposal["session_label"] = None

    return "\n\n".join(filter(None, text_parts)), proposal


class _CoachChatRequest(BaseModel):
    message: str


@app.post("/coach-chat")
async def coach_chat(body: _CoachChatRequest):
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse({"reply": "No API key configured.", "proposal": None})

    user_message = body.message.strip()
    if not user_message:
        return JSONResponse({"reply": "", "proposal": None})

    history = load_coach_history(limit=20)
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": user_message})

    reply, proposal = _call_coach(messages, api_key)

    save_coach_message("user", user_message)
    save_coach_message("assistant", reply, json.dumps(proposal) if proposal else None)

    return JSONResponse({"reply": reply, "proposal": proposal})


_MEMO_MIN_MESSAGES = 3
_MEMO_STALE_HOURS = 4


def _should_update_memo() -> bool:
    from datetime import datetime as _dt
    memo = get_coach_memory()
    if memo is None:
        return len(load_coach_history(limit=_MEMO_MIN_MESSAGES)) >= _MEMO_MIN_MESSAGES
    try:
        age_h = (_dt.utcnow() - _dt.fromisoformat(memo["updated_at"])).total_seconds() / 3600
        return age_h >= _MEMO_STALE_HOURS
    except Exception:
        return False


def _regenerate_coach_memory(api_key: str) -> None:
    history = load_coach_history(limit=40)
    if len(history) < _MEMO_MIN_MESSAGES:
        return
    current = get_coach_memory()
    current_memo = current["memo"] if current else ""
    context = _build_coach_context()
    recent = history[-20:]
    conv_text = "\n\n".join(
        f"{'Coach' if m['role'] == 'assistant' else 'Athlete'}: {m['content'][:300]}"
        for m in recent
    )
    prompt = (
        "Update the coaching memo with DURABLE cross-session information — goals, tendencies, past decisions, "
        "long-term patterns. Omit anything visible in live session data (current CTL/ATL, today's readiness, "
        "upcoming sessions) since the coach already receives that every turn.\n\n"
        f"Current memo:\n{current_memo if current_memo else '(none)'}\n\n"
        f"Recent conversations:\n{conv_text}\n\n"
        f"Live context summary (for reference only — don't repeat this):\n{context[:600]}\n\n"
        "Write a replacement memo (150–250 words) covering:\n"
        "- Goals and timeline (Ghent to Amsterdam charity ride 13–14 Sep 2026, Haute Route Alpes 2027)\n"
        "- Tendencies (e.g. pushes through fatigue, HRV baseline, training response)\n"
        "- Plan decisions made via coach chat\n"
        "- Long-term patterns worth watching\n"
        "Third person (athlete/they). Specific, not generic. Replace the previous memo entirely."
    )
    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=MODEL_FAST,
            max_tokens=400,
            system="You are maintaining a compact coaching notes file. Be specific and concise.",
            messages=[{"role": "user", "content": prompt}],
        )
        set_coach_memory(resp.content[0].text)
    except Exception:
        pass


def _maybe_update_memo_bg(api_key: str) -> None:
    if _should_update_memo():
        threading.Thread(
            target=_regenerate_coach_memory,
            args=(api_key,),
            daemon=True,
        ).start()


def _stream_coach_sse(messages: list[dict], user_message: str, api_key: str):
    """Sync generator yielding SSE events for the coach chat stream."""
    # Save user message immediately so it survives connection drops or server restarts.
    save_coach_message("user", user_message)

    context = _build_coach_context()
    system = _COACH_SYSTEM + f"\n\n## Current Context\n{context}"
    client = _anthropic.Anthropic(api_key=api_key)

    full_text: list[str] = []
    proposal: Optional[dict] = None

    try:
        with client.messages.stream(
            model=MODEL_SMART,
            max_tokens=800,
            system=system,
            tools=[_COACH_TOOL],
            messages=messages,
        ) as stream:
            for chunk in stream.text_stream:
                full_text.append(chunk)
                yield f"data: {json.dumps({'type': 'text', 'chunk': chunk})}\n\n"
            final = stream.get_final_message()

        tool_call = None
        for block in final.content:
            if block.type == "tool_use" and block.name == "propose_plan_change":
                tool_call = block
                proposal = dict(block.input)

        if tool_call and final.stop_reason == "tool_use":
            followup = messages + [
                {"role": "assistant", "content": final.content},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_call.id, "content": "Proposal ready for athlete confirmation."}]},
            ]
            with client.messages.stream(
                model=MODEL_SMART,
                max_tokens=300,
                system=system,
                tools=[_COACH_TOOL],
                messages=followup,
            ) as stream2:
                for chunk in stream2.text_stream:
                    full_text.append(chunk)
                    yield f"data: {json.dumps({'type': 'text', 'chunk': chunk})}\n\n"

        if proposal:
            try:
                d = date.fromisoformat(proposal["date"])
                sess = session_for_date(d)
                ov = get_plan_override(proposal["date"])
                current_dur = ov["duration_min"] if ov else (sess[2] if sess else None)
                proposal["session_type"]  = proposal.pop("session_type", None) or (sess[0] if sess else None)
                proposal["session_label"] = proposal.pop("new_label", None)    or (sess[1] if sess else None)
                proposal["current_duration_min"] = current_dur
            except Exception:
                proposal["session_label"] = None
            yield f"data: {json.dumps({'type': 'proposal', 'data': proposal})}\n\n"

        full_reply = "".join(full_text)
        save_coach_message("assistant", full_reply, json.dumps(proposal) if proposal else None)
        _maybe_update_memo_bg(api_key)

    except Exception:
        # Don't leak raw exception text (may contain key/account details)
        logger.exception("coach-chat-stream failed")
        yield f"data: {json.dumps({'type': 'error', 'message': 'Coach is temporarily unavailable — check the server logs.'})}\n\n"

    yield f"data: {json.dumps({'type': 'done'})}\n\n"


@app.post("/coach-chat-stream")
async def coach_chat_stream(body: _CoachChatRequest):
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "No API key configured."})

    user_message = body.message.strip()
    if not user_message:
        return JSONResponse({"error": "Empty message."})

    history = load_coach_history(limit=20)
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": user_message})

    return StreamingResponse(
        _stream_coach_sse(messages, user_message, api_key),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/coach")
async def coach_tab_view(request: Request):
    return TEMPLATES.TemplateResponse(request=request, name="coach_tab.html", context={"active_tab": "coach"})


@app.get("/coach-history")
async def get_coach_history():
    return JSONResponse(load_coach_history(limit=30))


@app.delete("/coach-history")
async def delete_coach_history_endpoint():
    clear_coach_history()
    return JSONResponse({"ok": True})


_VALID_SESSION_TYPES = {"bike", "ftp", "long", "rest", "ruck", "strength", "tempo",
                        # Haute Route plan vocabulary (hr_plan.py)
                        "endurance", "recovery", "vo2", "sweetspot", "gym", "back_to_back"}


class _ApplyChangeRequest(BaseModel):
    date: str
    duration_min: int = Field(..., gt=0, le=600)
    reason: str = Field("", max_length=500)
    session_type: Optional[str] = None  # if provided, overrides the plan session type
    label: Optional[str] = Field(None, max_length=100)  # overrides the plan session label


@app.post("/apply-plan-change")
async def apply_plan_change(body: _ApplyChangeRequest):
    try:
        d = date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format")
    if body.session_type is not None and body.session_type not in _VALID_SESSION_TYPES:
        raise HTTPException(status_code=422, detail=f"Unknown session_type '{body.session_type}'")

    from .hr_plan import hr_session_for_date
    from .plan import session_for_date_extended
    sess = session_for_date_extended(d) or hr_session_for_date(d)
    if not sess:
        raise HTTPException(status_code=404, detail="No plan session on that date")

    stype = body.session_type or sess[0]
    label = body.label or sess[1]
    set_plan_override(body.date, stype, label, body.duration_min, body.reason)
    return JSONResponse({"ok": True, "date": body.date, "label": label, "duration_min": body.duration_min})


@app.post("/regenerate-advice")
def regenerate_advice_endpoint():
    today = _today()
    delete_advice(today)
    ctx = _build_context(today, force_fetch=True)
    return JSONResponse({"advice": ctx["advice"]})


@app.post("/regenerate-body-advice")
def regenerate_body_advice_endpoint():
    set_cached_text(f"body_analysis_v1_{_today().isoformat()}", "")
    ctx = _body_context()
    return JSONResponse({"analysis": ctx["body_analysis"]})


@app.get("/coach-memory")
async def coach_memory_get():
    memo = get_coach_memory()
    return JSONResponse({"memo": memo["memo"] if memo else "", "updated_at": memo.get("updated_at") if memo else None})


@app.post("/coach-memory/update")
async def coach_memory_update():
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "No API key"}, status_code=503)
    _regenerate_coach_memory(api_key)
    memo = get_coach_memory()
    return JSONResponse({"memo": memo["memo"] if memo else ""})


def run(host: str = "0.0.0.0", port: int = 8743) -> None:
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="warning")
