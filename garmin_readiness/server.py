from __future__ import annotations

import json
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
from pydantic import BaseModel

from .analysis import generate_recovery_suggestion, load_analyses_for_activities, prefetch_nutrition_targets, prefetch_workout_descriptions, refresh_analyses
from .client import get_api
from .display import FIELD_LABELS, fmt_value, readiness_label, enrich_activity
from .plan import (PLAN_START as _PLAN_START, build_calendar_weeks, build_camp_weeks,
                   build_combined_event_weeks, COMPOUND_SESSIONS,
                   CAMP_GRID_WORKOUTS, EVENT_PREP_DAYS, TENERIFE_DAYS, session_for_date)
from .hr_plan import (HR_PHASES, HR_PLAN_START, HR_TRAINING_WEEKS,
                      build_hr_calendar_weeks, build_hr_event_weeks,
                      HR_EVENT_START, HR_EVENT_END)
from .mersea_routes import MERSEA_ROUTES, MERSEA_TARGET_DATE
from .report import generate_advice, generate_dashboard_explainer, generate_pmc_analysis, generate_pmc_explainer
from .body import bp_classification, fetch_body_composition, fetch_blood_pressure
from .history import (
    ACTIVITY_MATCH,
    baseline_stats,
    clear_coach_history,
    composite_score,
    delete_advice,
    delete_plan_override,
    get_coach_memory,
    get_plan_override,
    history_for_chart,
    list_plan_overrides,
    load,
    load_activities_by_date,
    load_body_metrics,
    load_blood_pressure,
    load_coach_history,
    load_recent_activities,
    pmc_history,
    raw_history,
    save,
    save_activities,
    save_body_metrics,
    save_blood_pressure,
    save_coach_message,
    set_coach_memory,
    set_plan_override,
    seven_day_composite_trend_csv,
    z_score,
    get_cached_text,
)
from .metrics import DailyMetrics, available_count, fetch_metrics, fetch_activities, TEXT_FIELDS

load_dotenv()

_advice_cache: dict[str, str] = {}
_pmc_cache: dict[str, str] = {}

_BIKE_TYPE_KEYS = {"road_biking", "cycling", "virtual_ride", "indoor_cycling", "mountain_biking"}
_HARD_LABELS = {"Tempo Intervals", "FTP Test", "FTP Re-test"}
_HARD_SESSION_TYPES = {"tempo", "ftp", "long"}

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


def _apply_overrides(weeks: list[dict]) -> list[dict]:
    """Patch dur_min / dur_fmt for any day that has a plan override in the DB."""
    overrides = {o["date"]: o for o in list_plan_overrides()}
    if not overrides:
        return weeks
    for week in weeks:
        for day in week["days"]:
            key = day["date"].isoformat()
            if key in overrides:
                dur = overrides[key]["duration_min"]
                day["dur_min"] = dur
                day["dur_fmt"] = _fmt_min(dur)
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
    if z >= 0.5:
        return "text-emerald-400"
    if z <= -0.5:
        return "text-red-400"
    return "text-yellow-400"


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
    if force_fetch:
        # Pop in-process cache so advice is re-read from SQLite, but keep the
        # SQLite row — re-generating advice on every refresh causes inconsistency.
        _advice_cache.pop(date_key, None)
    if date_key not in _advice_cache:
        _advice_cache[date_key] = generate_advice(m, stats, comp_z)

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

    return {
        "date": date_key,
        "date_long": target.strftime("%A, %-d %B %Y"),
        "comp_z": comp_z,
        "comp_label": comp_label,
        "comp_colour": comp_colour,
        "badges": badges,
        "metrics": metric_rows,
        "chart_labels": chart_labels,
        "chart_values": chart_values,
        "baseline_count": len(stats),
        "activities": activities,
        "trend_note": seven_day_composite_trend_csv(),
        "activity_blurb": _activity_context_blurb(activities),
        "advice": _advice_cache[date_key],
        "week_summary": _ws,
        "metric_explainer": generate_dashboard_explainer(),
        "sparklines": sparklines,
        "today_plan": today_plan,
        "swap_suggestion": swap_suggestion,
        "event_tracker": event_tracker,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, date: Optional[str] = None, msg: Optional[str] = None):
    target = date_fromisoformat_safe(date) if date else _today()
    ctx = _build_context(target)
    ctx["flash_msg"] = msg
    return TEMPLATES.TemplateResponse(request=request, name="dashboard.html", context=ctx)


