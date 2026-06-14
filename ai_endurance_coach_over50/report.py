"""HTML email builder: assembles readiness data, Claude advice, and planned workout."""
from __future__ import annotations

import os
import smtplib
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import anthropic

from .display import FIELD_LABELS, fmt_value, readiness_label, enrich_activity
from .plan import session_for_date, session_for_date_extended
from .history import (
    ACTIVITY_MATCH,
    LOWER_IS_BETTER,
    baseline_stats,
    composite_score,
    get_cached_text,
    load_activities_by_date,
    load_advice,
    load_recent_activities,
    save_advice,
    set_cached_text,
    seven_day_composite_trend_csv,
    z_score,
)
from .metrics import DailyMetrics
from .llm import MODEL_FAST, MODEL_SMART

_UNSCORED = {"training_load_chronic", "vo2_max"}


def _build_prompt(m: DailyMetrics, stats: dict, comp_z: Optional[float]) -> str:
    target = m.date
    label, _ = readiness_label(comp_z)

    lines = [
        f"Date: {target.strftime('%A, %-d %B %Y')}",
        f"Composite readiness score: {f'{comp_z:+.2f}σ' if comp_z is not None else 'no baseline yet'} ({label})",
        "",
        "Metric details (z-score = deviation from personal 30-day baseline, positive = better):",
    ]

    for field, (label_str, unit) in FIELD_LABELS.items():
        value = getattr(m, field)
        val_str = fmt_value(field, value) + (unit if value is not None else "")
        if field in stats and value is not None:
            mean, std = stats[field]
            z = z_score(value, mean, std, field)
            lines.append(f"  {label_str}: {val_str}  (z={z:+.2f}, 30d avg={fmt_value(field, mean)}{unit})")
        else:
            lines.append(f"  {label_str}: {val_str}  (no baseline)")

    lines += [""]
    if m.hrv_status:
        lines.append(f"HRV status: {m.hrv_status}")
    if m.training_status_label:
        lines.append(f"Training status: {m.training_status_label}")
    if m.acwr is not None and m.acwr_status:
        lines.append(f"ACWR: {m.acwr:.2f} ({m.acwr_status.replace('_', ' ').lower()})")

    lines += ["", f"7-day composite trend (oldest→today): {seven_day_composite_trend_csv()}"]

    session = session_for_date_extended(target)
    if session:
        stype, label, dur = session
        dur_str = f"{dur}m" if dur and dur < 60 else (f"{dur // 60}h{dur % 60:02d}m" if dur and dur % 60 else f"{dur // 60}h") if dur else "—"
        lines += ["", f"Today's planned workout: {label} ({stype}, {dur_str})"]
    else:
        lines += ["", "Today's planned workout: not in plan period"]

    lines += [
        "",
        "Based on these metrics, please provide:",
        "1. A clear one-line recommendation: Train / Rest / Active Recovery",
        "2. Two or three sentences explaining the key signals driving that recommendation",
        "3. Comment on whether the planned workout is appropriate given today's readiness, and if not suggest a modification",
        "4. One watchout if any metric is concerning",
        "",
        "Keep the response concise — it will appear in a morning email. "
        "Use plain language, no bullet markdown, just short paragraphs. "
        "Address the user as 'you'.",
    ]
    return "\n".join(lines)


def generate_advice(m: DailyMetrics, stats: dict, comp_z: Optional[float]) -> str:
    cached = load_advice(m.date)
    if cached:
        return cached

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _rule_based_advice(m, stats, comp_z)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = _build_prompt(m, stats, comp_z)

    try:
        message = client.messages.create(
            model=MODEL_FAST,
            max_tokens=400,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
            system=(
                "You are a knowledgeable fitness coach who helps athletes decide whether to train or rest "
                "based on objective physiological data from a Garmin device. "
                "Be direct, warm, and evidence-based. Never be vague."
            ),
        )
        text = message.content[0].text
        save_advice(m.date, text)
        return text
    except anthropic.APIStatusError as e:
        import logging
        logging.getLogger(__name__).warning("Anthropic API error (%s), using rule-based advice", e.status_code)
        return _rule_based_advice(m, stats, comp_z)


def _rule_based_advice(m: DailyMetrics, stats: dict, comp_z: Optional[float]) -> str:
    """Fallback when no Anthropic API key is set."""
    if comp_z is None:
        return "Still building your baseline — keep logging data for a few more days."

    concerns = []
    if m.acwr is not None and m.acwr > 1.5:
        concerns.append(f"ACWR is {m.acwr:.2f} (high injury risk zone)")
    if m.hrv_status and m.hrv_status.upper() in ("LOW", "POOR"):
        concerns.append(f"HRV status is {m.hrv_status.lower()}")
    if m.sleep_score is not None and "sleep_score" in stats:
        mean, std = stats["sleep_score"]
        if z_score(m.sleep_score, mean, std, "sleep_score") < -1.0:
            concerns.append("sleep was significantly below your average")

    high_acwr = m.acwr is not None and m.acwr > 1.5

    if comp_z <= -1.0 or len(concerns) >= 2:
        rec = "Rest or Active Recovery"
        detail = f"Several signals are below par today: {', '.join(concerns)}. " if concerns else ""
        detail += "Keep today easy — a short walk or gentle mobility work is fine."
    elif high_acwr:
        rec = "Train Easy — High Injury Risk"
        detail = (
            f"Your overall readiness is around average, but ACWR is {m.acwr:.2f} "
            f"({m.acwr_status.replace('_', ' ').lower() if m.acwr_status else 'high'}) "
            "which sits in the elevated injury risk zone. If you train today, keep intensity low "
            "and avoid high-impact or max-effort sessions."
        )
    elif comp_z >= 0.5:
        rec = "Train"
        detail = "Your readiness is above your average. Good day to push it if planned."
    else:
        rec = "Train at Moderate Intensity"
        detail = "Readiness is around your average. Train as planned but don't force extra intensity."

    return f"Recommendation: {rec}\n\n{detail}"


