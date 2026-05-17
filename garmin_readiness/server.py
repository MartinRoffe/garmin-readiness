from __future__ import annotations

import os
import secrets
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

from .analysis import load_analyses_for_activities, prefetch_nutrition_targets, prefetch_workout_descriptions, refresh_analyses
from .client import get_api
from .display import FIELD_LABELS, fmt_value, readiness_label, enrich_activity
from .plan import PLAN_START as _PLAN_START, build_calendar_weeks, session_for_date
from .report import generate_advice, generate_pmc_analysis, generate_pmc_explainer
from .history import (
    ACTIVITY_MATCH,
    baseline_stats,
    composite_score,
    history_for_chart,
    load,
    load_activities_by_date,
    load_recent_activities,
    pmc_history,
    save,
    save_activities,
    seven_day_composite_trend_csv,
    z_score,
)
from .metrics import DailyMetrics, available_count, fetch_metrics, fetch_activities, TEXT_FIELDS

load_dotenv()

_advice_cache: dict[str, str] = {}
_pmc_cache: dict[str, str] = {}

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


def _build_calendar_ctx() -> dict[str, Any]:
    return {"weeks": build_calendar_weeks(), "today": date.today(), "plan_start": _PLAN_START}

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
    if date_key not in _advice_cache:
        _advice_cache[date_key] = generate_advice(m, stats, comp_z)

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
        "week_completion": _week_completion(),
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, date: Optional[str] = None):
    target = date_fromisoformat_safe(date) if date else _today()
    ctx = _build_context(target)
    return TEMPLATES.TemplateResponse(request=request, name="dashboard.html", context=ctx)


@app.get("/analysis", response_class=HTMLResponse)
async def analysis_view(request: Request):
    activities_raw = load_recent_activities(days=14)
    activities = load_analyses_for_activities(
        [enrich_activity(a) for a in activities_raw]
    )
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
        _pmc_cache[date_key] = generate_pmc_analysis(history)
    return TEMPLATES.TemplateResponse(
        request=request,
        name="performance.html",
        context={
            "history": history,
            "today": today_entry,
            "pmc_analysis": _pmc_cache[date_key],
            "pmc_explainer": generate_pmc_explainer(),
        },
    )


_BIKE_TYPES = {"bike", "tempo", "ftp", "long"}

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
    cycling_labels = list({
        day["label"]
        for week in ctx["weeks"]
        for day in week["days"]
        if day["type"] in _BIKE_TYPES
    })
    ctx["workout_descs"] = prefetch_workout_descriptions(cycling_labels)

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
            if stype == "rest" or day["date"] >= today:
                day["completed"] = None
                day["actual_min"] = None
            else:
                day_acts = acts_by_date.get(day["date"].isoformat(), [])
                valid_keys = ACTIVITY_MATCH.get(stype, set())
                matched = [a for a in day_acts if a["type_key"] in valid_keys]
                day["completed"] = bool(matched)
                actual = int(sum(a.get("duration_seconds", 0) or 0 for a in matched) / 60)
                day["actual_min"] = actual if matched else None
                done_min += actual
        week["plan_min_fmt"] = _fmt_min(plan_min)
        week["done_min_fmt"] = _fmt_min(done_min) if done_min else None
        week["completion_pct"] = int(done_min / plan_min * 100) if plan_min and done_min else None

    return TEMPLATES.TemplateResponse(request=request, name="calendar.html", context=ctx)


@app.get("/training", response_class=HTMLResponse)
async def training_plan(request: Request):
    return TEMPLATES.TemplateResponse(request=request, name="training.html", context={})


@app.get("/nutrition", response_class=HTMLResponse)
async def nutrition_plan(request: Request):
    weeks = build_calendar_weeks()
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


@app.get("/refresh", response_class=RedirectResponse)
async def refresh(date: Optional[str] = None):
    target = date_fromisoformat_safe(date) if date else _today()
    _build_context(target, force_fetch=True)
    redirect_url = f"/?date={target.isoformat()}"
    return RedirectResponse(url=redirect_url, status_code=303)


def _today() -> date:
    from datetime import date as _date
    return _date.today()


def date_fromisoformat_safe(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return _today()


def run(host: str = "0.0.0.0", port: int = 8080) -> None:
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="warning")