@app.get("/send-email", response_class=RedirectResponse)
async def send_email_now():
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

    try:
        from .report import run_report
        run_report(m, dry_run=False)
        sentinel.touch()
        return RedirectResponse(url="/?msg=sent", status_code=303)
    except Exception as e:
        logger.error("send-email failed: %s", e)
        return RedirectResponse(url="/?msg=error", status_code=303)


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
async def analysis_view(request: Request):
    activities_raw = load_recent_activities(days=14)
    activities = load_analyses_for_activities(
        [enrich_activity(a) for a in activities_raw]
    )
    activities = _merge_compound_activities(activities)
    return TEMPLATES.TemplateResponse(
        request=request,
        name="analysis.html",
        context={"activities": activities},
    )


@app.get("/analysis-refresh", response_class=RedirectResponse)
async def analysis_refresh():
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
    if date_key not in _pmc_cache:
        m_today = load(date.today()) or DailyMetrics(date=date.today())
        stats_today = baseline_stats(date.today())
        comp_z_today = composite_score(m_today, stats_today)
        _pmc_cache[date_key] = generate_pmc_analysis(history, m_today, comp_z_today)

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

    return TEMPLATES.TemplateResponse(
        request=request,
        name="performance.html",
        context={
            "history": history,
            "today": today_entry,
            "pmc_analysis": _pmc_cache[date_key],
            "pmc_explainer": generate_pmc_explainer(),
            "z2_points": z2_points,
            "proj_data": proj_data,
            "event_ctl": event_ctl,
            "load_chart_data": load_chart_data,
            "event_date_label": _PLAN_EVENT_DATE.strftime("%-d %b %Y"),
            "camp_start_label": date(2026, 8, 13).strftime("%-d %b"),
            "camp_end_label":   date(2026, 8, 27).strftime("%-d %b"),
            "event_prep_label": date(2026, 8, 31).strftime("%-d %b"),
        },
    )


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


def _ctl_projection(current_ctl: float, current_atl: float) -> tuple[list[dict], float]:
    """Project CTL from today to event day using all plan sessions including Tenerife camp.

    Uses additive deltas calibrated against observed week-1 data rather than
    the standard Coggan EMA, because Garmin's CTL units don't follow the
    standard TSS-based scale. A soft ceiling (diminishing returns above CTL 300)
    prevents runaway growth.
    """
    today = date.today()
    days_ahead = (_PLAN_EVENT_DATE - today).days
    if days_ahead <= 0:
        return [], round(current_ctl, 1)

    ctl = current_ctl
    result = []
    for i in range(1, days_ahead + 1):
        d = today + timedelta(days=i)
        sess = _session_for_projection(d)
        if sess and sess[0] != "rest":
            stype, _, dur_min = sess
            rate = _CTL_PER_MIN.get(stype, 0.35)
            ceiling = (300 / max(ctl, 300)) ** 2
            delta = rate * (dur_min or 0) * ceiling
        else:
            delta = _CTL_REST_DECLINE
        ctl = max(0.0, ctl + delta)
        result.append({
            "label": d.strftime("%-d %b"),
            "ctl":   round(ctl, 1),
        })
    return result, round(result[-1]["ctl"], 1) if result else round(current_ctl, 1)


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


