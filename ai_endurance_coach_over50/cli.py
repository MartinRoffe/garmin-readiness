"""CLI entry point; dispatches to fetch, email, backfill, workout-upload, and server modes."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from datetime import date, timedelta
from typing import Optional

from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .client import get_api
from .display import FIELD_LABELS, fmt_value, readiness_label
from .history import (
    baseline_stats,
    composite_score,
    history_for_chart,
    load,
    save,
    save_activities,
    z_score,
    LOWER_IS_BETTER,
    SCORED_FIELDS,
)
from .metrics import DailyMetrics, available_count, fetch_metrics, fetch_activities

console = Console()




def _z_bar(z: Optional[float], width: int = 12) -> Text:
    """Compact text bar centred at 0, coloured by z-score."""
    if z is None:
        return Text("—", style="dim")
    clamped = max(-2.0, min(2.0, z))
    filled = int(abs(clamped) / 2.0 * (width // 2))
    bar = [" "] * width
    mid = width // 2
    if clamped >= 0:
        for i in range(mid, mid + filled):
            bar[i] = "█"
    else:
        for i in range(mid - filled, mid):
            bar[i] = "█"

    colour = "green" if z >= 0.5 else ("red" if z <= -0.5 else "yellow")
    sign = "+" if z >= 0 else ""
    label = f"{sign}{z:.2f}σ"
    t = Text("".join(bar), style=colour)
    t.append(f" {label}", style=colour)
    return t


def _readiness_label_rich(z: Optional[float]) -> tuple[str, str]:
    """Returns (label, Rich colour) for the composite z-score."""
    if z is None:
        return "Building baseline…", "dim"
    if z >= 1.0:
        return "ABOVE AVERAGE", "bold green"
    if z >= 0.25:
        return "GOOD", "green"
    if z >= -0.25:
        return "AVERAGE", "yellow"
    if z >= -1.0:
        return "BELOW AVERAGE", "red"
    return "LOW", "bold red"


def _sparkline(points: list[Optional[float]], width: int = 20) -> Text:
    bars = " ▁▂▃▄▅▆▇█"
    values = [p for p in points if p is not None]
    if not values:
        return Text("no data", style="dim")
    lo, hi = min(values), max(values)
    span = hi - lo or 1

    result = Text()
    for p in points[-width:]:
        if p is None:
            result.append("·", style="dim")
        else:
            idx = int((p - lo) / span * (len(bars) - 1))
            colour = "green" if p >= 0.25 else ("red" if p <= -0.25 else "yellow")
            result.append(bars[idx], style=colour)
    return result


def _render_dashboard(m: DailyMetrics, stats: dict, comp_z: Optional[float]) -> None:
    target = m.date

    # ── Header ──────────────────────────────────────────────────────────────
    label, colour = _readiness_label_rich(comp_z)
    tags = []
    if m.hrv_status:
        tags.append(f"HRV {m.hrv_status.title()}")
    if m.training_status_label:
        tags.append(f"Training {m.training_status_label}")
    if m.acwr is not None:
        acwr_tag = f"ACWR {m.acwr:.2f}"
        if m.acwr_status:
            acwr_tag += f" ({m.acwr_status.replace('_', ' ').lower()})"
        tags.append(acwr_tag)
    header = Text()
    header.append(f"Daily Readiness  {target.strftime('%a %d %b %Y')}", style="bold")
    if comp_z is not None:
        header.append(f"\nComposite: ", style="dim")
        header.append(f"{comp_z:+.2f}σ  {label}", style=colour)
    else:
        header.append(f"\n{label}", style=colour)
    if tags:
        header.append(f"\n{' · '.join(tags)}", style="dim")

    console.print()
    console.print(Panel(header, box=box.ROUNDED, expand=False))

    # ── Metrics table ────────────────────────────────────────────────────────
    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
    table.add_column("Metric", style="", min_width=22)
    table.add_column("Today", justify="right", min_width=10)
    table.add_column("30d Avg", justify="right", min_width=10)
    table.add_column("Deviation", min_width=18)

    for field, (label_str, unit) in FIELD_LABELS.items():
        value = getattr(m, field)
        val_str = fmt_value(field, value) + (unit if value is not None else "")

        # ACWR: append Garmin's own status badge next to the value
        if field == "acwr" and m.acwr_status and value is not None:
            badge = m.acwr_status.replace("_", " ").title()
            val_str = f"{val_str}  [{badge}]"

        if field in stats:
            mean, std = stats[field]
            avg_str = fmt_value(field, mean) + unit
            z = z_score(value, mean, std, field) if value is not None else None
            bar = _z_bar(z)
        elif field in ("training_load_chronic", "vo2_max") and value is not None:
            # Context-only fields: show value but no deviation scored
            avg_str = "—"
            bar = Text("(not scored)", style="dim")
        else:
            avg_str = "—"
            bar = Text("—", style="dim")

        table.add_row(label_str, val_str, avg_str, bar)

    console.print(table)

    # ── Trend sparkline ──────────────────────────────────────────────────────
    history = history_for_chart(days=14)
    spark_vals = [v for _, v in history]
    spark = _sparkline(spark_vals)
    console.print(f"  14-day trend  ", end="")
    console.print(spark)

    n_metrics = len(stats)
    status = "building — need more history" if not stats else f"{n_metrics} metrics tracked"
    console.print(f"\n  [dim]Baseline: {status} (30-day rolling window)[/dim]")
    console.print()


def _load_or_fetch(target: date, api=None, force: bool = False) -> DailyMetrics:
    if not force:
        cached = load(target)
        if cached is not None and available_count(cached) > 0:
            return cached

    if api is None:
        raise RuntimeError("API client required to fetch data")

    m = fetch_metrics(api, target)
    save(m)
    try:
        acts = fetch_activities(api, days=7)
        save_activities(acts)
    except Exception:
        pass
    return m


def main() -> None:
    # DOTENV_PATH lets launchd (which has no shell env) point to the right .env file
    _env_path = os.getenv("DOTENV_PATH")
    if _env_path:
        load_dotenv(_env_path)
    else:
        load_dotenv()  # tries CWD/.env first
        _fallback = Path.home() / ".ai_endurance_coach_over50" / ".env"
        if _fallback.exists():
            load_dotenv(_fallback, override=False)
    logging.basicConfig(level=logging.WARNING)

    import argparse

    parser = argparse.ArgumentParser(description="AI Endurance Coach (50+) — Garmin-powered readiness & coaching")
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Target date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Force re-fetch from Garmin Connect even if cached",
    )
    parser.add_argument(
        "--backfill",
        type=int,
        metavar="DAYS",
        help="Fetch and store the last N days to build a baseline",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show debug logs",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start the web dashboard at http://127.0.0.1:8743",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8743,
        help="Port for --serve (default: 8743)",
    )
    parser.add_argument(
        "--email",
        action="store_true",
        help="Fetch today's data and send the daily readiness email",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --email or --workouts: print what would be done without sending/uploading",
    )
    parser.add_argument(
        "--workouts",
        action="store_true",
        help="Upload training plan bike workouts to Garmin Connect and schedule them "
             "(applies any coach plan overrides; use --dry-run to preview)",
    )
    parser.add_argument(
        "--withings-sync",
        action="store_true",
        help="Push recent Withings measurements to Garmin Connect before fetching",
    )
    parser.add_argument(
        "--setup-schedule",
        action="store_true",
        help="Install a launchd job to email daily at 7am",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, force=True)

    if args.setup_schedule:
        _setup_schedule()
        return

    if args.serve:
        from .server import run as serve_run
        console.print(f"[bold]Dashboard at[/bold] http://127.0.0.1:{args.port}")
        serve_run(port=args.port)
        return

    if args.workouts:
        from .workouts import upload_and_schedule
        email_addr = os.getenv("GARMIN_EMAIL")
        password_val = os.getenv("GARMIN_PASSWORD")
        if not email_addr or not password_val:
            console.print("[red]GARMIN_EMAIL and GARMIN_PASSWORD must be set[/red]")
            sys.exit(1)
        if args.dry_run:
            from .workouts import upload_and_schedule
            upload_and_schedule(None, dry_run=True)
        else:
            api = get_api(email_addr, password_val)
            upload_and_schedule(api, dry_run=False)
        return

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password:
        console.print(
            "[red]GARMIN_EMAIL and GARMIN_PASSWORD must be set "
            "(copy .env.example → .env)[/red]"
        )
        sys.exit(1)

    target = date.fromisoformat(args.date)

    # Backfill mode: fetch historical days to prime the baseline
    if args.backfill:
        console.print(f"[bold]Backfilling {args.backfill} days…[/bold]")
        api = get_api(email, password)
        for i in range(args.backfill, 0, -1):
            d = date.today() - timedelta(days=i)
            cached = load(d)
            if cached and available_count(cached) > 0 and not args.fetch:
                console.print(f"  {d.isoformat()}  [dim]cached[/dim]")
                continue
            console.print(f"  {d.isoformat()}  fetching…", end="")
            try:
                m = fetch_metrics(api, d)
                save(m)
                n = available_count(m)
                console.print(f"  [green]{n} metrics[/green]")
            except Exception as e:
                console.print(f"  [red]error: {e}[/red]")
        console.print("[green]Backfill complete.[/green]\n")
        if not args.fetch and target == date.today():
            # Also fetch today after backfill
            pass

    # Push Withings data to Garmin and save directly to SQLite
    if args.withings_sync:
        from .withings import sync_withings_to_garmin
        _w_api = get_api(email, password)
        with console.status("Syncing Withings → Garmin…"):
            _synced = sync_withings_to_garmin(_w_api)
        if _synced:
            console.print("[green]Withings data synced.[/green]")
        else:
            console.print("[dim]No new Withings data to sync.[/dim]")

    # Fetch / load today's metrics
    needs_api = args.fetch or load(target) is None
    api = get_api(email, password) if needs_api else None

    with console.status("Fetching metrics…") if needs_api else _null_ctx():
        m = _load_or_fetch(target, api=api, force=args.fetch)

    if available_count(m) == 0:
        console.print(
            f"[yellow]No metrics available for {target}. "
            "Sync your watch and try again.[/yellow]"
        )
        sys.exit(0)

    if args.email:
        from .report import run_report

        sentinel = Path.home() / ".ai_endurance_coach_over50" / f"sent_{target.isoformat()}"
        if sentinel.exists() and not args.dry_run:
            console.print(f"[dim]Email already sent for {target}, skipping.[/dim]")
            sys.exit(0)

        # Require sleep end + body battery — ensures overnight sleep was recorded and
        # body battery reflects the post-sleep reading, not a mid-sleep partial value
        _missing = []
        if getattr(m, "sleep_end_ts", None) is None:
            _missing.append("sleep_end (sleep not yet recorded)")
        if getattr(m, "body_battery_morning", None) is None:
            _missing.append("body_battery_morning")
        if _missing and not args.dry_run:
            console.print(
                f"[yellow]Watch hasn't synced yet — missing: {', '.join(_missing)}. "
                "Will retry later.[/yellow]"
            )
            sys.exit(2)

        # Touch the sentinel BEFORE sending so a crash after SMTP delivery can't
        # cause a duplicate email on the launchd retry. On a clean failure the
        # sentinel is removed again so the retry loop still works.
        if not args.dry_run:
            sentinel.touch()
        try:
            with console.status("Generating advice…"):
                run_report(m, dry_run=args.dry_run)
        except Exception:
            if not args.dry_run:
                sentinel.unlink(missing_ok=True)
            raise
        return

    stats = baseline_stats(target)
    comp_z = composite_score(m, stats)

    _render_dashboard(m, stats, comp_z)


def _setup_schedule() -> None:
    import shutil
    import subprocess
    from pathlib import Path

    python = sys.executable
    project_dir = str(Path(__file__).parent.parent)
    # Use ~/.ai_endurance_coach_over50/.env — accessible to launchd without Full Disk Access
    env_file = Path.home() / ".ai_endurance_coach_over50" / ".env"
    src_env = Path.cwd() / ".env"
    if not env_file.exists() and src_env.exists():
        import shutil as _shutil
        _shutil.copy2(src_env, env_file)
        env_file.chmod(0o600)
    scripts_dir = Path.home() / ".ai_endurance_coach_over50"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    # Write wrapper scripts — more reliable than EnvironmentVariables in plists
    serve_script = scripts_dir / "serve.sh"
    email_script = scripts_dir / "email.sh"

    serve_script.write_text(
        f"#!/bin/bash\n"
        f"export PYTHONPATH={project_dir}\n"
        f"export DOTENV_PATH={env_file}\n"
        f"exec {python} -m ai_endurance_coach_over50.cli --serve\n"
    )
    email_script.write_text(
        f"#!/bin/bash\n"
        f"export PYTHONPATH={project_dir}\n"
        f"export DOTENV_PATH={env_file}\n"
        f"# Retry every 30 min until 10:00 if watch hasn't synced yet (exit 2 = not ready)\n"
        f"DEADLINE=$(date -v+3H +%s 2>/dev/null || date --date='+3 hours' +%s)\n"
        f"while true; do\n"
        f"    {python} -m ai_endurance_coach_over50.cli --withings-sync --email --fetch\n"
        f"    CODE=$?\n"
        f"    [ $CODE -eq 0 ] && exit 0\n"
        f"    [ $CODE -ne 2 ] && exit $CODE\n"
        f"    [ $(date +%s) -ge $DEADLINE ] && {{\n"
        f"        echo 'Watch never synced by 10:00 — giving up'\n"
        f"        exit 2\n"
        f"    }}\n"
        f"    echo 'Watch not synced yet, retrying in 30 min…'\n"
        f"    sleep 1800\n"
        f"done\n"
    )
    serve_script.chmod(0o755)
    email_script.chmod(0o755)

    label = "com.ai-endurance-coach-over50.daily"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    def _plist(label: str, script: Path, extra_keys: str = "") -> str:
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>{script}</string>
    </array>
    {extra_keys}
    <key>StandardOutPath</key>
    <string>{Path.home()}/.ai_endurance_coach_over50/{label.split(".")[-1]}.log</string>
    <key>StandardErrorPath</key>
    <string>{Path.home()}/.ai_endurance_coach_over50/{label.split(".")[-1]}.log</string>
</dict>
</plist>"""

    plist = _plist(
        label,
        email_script,
        """<key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>8</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>""",
    )

    # ── Server plist ─────────────────────────────────────────────────────
    server_label = "com.ai-endurance-coach-over50.server"
    server_plist_path = Path.home() / "Library" / "LaunchAgents" / f"{server_label}.plist"

    server_plist = _plist(
        server_label,
        serve_script,
        """<key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>""",
    )

    server_plist_path.write_text(server_plist)
    plist_path.write_text(plist)

    # ── Load both ─────────────────────────────────────────────────────────
    ok = True
    for p, name in [(server_plist_path, "server"), (plist_path, "daily email")]:
        subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
        r = subprocess.run(["launchctl", "load", str(p)], capture_output=True, text=True)
        if r.returncode == 0:
            console.print(f"[green]✓[/green] {name}")
        else:
            console.print(f"[red]✗ {name}:[/red] {r.stderr.strip()}")
            ok = False

    if ok:
        console.print(f"\n  Dashboard: [bold]http://127.0.0.1:8743[/bold]")
        console.print(f"  Email:     daily at 07:00 (or on wake)")
        console.print(f"  Logs:      ~/.ai_endurance_coach_over50/server.log  /  daily.log")
        console.print(f"\n  Test email: [bold]endurance-coach --email --dry-run[/bold]")


class _null_ctx:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


if __name__ == "__main__":
    main()
