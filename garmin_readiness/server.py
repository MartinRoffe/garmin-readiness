from __future__ import annotations

import os
import secrets
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

from .analysis import generate_recovery_suggestion, load_analyses_for_activities, prefetch_nutrition_targets, prefetch_workout_descriptions, refresh_analyses
from .client import get_api
from .display import FIELD_LABELS, fmt_value, readiness_label, enrich_activity
from .plan import (PLAN_START as _PLAN_START, build_calendar_weeks, build_camp_weeks,
                   build_charity_weeks, build_event_prep_weeks, COMPOUND_SESSIONS,
                   CAMP_GRID_WORKOUTS, EVENT_PREP_DAYS, session_for_date)
from .report import generate_advice, generate_dashboard_explainer, generate_pmc_analysis, generate_pmc_explainer
from .body import bp_classification, fetch_body_composition, fetch_blood_pressure
from .history import (
    ACTIVITY_MATCH,
    baseline_stats,
    composite_score,
    history_for_chart,
    load,
    load_activities_by_date,
    load_body_metrics,
    load_blood_pressure,
    load_recent_activities,
    pmc_history,
    raw_history,
    save,
    save_activities,
    save_body_metrics,
    save_blood_pressure,
    seven_day_composite_trend_csv,
    z_score,
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


def _build_calendar_ctx() -> dict[str, Any]:
    return {
        "weeks": build_calendar_weeks(),
        "today": date.today(),
        "plan_start": _PLAN_START,
        "camp_weeks": build_camp_weeks(),
        "event_prep_weeks": build_event_prep_weeks(),
        "charity_weeks": build_charity_weeks(),
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
    _wc = _week_completion()
    if target >= _PLAN_START and (_EVENT_DATE - target).days > 0:
        _week_pct = _wc.get("pct") if _wc else None
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
        "week_completion": _wc,
        "metric_explainer": generate_dashboard_explainer(),
        "sparklines": sparklines,
        "today_plan": today_plan,
        "swap_suggestion": swap_suggestion,
        "event_tracker": event_tracker,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, date: Optional[str] = None):
    target = date_fromisoformat_safe(date) if date else _today()
    ctx = _build_context(target)
    return TEMPLATES.TemplateResponse(request=request, name="dashboard.html", context=ctx)


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

    return TEMPLATES.TemplateResponse(
        request=request,
        name="performance.html",
        context={
            "history": history,
            "today": today_entry,
            "pmc_analysis": _pmc_cache[date_key],
            "pmc_explainer": generate_pmc_explainer(),
            "z2_points": z2_points,
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
    cycling_labels = {
        day["label"]
        for week in ctx["weeks"]
        for day in week["days"]
        if day["type"] in _BIKE_TYPES
    }
    cycling_labels |= {d["label"] for d in EVENT_PREP_DAYS if d["type"] in _BIKE_TYPES}
    cycling_labels |= {v["label"] for v in CAMP_GRID_WORKOUTS.values() if v["type"] in _BIKE_TYPES}
    ctx["workout_descs"] = prefetch_workout_descriptions(list(cycling_labels))

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
                    day["completed"] = all(s["completed"] for s in day["sub_sessions"])
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


@app.get("/tenerife", response_class=HTMLResponse)
async def tenerife_view(request: Request):
    return TEMPLATES.TemplateResponse(request=request, name="tenerife.html", context={})


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

    # Weight chart: one point per day (latest reading if multiple)
    weight_by_date: dict[str, Optional[float]] = {}
    fat_by_date: dict[str, Optional[float]] = {}
    for r in body_rows:
        d = r["date"]
        if r.get("weight_kg") is not None:
            weight_by_date[d] = r["weight_kg"]
        if r.get("fat_pct") is not None:
            fat_by_date[d] = r["fat_pct"]

    weight_dates = sorted(weight_by_date)
    weight_values = [weight_by_date[d] for d in weight_dates]
    fat_dates = sorted(fat_by_date)
    fat_values = [fat_by_date[d] for d in fat_dates]

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


def run(host: str = "0.0.0.0", port: int = 8743) -> None:
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="warning")