@app.get("/nutrition", response_class=HTMLResponse)
async def nutrition_plan(request: Request):
    weeks = _calendar_weeks()
    unique_sessions = list({(d["type"], d["dur_min"]) for w in weeks for d in w["days"]})
    nut_targets = prefetch_nutrition_targets(unique_sessions)
    today = date.today()
    current_week = max(0, min(11, (today - _PLAN_START).days // 7))
    # Enrich each day with nutrition target data
    for week in weeks:
        for day in week["days"]:
            key = f"{day['type']}_{day['dur_min']}"
            target = nut_targets.get(key, {})
            day["kcal"] = target.get("kcal")
            day["protein_g"] = target.get("protein_g")
            day["carbs_g"] = target.get("carbs_g")
            day["fat_g"] = target.get("fat_g")
            day["nut_brief"] = target.get("brief")
    return TEMPLATES.TemplateResponse(
        request=request,
        name="nutrition.html",
        context={
            "weeks": weeks,
            "current_week": current_week,
            "today": today.isoformat(),
        },
    )


@app.get("/tenerife", response_class=HTMLResponse)
async def tenerife_view(request: Request):
    return TEMPLATES.TemplateResponse(request=request, name="tenerife.html", context={})


@app.get("/haute-route", response_class=HTMLResponse)
async def haute_route_view(request: Request):
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
    }
    return TEMPLATES.TemplateResponse(request=request, name="hr_calendar.html", context=ctx)


@app.get("/mersea", response_class=HTMLResponse)
async def mersea_view(request: Request):
    import json
    return TEMPLATES.TemplateResponse(request=request, name="mersea.html", context={
        "active_tab":  "mersea",
        "routes":      MERSEA_ROUTES,
        "routes_json": json.dumps(MERSEA_ROUTES),
        "target_date": MERSEA_TARGET_DATE.isoformat(),
    })


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
    body_rows = load_body_metrics(days=90)
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

    return {
        "latest_body": latest_body,
        "latest_bp": latest_bp,
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
    }


@app.get("/body", response_class=HTMLResponse)
async def body_view(request: Request):
    ctx = _body_context()
    return TEMPLATES.TemplateResponse(request=request, name="body.html", context=ctx)


@app.get("/body-refresh", response_class=RedirectResponse)
async def body_refresh():
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
async def withings_sync():
    """Push Withings measurements to Garmin Connect, then refresh body data from Garmin."""
    email_addr = os.getenv("GARMIN_EMAIL", "")
    password = os.getenv("GARMIN_PASSWORD", "")
    if email_addr and password:
        try:
            api = get_api(email_addr, password)
            from .withings import sync_withings_to_garmin
            sync_withings_to_garmin(api, days=30)
            body_readings = fetch_body_composition(api, days=90)
            if body_readings:
                save_body_metrics(body_readings)
            bp_readings = fetch_blood_pressure(api, days=90)
            if bp_readings:
                save_blood_pressure(bp_readings)
        except Exception:
            pass
    return RedirectResponse(url="/body", status_code=303)


@app.get("/refresh", response_class=RedirectResponse)
async def refresh(date: Optional[str] = None):
    target = date_fromisoformat_safe(date) if date else _today()
    _build_context(target, force_fetch=True)
    redirect_url = f"/?date={target.isoformat()}"
    return RedirectResponse(url=redirect_url, status_code=303)


@app.get("/recovery-suggestion")
async def recovery_suggestion_view(date: Optional[str] = None):
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
    "a 5-day charity cycling event (Lap the Map, ~170 km total, 6 Sep 2026). "
    "The athlete is 50+, training 6+ hours/week mixing cycling, kettlebells, rucking, and MaxiClimber. "
    "They also have a longer-term goal: Haute Route Alpes 2027 (7 stages, ~900 km, ~25,000 m elevation).\n\n"
    "You have access to their live Garmin data in the context block below. "
    "Use it to give specific, evidence-based advice referencing actual numbers.\n\n"
    "Response style: direct and concise (2–4 short paragraphs). Use **bold** for key numbers/points.\n\n"
    "When you recommend modifying a planned session's duration, call the propose_plan_change tool — "
    "a confirmation card will appear for the athlete to review. After the tool call, briefly explain "
    "the proposed change in your text (do not say 'above' or 'below' — just refer to 'the proposal card').\n\n"
    "Training plan context: 12-week plan runs 18 May – 9 Aug 2026. Builds from Zone 2 base to a 5-hour "
    "event simulation. Key sessions: Zone 2 rides, FTP tests (wks 3/7/12), hill repeats and tempo from "
    "wk 5, progressive rucking (Mersea Coastal Spur build in wks 9–10), KB + MaxiClimber strength.\n\n"
    "PMC note: Garmin TSB units differ from Coggan TSS. Rough bands: "
    "fresh > −50, moderate load −50 to −150, heavy load −150 to −250, very high fatigue < −250."
)