def _workouts_html(activities: list[dict]) -> str:
    if not activities:
        return ""

    rows = ""
    for a in activities:
        stats_parts = []
        if a.get("duration_fmt"):
            stats_parts.append(a["duration_fmt"])
        if a.get("distance_fmt"):
            stats_parts.append(a["distance_fmt"])
        if a.get("pace_fmt"):
            stats_parts.append(a["pace_fmt"])
        if a.get("avg_hr"):
            stats_parts.append(f"{int(a['avg_hr'])} bpm avg")
        if a.get("calories"):
            stats_parts.append(f"{int(a['calories'])} kcal")

        date_str = a.get("date", "")
        date_display = date_str[5:].replace("-", " ") if date_str else ""

        rows += f"""
        <tr style="border-bottom:1px solid #f3f4f6;">
          <td style="padding:10px 12px;font-size:20px;width:32px;">{a['icon']}</td>
          <td style="padding:10px 12px;">
            <p style="margin:0;font-size:13px;font-weight:600;color:#111827;">{a.get('name') or a['type_label']}</p>
            <p style="margin:2px 0 0;font-size:11px;color:#9ca3af;">{a['type_label']} · {date_display}</p>
          </td>
          <td style="padding:10px 12px;text-align:right;font-size:12px;color:#6b7280;white-space:nowrap;">
            {'  ·  '.join(stats_parts)}
          </td>
        </tr>"""

    return f"""
        <!-- Workouts -->
        <tr>
          <td style="padding:0 32px 8px;">
            <p style="margin:0 0 12px;font-size:11px;color:#6b7280;letter-spacing:0.1em;text-transform:uppercase;border-top:1px solid #e5e7eb;padding-top:24px;">Last 7 Days · Workouts</p>
            <table width="100%" cellpadding="0" cellspacing="0">
              {rows}
            </table>
          </td>
        </tr>"""


def _planned_session_html(d: date) -> str:
    session = session_for_date_extended(d)
    if not session:
        return ""
    stype, label, dur = session
    dur_str = ""
    if dur:
        dur_str = f"{dur}m" if dur < 60 else (f"{dur // 60}h{dur % 60:02d}m" if dur % 60 else f"{dur // 60}h")

    type_colours = {
        "rest":     ("#f3f4f6", "#374151"),
        "strength": ("#f5f3ff", "#6d28d9"),
        "bike":     ("#ecfdf5", "#059669"),
        "tempo":    ("#fffbeb", "#d97706"),
        "ftp":      ("#fff7ed", "#ea580c"),
        "ruck":     ("#fdf2f8", "#be185d"),
        "long":     ("#fefce8", "#b45309"),
    }
    bg, fg = type_colours.get(stype, ("#f3f4f6", "#374151"))

    dur_part = f'<span style="font-size:12px;color:#6b7280;margin-left:8px;">{dur_str}</span>' if dur_str else ""
    return f"""
        <!-- Today's Workout -->
        <tr>
          <td style="padding:0 32px 24px;">
            <p style="margin:0 0 10px;font-size:11px;color:#6b7280;letter-spacing:0.1em;text-transform:uppercase;border-top:1px solid #e5e7eb;padding-top:24px;">Today's Planned Workout</p>
            <div style="background:{bg};border-radius:8px;padding:12px 16px;display:inline-block;">
              <span style="font-size:14px;font-weight:700;color:{fg};">{label}</span>{dur_part}
            </div>
          </td>
        </tr>"""


def _week_completion_html(today: date) -> str:
    """Return an HTML block showing this week's plan vs actual training time."""
    mon = today - timedelta(days=today.weekday())
    # Plan: full week (Mon–Sun); actual: Mon–yesterday only (today not done yet)
    acts_by_date = load_activities_by_date(mon, today - timedelta(days=1))

    plan_min = 0
    for i in range(7):
        session = session_for_date_extended(mon + timedelta(days=i))
        if session:
            stype, _, dur = session
            if stype != "rest" and dur:
                plan_min += dur

    done_min = 0
    for day_acts in acts_by_date.values():
        for a in day_acts:
            if any(a["type_key"] in keys for keys in ACTIVITY_MATCH.values()):
                done_min += int((a.get("duration_seconds", 0) or 0) / 60)

    if plan_min == 0:
        return ""

    pct = int(done_min / plan_min * 100)

    def fmt(m: int) -> str:
        if m < 60:
            return f"{m}m"
        h, r = divmod(m, 60)
        return f"{h}h{r:02d}m" if r else f"{h}h"

    pct_colour = "#34d399" if pct >= 90 else "#facc15" if pct >= 60 else "#f87171"
    bar_filled = min(pct, 100)
    bar_empty = 100 - bar_filled

    days_elapsed = (today - mon).days  # 0=Mon, 6=Sun
    week_num = (today - timedelta(days=today.weekday())).isocalendar()[1]

    return f"""
        <!-- Week completion -->
        <tr>
          <td style="padding:0 32px 24px;">
            <p style="margin:0 0 10px;font-size:11px;color:#6b7280;letter-spacing:0.1em;text-transform:uppercase;border-top:1px solid #e5e7eb;padding-top:24px;">This Week · Training Progress</p>
            <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;border-radius:8px;padding:14px 16px;">
              <tr>
                <td style="padding:0;">
                  <table width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                      <td style="font-size:28px;font-weight:700;color:{pct_colour};width:64px;">{pct}%</td>
                      <td style="padding-left:12px;">
                        <p style="margin:0;font-size:12px;color:#374151;">
                          <strong>{fmt(done_min)}</strong>
                          <span style="color:#9ca3af;"> of {fmt(plan_min)} planned</span>
                        </p>
                        <p style="margin:4px 0 8px;font-size:11px;color:#9ca3af;">
                          Day {days_elapsed + 1} of 7 · week {week_num}
                        </p>
                        <!-- progress bar -->
                        <table width="100%" cellpadding="0" cellspacing="0" style="border-radius:4px;overflow:hidden;height:6px;">
                          <tr>
                            <td width="{bar_filled}%" style="background:{pct_colour};height:6px;"></td>
                            <td width="{bar_empty}%" style="background:#e5e7eb;height:6px;"></td>
                          </tr>
                        </table>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>"""


