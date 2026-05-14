from __future__ import annotations

import os
import smtplib
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import anthropic

from .display import FIELD_LABELS, fmt_value, readiness_label, enrich_activity
from .plan import session_for_date
from .history import (
    LOWER_IS_BETTER,
    baseline_stats,
    composite_score,
    load_recent_activities,
    seven_day_composite_trend_csv,
    z_score,
)
from .metrics import DailyMetrics

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

    session = session_for_date(target)
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
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _rule_based_advice(m, stats, comp_z)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = _build_prompt(m, stats, comp_z)

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
            system=(
                "You are a knowledgeable fitness coach who helps athletes decide whether to train or rest "
                "based on objective physiological data from a Garmin device. "
                "Be direct, warm, and evidence-based. Never be vague."
            ),
        )
        return message.content[0].text
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
    session = session_for_date(d)
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

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(from_addr, app_password)
        server.sendmail(from_addr, to_addr, msg.as_string())


def run_report(m: DailyMetrics, dry_run: bool = False) -> None:
    """Generate and send (or print) the daily readiness email."""
    stats = baseline_stats(m.date)
    comp_z = composite_score(m, stats)
    label, _ = readiness_label(comp_z)

    activities = load_recent_activities(days=7)
    advice = generate_advice(m, stats, comp_z)
    score_str = f"{comp_z:+.2f}σ" if comp_z is not None else "—"
    subject = f"Readiness {m.date.strftime('%-d %b')} · {score_str} {label}"
    html = build_html(m, stats, comp_z, advice, activities)

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

def _build_pmc_prompt(history: list[dict]) -> str:
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

    lines += ["", "Upcoming 7 days (planned sessions):"]
    for i in range(7):
        d = today + timedelta(days=i)
        session = session_for_date(d)
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
        "",
        "Please provide a concise training-load analysis covering:",
        "1. Current form: is TSB in a sustainable zone or showing signs of overreaching?",
        "2. Fitness trajectory: is CTL building as expected?",
        "3. One specific recommendation for the next 7 days given the planned sessions.",
        "Keep it under 120 words. Plain paragraphs, no headers or bullets. Address the athlete as 'you'.",
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


def generate_pmc_analysis(history: list[dict]) -> str:
    """Return a short Claude Haiku commentary on the current PMC state."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _rule_based_pmc(history)

    prompt = _build_pmc_prompt(history)
    if not prompt:
        return _rule_based_pmc(history)

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=(
                "You are an experienced cycling coach reviewing an athlete's training load data. "
                "Be direct, specific, and evidence-based. Reference the actual numbers. "
                "No bullet markdown — short paragraphs only. Address the athlete as 'you'."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
    except Exception:
        return _rule_based_pmc(history)