_COACH_TOOL = {
    "name": "propose_plan_change",
    "description": (
        "Propose changing a planned session's duration. The athlete must confirm before "
        "the change is applied. Only use this when recommending a specific duration change."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "Session date (YYYY-MM-DD)"},
            "duration_min": {"type": "integer", "description": "New duration in minutes"},
            "reason": {"type": "string", "description": "Why this change is recommended (1–2 sentences)"},
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

    # Show all remaining plan sessions (up to 90 days ahead) so the coach
    # can reason about the full training block, not just the current week.
    upcoming_lines = []
    for i in range(90):
        d = today + timedelta(days=i)
        sess = session_for_date(d)
        if sess is None:
            break  # past the end of the plan
        stype, label, dur = sess
        if stype == "rest":
            continue
        ov = get_plan_override(d.isoformat())
        if ov:
            dur = ov["duration_min"]
            label = f"{label} [MODIFIED]"
        upcoming_lines.append(f"  {d.strftime('%a %d %b')} ({d.isoformat()}): {label} ({dur}min) [{stype}]")

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

    parts = [
        f"Today: {today.strftime('%A %d %B %Y')}",
        "",
        "## Training Load (PMC)",
        f"CTL (fitness): {today_pmc.get('ctl')}  |  ATL (fatigue): {today_pmc.get('atl')}  |  TSB (form): {today_pmc.get('tsb')}",
        "",
        "## Today's Readiness",
        f"Composite z-score: {f'{comp_z:+.2f}σ' if comp_z is not None else 'n/a'}",
        f"HRV: {m.hrv_last_night}  |  Sleep score: {m.sleep_score}  |  Body battery (AM): {m.body_battery_morning}  |  Avg stress: {m.avg_stress}",
        "",
        "## Upcoming Plan Sessions (full remaining plan)",
        *upcoming_lines,
        "",
        "## Recent Activities (last 14 days)",
        *(act_lines or ["  None recorded"]),
    ]
    if ov_lines:
        parts += ["", "## Active Plan Overrides", *ov_lines]

    memo = get_coach_memory()
    if memo:
        parts += ["", "## Coach Memory (cross-session context)", memo["memo"]]

    return "\n".join(parts)


def _call_coach(messages: list[dict], api_key: str) -> tuple[str, Optional[dict]]:
    context = _build_coach_context()
    system = _COACH_SYSTEM + f"\n\n## Current Context\n{context}"

    client = _anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
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
            model="claude-sonnet-4-6",
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
            proposal["session_label"] = sess[1] if sess else None
            proposal["session_type"] = sess[0] if sess else None
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
        "- Goals and timeline (Lap the Map, Haute Route)\n"
        "- Tendencies (e.g. pushes through fatigue, HRV baseline, training response)\n"
        "- Plan decisions made via coach chat\n"
        "- Long-term patterns worth watching\n"
        "Third person (athlete/they). Specific, not generic. Replace the previous memo entirely."
    )
    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
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
    context = _build_coach_context()
    system = _COACH_SYSTEM + f"\n\n## Current Context\n{context}"
    client = _anthropic.Anthropic(api_key=api_key)

    full_text: list[str] = []
    proposal: Optional[dict] = None

    try:
        with client.messages.stream(
            model="claude-sonnet-4-6",
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
                model="claude-sonnet-4-6",
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
                proposal["session_label"] = sess[1] if sess else None
                proposal["session_type"] = sess[0] if sess else None
                proposal["current_duration_min"] = current_dur
            except Exception:
                proposal["session_label"] = None
            yield f"data: {json.dumps({'type': 'proposal', 'data': proposal})}\n\n"

        full_reply = "".join(full_text)
        save_coach_message("user", user_message)
        save_coach_message("assistant", full_reply, json.dumps(proposal) if proposal else None)
        _maybe_update_memo_bg(api_key)

    except Exception as exc:
        yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

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


@app.get("/coach-history")
async def get_coach_history():
    return JSONResponse(load_coach_history(limit=30))


@app.delete("/coach-history")
async def delete_coach_history_endpoint():
    clear_coach_history()
    return JSONResponse({"ok": True})


class _ApplyChangeRequest(BaseModel):
    date: str
    duration_min: int
    reason: str = ""


@app.post("/apply-plan-change")
async def apply_plan_change(body: _ApplyChangeRequest):
    try:
        d = date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format")

    sess = session_for_date(d)
    if not sess:
        raise HTTPException(status_code=404, detail="No plan session on that date")

    stype, label, _ = sess
    set_plan_override(body.date, stype, label, body.duration_min, body.reason)
    return JSONResponse({"ok": True, "date": body.date, "label": label, "duration_min": body.duration_min})


@app.post("/regenerate-advice")
async def regenerate_advice_endpoint():
    today = _today()
    delete_advice(today)
    ctx = _build_context(today, force_fetch=True)
    return JSONResponse({"advice": ctx["advice"]})


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