def build_html(m: DailyMetrics, stats: dict, comp_z: Optional[float], advice: str, activities: list[dict] | None = None) -> str:
    label, _ = readiness_label(comp_z)

    score_colour = (
        "#34d399" if comp_z is not None and comp_z >= 0.25
        else "#f87171" if comp_z is not None and comp_z <= -0.25
        else "#facc15"
    )

    # Metric rows
    rows_html = ""
    for field, (label_str, unit) in FIELD_LABELS.items():
        value = getattr(m, field)
        val_str = fmt_value(field, value) + (unit if value is not None else "")
        context_only = field in _UNSCORED

        if field in stats and value is not None:
            mean, std = stats[field]
            z = z_score(value, mean, std, field)
            avg_str = fmt_value(field, mean) + unit
            z_str = f"{z:+.2f}σ"
            if z >= 0.5:
                z_col = "#16a34a"
            elif z <= -0.5:
                z_col = "#dc2626"
            else:
                z_col = "#ca8a04"
        else:
            avg_str = "—"
            z_str = "context" if context_only else "—"
            z_col = "#9ca3af"

        rows_html += f"""
        <tr style="border-bottom:1px solid #e5e7eb;">
          <td style="padding:8px 12px;color:#374151;font-size:13px;">{label_str}</td>
          <td style="padding:8px 12px;text-align:right;font-weight:600;font-size:13px;">{val_str}</td>
          <td style="padding:8px 12px;text-align:right;color:#6b7280;font-size:13px;">{avg_str}</td>
          <td style="padding:8px 12px;text-align:right;color:{z_col};font-size:13px;font-weight:600;">{z_str}</td>
        </tr>"""

    # Status badges
    badges_html = ""
    badge_items = []
    if m.hrv_status:
        badge_items.append(f"HRV {m.hrv_status.title()}")
    if m.training_status_label:
        badge_items.append(f"Training {m.training_status_label}")
    if m.acwr is not None and m.acwr_status:
        badge_items.append(f"ACWR {m.acwr:.2f} · {m.acwr_status.replace('_', ' ').title()}")
    for badge in badge_items:
        badges_html += (
            f'<span style="display:inline-block;margin:3px 4px 3px 0;padding:3px 10px;'
            f'background:#f3f4f6;border-radius:20px;font-size:12px;color:#374151;">'
            f'{badge}</span>'
        )

    # Advice paragraphs
    advice_html = "".join(
        f'<p style="margin:0 0 10px;line-height:1.6;color:#1f2937;">{p.strip()}</p>'
        for p in advice.strip().split("\n\n") if p.strip()
    )

    score_display = f"{comp_z:+.2f}σ" if comp_z is not None else "—"

    return f"""<!doctype html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;">
    <tr><td align="center" style="padding:32px 16px;">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1);">

        <!-- Header -->
        <tr>
          <td style="background:#111827;padding:28px 32px;">
            <p style="margin:0 0 4px;font-size:11px;color:#6b7280;letter-spacing:0.1em;text-transform:uppercase;">Daily Readiness</p>
            <p style="margin:0;font-size:22px;font-weight:700;color:#f9fafb;">{m.date.strftime('%A, %-d %B %Y')}</p>
            <p style="margin:8px 0 0;font-size:36px;font-weight:800;color:{score_colour};">{score_display}
              <span style="font-size:16px;color:#9ca3af;font-weight:400;margin-left:8px;">{label}</span>
            </p>
            <div style="margin-top:12px;">{badges_html}</div>
          </td>
        </tr>

        <!-- Advice -->
        <tr>
          <td style="padding:24px 32px 16px;">
            <p style="margin:0 0 12px;font-size:11px;color:#6b7280;letter-spacing:0.1em;text-transform:uppercase;">Today's Advice</p>
            {advice_html}
          </td>
        </tr>

        {_planned_session_html(m.date)}
        {_week_completion_html(m.date)}

        <!-- Metrics table -->
        <tr>
          <td style="padding:24px 32px 8px;">
            <p style="margin:0 0 12px;font-size:11px;color:#6b7280;letter-spacing:0.1em;text-transform:uppercase;">Metrics</p>
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr style="border-bottom:2px solid #e5e7eb;">
                <th style="padding:6px 12px;text-align:left;font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;">Metric</th>
                <th style="padding:6px 12px;text-align:right;font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;">Today</th>
                <th style="padding:6px 12px;text-align:right;font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;">30d Avg</th>
                <th style="padding:6px 12px;text-align:right;font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;">vs Baseline</th>
              </tr>
              {rows_html}
            </table>
          </td>
        </tr>

        {_workouts_html([enrich_activity(a) for a in (activities or [])])}

        <!-- Footer -->
        <tr>
          <td style="padding:20px 32px;background:#f9fafb;border-top:1px solid #e5e7eb;">
            <p style="margin:0;font-size:11px;color:#9ca3af;text-align:center;">
              Generated from your Garmin data · 30-day rolling baseline · {len(stats)} metrics tracked
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def send_email(html: str, subject: str, to_addr: str, from_addr: str, app_password: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(from_addr, app_password)
            server.sendmail(from_addr, to_addr, msg.as_string())
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(
            "Gmail rejected the login — check GMAIL_ADDRESS/GMAIL_APP_PASSWORD"
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise RuntimeError(f"Failed to send email via Gmail SMTP: {exc}") from exc


def run_report(m: DailyMetrics, dry_run: bool = False) -> None:
    """Generate and send (or print) the daily readiness email."""
    from .alerts import check_fatigue_alerts
    stats = baseline_stats(m.date)
    comp_z = composite_score(m, stats)
    label, _ = readiness_label(comp_z)

    activities = load_recent_activities(days=7)
    advice = generate_advice(m, stats, comp_z)
    score_str = f"{comp_z:+.2f}σ" if comp_z is not None else "—"
    subject = f"Readiness {m.date.strftime('%-d %b')} · {score_str} {label}"
    html = build_html(m, stats, comp_z, advice, activities)

    # Prepend HIGH fatigue alerts to the email
    fatigue_alerts = [a for a in check_fatigue_alerts(m.date) if a["severity"] == "HIGH"]
    if fatigue_alerts:
        alert_rows = "".join(
            f'<tr><td style="padding:10px 16px;font-size:13px;color:#7f1d1d;">'
            f'<strong>⚠ {a["type"].replace("_", " ")}</strong>: {a["message"]}</td></tr>'
            for a in fatigue_alerts
        )
        alert_block = (
            '<table width="100%" cellpadding="0" cellspacing="0" style="background:#fef2f2;'
            'border-left:4px solid #ef4444;margin-bottom:16px;border-radius:0 4px 4px 0;">'
            f'{alert_rows}</table>'
        )
        html = html.replace("<!-- Advice -->", f"<!-- Alerts -->\n        <tr><td style='padding:16px 32px 0;'>{alert_block}</td></tr>\n\n        <!-- Advice -->")

    # HRV traffic-light callout (amber/red days) — rule-based, no Claude call
    modulation = None
    try:
        from .modulation import session_modulation
        modulation = session_modulation(m.date, m, comp_z)
    except Exception:
        modulation = None
    if modulation and modulation.get("light", {}).get("status") in ("amber", "red"):
        light = modulation["light"]
        is_red = light["status"] == "red"
        bg, border, fg = (("#fef2f2", "#ef4444", "#7f1d1d") if is_red
                          else ("#fffbeb", "#f59e0b", "#78350f"))
        if modulation.get("label"):
            mod_text = (
                f"{modulation.get('headline', 'Adjust today')}: {light['reason']}. "
                f"Suggested: <strong>{modulation['label']} ({modulation['duration_min']} min)</strong> "
                f"instead of {modulation.get('planned_label', 'the planned session')}. "
                "Open the dashboard to apply."
            )
        else:
            mod_text = f"{light['reason']}. Keep today genuinely easy."
        mod_block = (
            f'<table width="100%" cellpadding="0" cellspacing="0" style="background:{bg};'
            f'border-left:4px solid {border};margin-bottom:16px;border-radius:0 4px 4px 0;">'
            f'<tr><td style="padding:10px 16px;font-size:13px;color:{fg};">'
            f'<strong>{"🔴" if is_red else "🟠"} HRV {light["status"].upper()} DAY</strong>: {mod_text}'
            f'</td></tr></table>'
        )
        html = html.replace("<!-- Advice -->", f"<!-- Modulation -->\n        <tr><td style='padding:16px 32px 0;'>{mod_block}</td></tr>\n\n        <!-- Advice -->")

    if dry_run:
        print(f"Subject: {subject}\n")
        print("--- Advice ---")
        print(advice)
        return

    to_addr = os.getenv("REPORT_TO", os.getenv("GMAIL_ADDRESS", ""))
    from_addr = os.getenv("GMAIL_ADDRESS", "")
    app_password = os.getenv("GMAIL_APP_PASSWORD", "")

    if not all([to_addr, from_addr, app_password]):
        raise RuntimeError(
            "Set GMAIL_ADDRESS, GMAIL_APP_PASSWORD, and REPORT_TO in your .env"
        )

    send_email(html, subject, to_addr, from_addr, app_password)
    print(f"Email sent to {to_addr}  [{subject}]")


# ── PMC analysis ─────────────────────────────────────────────────────────────

def _build_pmc_prompt(history: list[dict], m=None, comp_z: Optional[float] = None) -> str:
    today = date.today()
    recent = [h for h in history if h["ctl"] is not None]
    if not recent:
        return ""

    cur = recent[-1]
    week_ago = recent[-8] if len(recent) >= 8 else recent[0]

    ctl_delta = round(cur["ctl"] - week_ago["ctl"], 1) if week_ago["ctl"] else None
    atl_delta = round(cur["atl"] - week_ago["atl"], 1) if week_ago["atl"] else None
    tsb_delta = round(cur["tsb"] - week_ago["tsb"], 1) if week_ago["tsb"] is not None and week_ago["tsb"] is not None else None

    lines = [
        "Performance Management Chart data (Garmin training-load units, not Coggan TSS):",
        f"  Today — CTL (fitness): {cur['ctl']}  ATL (fatigue): {cur['atl']}  TSB (form): {cur['tsb']}",
        f"  7-day change — CTL: {f'{ctl_delta:+.1f}' if ctl_delta is not None else '—'}  "
        f"ATL: {f'{atl_delta:+.1f}' if atl_delta is not None else '—'}  "
        f"TSB: {f'{tsb_delta:+.1f}' if tsb_delta is not None else '—'}",
        "",
        "Last 14 days (date · CTL · ATL · TSB):",
    ]
    for h in recent[-14:]:
        lines.append(f"  {h['date']}  CTL={h['ctl']}  ATL={h['atl']}  TSB={h['tsb']}")

    # Include today's recovery context so this analysis doesn't contradict the daily readiness advice
    if m is not None or comp_z is not None:
        lines += ["", "Today's recovery context (from Garmin biometric data):"]
        if comp_z is not None:
            from .display import readiness_label
            label, _ = readiness_label(comp_z)
            lines.append(f"  Composite readiness score: {comp_z:+.2f}σ ({label})")
        if m is not None:
            if getattr(m, "hrv_last_night", None) is not None:
                lines.append(f"  HRV last night: {m.hrv_last_night} ms")
            if getattr(m, "hrv_status", None):
                lines.append(f"  HRV status: {m.hrv_status}")
            if getattr(m, "sleep_score", None) is not None:
                lines.append(f"  Sleep score: {m.sleep_score}")
            if getattr(m, "avg_stress", None) is not None:
                lines.append(f"  Avg stress: {m.avg_stress}")
            if getattr(m, "acwr", None) is not None:
                lines.append(f"  ACWR: {m.acwr:.2f}" + (f" ({m.acwr_status.replace('_',' ').lower()})" if getattr(m, "acwr_status", None) else ""))

    lines += ["", "Upcoming 7 days (planned sessions):"]
    for i in range(7):
        d = today + timedelta(days=i)
        session = session_for_date_extended(d)
        if session:
            stype, label, dur = session
            dur_str = f"{dur}m" if dur and dur < 60 else (f"{dur // 60}h{dur % 60:02d}m" if dur and dur % 60 else f"{dur // 60}h") if dur else "—"
            lines.append(f"  {d.isoformat()} ({d.strftime('%a')})  {label} [{stype}] {dur_str}")
        else:
            lines.append(f"  {d.isoformat()} ({d.strftime('%a')})  outside plan")

    lines += [
        "",
        "Note: CTL uses 28-day window (not classic 42-day), so it responds faster than TrainingPeaks.",
        "      TSB thresholds are relative to zero only — do not apply Coggan absolute zones.",
        "      The daily train/rest recommendation is handled separately — do not repeat it here.",
        "",
        "Please provide a concise training-load analysis covering:",
        "1. Current form: is TSB in a sustainable zone or showing signs of overreaching?",
        "2. Fitness trajectory: is CTL building as expected for this stage of the block?",
        "3. One week-level or block-level suggestion (e.g. load distribution, recovery timing) "
        "that accounts for both the PMC numbers AND today's recovery context if provided.",
        "Keep it under 130 words. Plain paragraphs, no headers or bullets. Address the athlete as 'you'.",
    ]
    return "\n".join(lines)


def _rule_based_pmc(history: list[dict]) -> str:
    recent = [h for h in history if h["tsb"] is not None]
    if not recent:
        return "Not enough training load data yet — keep logging to build your baseline."
    cur = recent[-1]
    tsb = cur["tsb"]
    ctl = cur["ctl"]
    if tsb < -200:
        tone = "Your form is very deep in the red — fatigue is significantly outpacing fitness. A recovery day or two would help consolidate the training gains."
    elif tsb < -100:
        tone = "You're carrying a substantial training load. This is a productive stress zone, but watch for signs of accumulated fatigue."
    elif tsb < 0:
        tone = "Moderate fatigue relative to fitness — a normal training state. Continue as planned."
    else:
        tone = "You're in positive form: fitness exceeds current fatigue. Good time for a quality session or race effort."
    ctl_str = f"CTL is {ctl:.0f}" if ctl else "CTL data available"
    return f"{ctl_str} with TSB at {tsb:.0f}. {tone}"


def generate_pmc_analysis(history: list[dict], m=None, comp_z: Optional[float] = None) -> str:
    """Return a short Claude Haiku commentary on the current PMC state.

    Cached in text_cache keyed by date so restarts don't produce a different answer.
    Accepts today's DailyMetrics and comp_z so the analysis is consistent with readiness advice.
    """
    cache_key = f"pmc_analysis_v2_{date.today().isoformat()}"
    cached = get_cached_text(cache_key)
    if cached:
        return cached

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _rule_based_pmc(history)

    prompt = _build_pmc_prompt(history, m, comp_z)
    if not prompt:
        return _rule_based_pmc(history)

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=MODEL_SMART,
            max_tokens=320,
            system=(
                "You are an experienced endurance coach reviewing an athlete's training load and recovery data. "
                "Be direct, specific, and evidence-based. Reference the actual numbers. "
                "Your role here is to assess the WEEKLY training load trajectory and block periodization — "
                "not to repeat today's daily train/rest recommendation (that is handled separately). "
                "No bullet markdown — short paragraphs only. Address the athlete as 'you'."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
        set_cached_text(cache_key, text)
        return text
    except Exception:
        return _rule_based_pmc(history)


_PMC_EXPLAINER_KEY = "pmc_explainer_v1"


def generate_pmc_explainer() -> str:
    """Return a plain-English explanation of ATL, CTL, and TSB — cached permanently."""
    cached = get_cached_text(_PMC_EXPLAINER_KEY)
    if cached:
        return cached

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return ""

    prompt = (
        "Explain ATL, CTL, and TSB (Training Stress Balance) to an amateur endurance athlete "
        "in plain, practical language. Context: the athlete is 50+, trains 6+ hours per week "
        "mixing cycling, strength, and rucking, and is trying to build fitness while losing weight. "
        "The values come from Garmin, which uses a 7-day window for ATL and 28-day for CTL — "
        "these are relative Garmin load units, not Coggan TSS, so absolute thresholds differ.\n\n"
        "Cover:\n"
        "1. What each metric measures and why it matters\n"
        "2. How to read the relationship between them day-to-day\n"
        "3. What TSB values to watch for (positive = fresh, negative = fatigued, when to be concerned)\n"
        "4. One or two practical tips for acting on these numbers\n\n"
        "Use short paragraphs with a bold heading per section (use ** markdown bold). "
        "Aim for around 200 words. Be direct and concrete — no vague generalities."
    )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=MODEL_SMART,
            max_tokens=600,
            system=(
                "You are a knowledgeable endurance coach writing a concise reference guide "
                "for an athlete who wants to understand their training metrics. "
                "Write in second person, keep it practical."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
        set_cached_text(_PMC_EXPLAINER_KEY, text)
        return text
    except Exception:
        return ""


_DASHBOARD_EXPLAINER_KEY = "dashboard_explainer_v1"


def generate_dashboard_explainer() -> str:
    """Plain-English explainer for all dashboard metrics — cached permanently."""
    cached = get_cached_text(_DASHBOARD_EXPLAINER_KEY)
    if cached:
        return cached

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return ""

    prompt = (
        "Explain the following five metrics shown on an athlete's daily readiness dashboard. "
        "The athlete is 50+, male, ~85 kg, training 6+ hours/week with a mix of Zone 2 cycling, "
        "tempo intervals, strength training, and weighted rucking. "
        "The metrics use Garmin's units (not Coggan TSS). CTL uses a 28-day window, ATL uses 7 days.\n\n"
        "Write a separate section for each metric with a **bold heading**. "
        "Keep each section to 3–5 sentences. Be direct and practical — explain what it means "
        "day-to-day and what to do about it. Address the athlete as 'you'.\n\n"
        "Metrics to explain:\n"
        "1. **Composite Score** — a mean z-score across all readiness metrics (HRV, sleep, resting HR, "
        "stress, body battery, etc.), measured in standard deviations from a 30-day personal baseline. "
        "Positive = above your norm, negative = below. Lower-is-better fields are sign-flipped.\n"
        "2. **ACWR (Acute:Chronic Workload Ratio)** — ATL divided by CTL. "
        "The sweet spot is roughly 0.8–1.3. Above 1.5 is high injury-risk territory.\n"
        "3. **ATL (Acute Training Load)** — 7-day rolling training load. Represents current fatigue.\n"
        "4. **CTL (Chronic Training Load)** — 28-day rolling training load. Represents fitness base.\n"
        "5. **TSB (Training Stress Balance)** — CTL minus ATL. Positive = fresh, negative = fatigued. "
        "Not absolute Coggan zones — interpret relative to zero only.\n\n"
        "End with one short paragraph titled **How they work together** connecting all five."
    )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=MODEL_FAST,
            max_tokens=800,
            system=(
                "You are a knowledgeable endurance coach writing a concise reference guide "
                "for an athlete who wants to understand their training metrics. "
                "Use **bold** markdown for section headings. Write in second person."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
        set_cached_text(_DASHBOARD_EXPLAINER_KEY, text)
        return text
    except Exception:
        return ""


def _build_sleep_prompt(data: list[dict], avgs_7: dict, avgs_30: dict) -> str:
    today = date.today()
    lines = [
        f"Date: {today.isoformat()} ({today.strftime('%A')})",
        "",
        "Athlete profile: male, 50+, amateur endurance athlete. Currently in a 12-week charity "
        "cycling and strength block. Training 6–8 hours/week mixing Zone 2 cycling, tempo "
        "intervals, strength, and rucking.",
        "",
        "30-night sleep data (oldest → most recent, '-' = no data):",
        "Date        Score  Hours  Deep%  REM%   HRV(ms)",
    ]
    for d in data:
        score  = f"{d['sleep_score']:>5.0f}" if d['sleep_score'] is not None else "    -"
        hrs    = f"{d['sleep_hours']:>5.1f}" if d['sleep_hours'] is not None else "    -"
        deep   = f"{d['deep_pct']:>5.0f}" if d['deep_pct'] is not None else "    -"
        rem    = f"{d['rem_pct']:>4.0f}" if d['rem_pct'] is not None else "   -"
        hrv    = f"{d['hrv']:>6.0f}" if d['hrv'] is not None else "     -"
        lines.append(f"{d['date']}  {score}  {hrs}  {deep}  {rem}  {hrv}")

    lines += [
        "",
        "7-day averages:",
        f"  Sleep score: {avgs_7.get('sleep_score') or '-'}  "
        f"Total sleep: {avgs_7.get('sleep_hours') or '-'}h  "
        f"Deep: {avgs_7.get('deep_pct') or '-'}%  "
        f"REM: {avgs_7.get('rem_pct') or '-'}%  "
        f"HRV: {avgs_7.get('hrv') or '-'} ms",
        "30-day averages:",
        f"  Sleep score: {avgs_30.get('sleep_score') or '-'}  "
        f"Total sleep: {avgs_30.get('sleep_hours') or '-'}h  "
        f"Deep: {avgs_30.get('deep_pct') or '-'}%  "
        f"REM: {avgs_30.get('rem_pct') or '-'}%  "
        f"HRV: {avgs_30.get('hrv') or '-'} ms",
        "",
        "Reference ranges for endurance athletes:",
        "  Total sleep: 7.5–9h optimal. Deep sleep: >13% (ideally 15–20%). "
        "REM: >20%. HRV suppression >10% below 7d avg for 3+ days suggests accumulated fatigue.",
        "",
        "Please provide a concise sleep quality analysis covering:",
        "1. Current sleep quality trend — is it improving, declining, or stable vs 30d baseline?",
        "2. Stage architecture — are deep and REM percentages adequate for endurance recovery? "
        "Note any concerning nights.",
        "3. One or two specific, actionable recommendations for this athlete to improve sleep "
        "quality or how to interpret the data in the context of their training block.",
        "Keep it under 150 words. Short paragraphs, no headers or bullets. Address the athlete as 'you'.",
    ]
    return "\n".join(lines)


def _rule_based_sleep(data: list[dict], avgs_7: dict) -> str:
    score = avgs_7.get("sleep_score")
    deep  = avgs_7.get("deep_pct")
    rem   = avgs_7.get("rem_pct")
    if score is None:
        return "Not enough sleep data yet — keep syncing to build your baseline."
    parts = []
    if score >= 75:
        parts.append(f"Your 7-day average sleep score of {score:.0f} is strong.")
    elif score >= 55:
        parts.append(f"Your 7-day average sleep score of {score:.0f} is moderate — there's room to improve.")
    else:
        parts.append(f"Your 7-day average sleep score of {score:.0f} is low — prioritise sleep this week.")
    if deep is not None and deep < 13:
        parts.append(f"Deep sleep is running low at {deep:.0f}% — try an earlier bedtime and limit alcohol.")
    if rem is not None and rem < 18:
        parts.append(f"REM is below target at {rem:.0f}% — stress and late training sessions can suppress REM.")
    return " ".join(parts) if parts else f"Average sleep score: {score:.0f}."


def generate_sleep_analysis(data: list[dict], avgs_7: dict, avgs_30: dict) -> str:
    """Return a Sonnet coaching commentary on the 30-night sleep picture. Cached daily."""
    cache_key = f"sleep_analysis_v1_{date.today().isoformat()}"
    cached = get_cached_text(cache_key)
    if cached:
        return cached

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _rule_based_sleep(data, avgs_7)

    prompt = _build_sleep_prompt(data, avgs_7, avgs_30)
    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=MODEL_SMART,
            max_tokens=400,
            system=(
                "You are an experienced endurance coach reviewing an athlete's 30-night sleep data. "
                "Be direct, specific, and evidence-based — reference actual numbers from the data. "
                "No bullet points or markdown headers. Short paragraphs only. Address the athlete as 'you'."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
        set_cached_text(cache_key, text)
        return text
    except Exception:
        return _rule_based_sleep(data, avgs_7)


# ── Body composition advisor ──────────────────────────────────────────────────

_TENERIFE_DATE = date(2026, 8, 13)
_CHARITY_DATE  = date(2026, 9, 13)


def _build_body_prompt(body_rows: list[dict], latest: dict, pmc_today: dict, recent_metrics: list[dict]) -> str:
    today = date.today()
    weeks_to_tenerife = max(0, (_TENERIFE_DATE - today).days // 7)
    weeks_to_charity  = max(0, (_CHARITY_DATE  - today).days // 7)

    lines = [
        f"Date: {today.isoformat()} ({today.strftime('%A')})",
        f"Athlete profile: male, 50+, amateur endurance athlete. "
        f"Targets: Tenerife cycling camp {_TENERIFE_DATE.strftime('%-d %b %Y')} ({weeks_to_tenerife} weeks), "
        f"Ghent→Amsterdam charity ride {_CHARITY_DATE.strftime('%-d %b %Y')} ({weeks_to_charity} weeks). "
        f"Training 6–8 hours/week: Zone 2 cycling, tempo intervals, kettlebell strength, rucking.",
        "",
    ]

    # Latest readings
    def _f(v, dp=1): return f"{v:.{dp}f}" if v is not None else "—"
    lines += [
        "Latest body composition readings:",
        f"  Weight: {_f(latest.get('weight_kg'))} kg  |  Body fat: {_f(latest.get('fat_pct'))}%  |  Muscle mass: {_f(latest.get('muscle_mass_kg'))} kg",
        f"  Bone mass: {_f(latest.get('bone_mass_kg'), 2)} kg  |  Hydration: {_f(latest.get('hydration_pct'))}%  |  Visceral fat index: {_f(latest.get('visceral_fat'), 0)}",
        f"  BMI: {_f(latest.get('bmi'))}  |  Metabolic age: {_f(latest.get('metabolic_age'), 0)} years",
        "",
    ]

    # Weight & fat trend — weekly snapshots
    weight_rows = [r for r in body_rows if r.get("weight_kg") is not None]
    fat_rows    = [r for r in body_rows if r.get("fat_pct") is not None]
    muscle_rows = [r for r in body_rows if r.get("muscle_mass_kg") is not None]

    if weight_rows:
        # Rate of change: first half vs second half average
        mid = len(weight_rows) // 2
        first_avg  = sum(r["weight_kg"] for r in weight_rows[:mid]) / max(len(weight_rows[:mid]), 1)
        second_avg = sum(r["weight_kg"] for r in weight_rows[mid:]) / max(len(weight_rows[mid:]), 1)
        weekly_delta = (second_avg - first_avg) / max((len(weight_rows) // 2) / 7, 1)

        # Project to Tenerife
        projected = latest["weight_kg"] + weekly_delta * weeks_to_tenerife if latest.get("weight_kg") else None

        lines += [
            f"Weight trend (approx weekly rate): {weekly_delta:+.2f} kg/week  "
            f"({'losing' if weekly_delta < 0 else 'gaining'} weight)",
        ]
        if projected:
            lines.append(f"  → Projected weight at Tenerife ({_TENERIFE_DATE.strftime('%-d %b')}): {projected:.1f} kg  "
                         f"(if current rate continues)")
        lines.append("")

    # Compact trend table (every 7th reading, up to 13 rows)
    sampled = weight_rows[::max(1, len(weight_rows) // 13)]
    if sampled:
        lines.append("Body composition trend (sampled weekly, oldest → most recent):")
        lines.append("  Date          Weight  Fat%  Muscle")
        for r in sampled:
            fat_s    = f"{r['fat_pct']:.1f}%" if r.get("fat_pct") is not None else "—"
            muscle_s = f"{r['muscle_mass_kg']:.1f}" if r.get("muscle_mass_kg") is not None else "—"
            lines.append(f"  {r['date']}    {r['weight_kg']:.1f} kg  {fat_s:>5}  {muscle_s}")
        lines.append("")

    # Training context
    ctl = pmc_today.get("ctl")
    atl = pmc_today.get("atl")
    tsb = pmc_today.get("tsb")
    if any(v is not None for v in [ctl, atl, tsb]):
        lines += [
            "Training load context (Garmin units):",
            f"  CTL (fitness): {_f(ctl, 0)}  ATL (fatigue): {_f(atl, 0)}  TSB (form): {_f(tsb, 0)}",
            "",
        ]

    # NEAT context (14-day average steps + active calories)
    step_vals  = [r.get("total_steps")    for r in recent_metrics if r.get("total_steps")    is not None]
    cal_vals   = [r.get("active_calories") for r in recent_metrics if r.get("active_calories") is not None]
    if step_vals or cal_vals:
        avg_steps = round(sum(step_vals) / len(step_vals)) if step_vals else None
        avg_cals  = round(sum(cal_vals)  / len(cal_vals))  if cal_vals  else None
        lines += [
            "Non-exercise activity (14-day average):",
            f"  Daily steps: {avg_steps:,}" if avg_steps else "  Daily steps: —",
            f"  Active calories: {avg_cals} kcal/day" if avg_cals else "  Active calories: —",
            "",
        ]

    # Calorie intake (food log — available once user logs meals in Garmin Connect)
    con_vals = [r.get("calories_consumed")      for r in recent_metrics if r.get("calories_consumed")      is not None]
    adj_vals = [r.get("calorie_goal_adjusted")  for r in recent_metrics if r.get("calorie_goal_adjusted")  is not None]
    if con_vals:
        avg_consumed = round(sum(con_vals) / len(con_vals))
        avg_tdee     = round(sum(adj_vals) / len(adj_vals)) if adj_vals else None
        deficit_note = ""
        if avg_tdee:
            diff = avg_tdee - avg_consumed
            deficit_note = f"  avg deficit vs TDEE: {diff:+,} kcal/day"
        lines += [
            "Calorie intake (logged in Garmin Connect, last 14 days):",
            f"  Avg consumed: {avg_consumed:,} kcal/day",
            f"  Avg TDEE (activity-adjusted): {avg_tdee:,} kcal/day" if avg_tdee else "",
            deficit_note,
            "  Note: a sustained deficit of ~500 kcal/day ≈ 0.5 kg/week fat loss."
            " If deficit exceeds 700 kcal/day on training days, flag risk of under-fuelling.",
            "",
        ]

    lines += [
        "Please provide a concise body composition analysis covering:",
        "1. Weight trajectory — at current rate, what weight will the athlete reach by Tenerife (13 Aug)?",
        "   How significant is that for W/kg (watts per kilogram) on mountain climbs?",
        "2. Body composition quality — is weight change driven by fat loss, muscle loss, or both?",
        "   Flag if muscle mass is declining (suggests need for more protein / strength work).",
        "3. One encouraging finding or specific concern from the data (visceral fat, hydration, metabolic age, BP trend).",
        "4. One actionable recommendation for the next 4 weeks given the athlete's training load and event timeline.",
        "",
        "Keep it under 180 words. Short paragraphs, no headers or bullets. Address the athlete as 'you'.",
        "Reference actual numbers from the data.",
    ]
    return "\n".join(lines)


def _rule_based_body(latest: dict) -> str:
    if not latest:
        return "No body composition data yet — sync your Withings scale to start tracking."
    parts = []
    w = latest.get("weight_kg")
    vf = latest.get("visceral_fat")
    bmi = latest.get("bmi")
    if w:
        parts.append(f"Current weight: {w:.1f} kg.")
    if vf is not None:
        if vf >= 13:
            parts.append(f"Visceral fat index is {vf:.0f} — above the healthy threshold of 13. Reducing this is a priority.")
        else:
            parts.append(f"Visceral fat index is {vf:.0f} — within the healthy range.")
    if bmi:
        parts.append(f"BMI: {bmi:.1f}.")
    parts.append("Sync more data and add an Anthropic API key for detailed AI analysis.")
    return " ".join(parts)


def generate_weekly_briefing(week_sessions: list[tuple], pmc_today: dict, comp_z: Optional[float]) -> Optional[str]:
    """Generate a Monday coach briefing for the coming week. Cached per ISO week."""
    from .plan import PLAN_START
    today = date.today()
    mon = today - timedelta(days=today.weekday())
    cache_key = f"weekly_briefing_v2_{mon.isoformat()}"
    cached = get_cached_text(cache_key)
    if cached:
        return cached

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    days_into = (today - PLAN_START).days
    week_num = max(1, days_into // 7 + 1) if today >= PLAN_START else 1

    sess_lines = []
    for day_name, stype, label, dur_min in week_sessions:
        dur_str = f"{dur_min}m" if dur_min < 60 else (f"{dur_min // 60}h{dur_min % 60:02d}m" if dur_min % 60 else f"{dur_min // 60}h")
        sess_lines.append(f"  {day_name}: {label} ({stype}, {dur_str})")

    ctl = pmc_today.get("ctl") or "—"
    atl = pmc_today.get("atl") or "—"
    tsb = pmc_today.get("tsb") or "—"
    z_str = f"{comp_z:+.2f}σ" if comp_z is not None else "—"

    retest_note = ""
    try:
        from .history import ftp_retest_due
        due = ftp_retest_due(today, plan_start=PLAN_START)
        if due:
            if due.get("age_days"):
                retest_note = (f"\nNOTE: the last FTP test was {due['age_days']} days ago; "
                               "recommend slotting a re-test this week.\n")
            else:
                retest_note = "\nNOTE: no FTP test logged yet; recommend slotting one this week.\n"
    except Exception:
        pass

    prompt = (
        f"Week {week_num} of the training block is starting. Here are the planned sessions:\n"
        + "\n".join(sess_lines) + "\n\n"
        f"Current PMC: CTL={ctl}, ATL={atl}, TSB={tsb}. Readiness: {z_str}.\n"
        + retest_note + "\n"
        "Provide a structured Monday briefing with exactly these four parts:\n"
        "(a) ONE sentence on current form (use the TSB/readiness data).\n"
        "(b) The KEY session of this week and WHY it matters for the event block.\n"
        "(c) ONE execution focus cue for the hardest session.\n"
        "(d) A ONE sentence pacing note for any hard/tempo/FTP session.\n\n"
        "Be specific and reference actual numbers. Under 120 words total. Plain text, no bullets."
    )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=MODEL_FAST,
            max_tokens=300,
            system=(
                "You are a concise endurance coach writing a Monday morning briefing for an amateur "
                "cyclist. Be specific, actionable, and direct."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
        set_cached_text(cache_key, text)
        return text
    except Exception:
        return None


def generate_body_analysis(body_rows: list[dict], latest: dict, pmc_today: dict, recent_metrics: list[dict]) -> str:
    """Return a Sonnet coaching commentary on body composition + trajectory. Cached daily."""
    if not latest or not body_rows:
        return ""

    cache_key = f"body_analysis_v1_{date.today().isoformat()}"
    cached = get_cached_text(cache_key)
    if cached:
        return cached

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _rule_based_body(latest)

    prompt = _build_body_prompt(body_rows, latest, pmc_today, recent_metrics)
    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=MODEL_SMART,
            max_tokens=450,
            system=(
                "You are an experienced endurance coach and sports nutritionist reviewing an athlete's "
                "90-day body composition data alongside their training load. "
                "Be direct, specific, and reference actual numbers from the data. "
                "No bullet points or markdown headers — short paragraphs only. Address the athlete as 'you'."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
        set_cached_text(cache_key, text)
        return text
    except Exception:
        return _rule_based_body(latest)
