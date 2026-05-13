from __future__ import annotations

import logging
import os
import sys
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
    z_score,
    LOWER_IS_BETTER,
    SCORED_FIELDS,
)
from .metrics import DailyMetrics, available_count, fetch_metrics

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
    return m


def main() -> None:
    # DOTENV_PATH lets launchd (which has no shell env) point to the right .env file
    _env_path = os.getenv("DOTENV_PATH")
    load_dotenv(_env_path if _env_path else None)
    logging.basicConfig(level=logging.WARNING)

    import argparse

    parser = argparse.ArgumentParser(description="Garmin → Daily Readiness")
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
        help="Start the web dashboard at http://127.0.0.1:8080",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for --serve (default: 8080)",
    )
    parser.add_argument(
        "--email",
        action="store_true",
        help="Fetch today's data and send the daily readiness email",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --email: print advice to stdout instead of sending",
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
        with console.status("Generating advice…"):
            run_report(m, dry_run=args.dry_run)
        return

    stats = baseline_stats(target)
    comp_z = composite_score(m, stats)

    _render_dashboard(m, stats, comp_z)


def _setup_schedule() -> None:
    import shutil
    import subprocess
    from pathlib import Path

    exe = shutil.which("garmin-readiness")
    if not exe:
        console.print("[red]garmin-readiness not found in PATH. Run 'pip install -e .' first.[/red]")
        sys.exit(1)

    label = "com.garmin-readiness.daily"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    env_file = Path.cwd() / ".env"

    plist_path.parent.mkdir(parents=True, exist_ok=True)

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
        <string>--email</string>
        <string>--fetch</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>DOTENV_PATH</key>
        <string>{env_file}</string>
    </dict>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>7</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{Path.home()}/.garmin_readiness/daily.log</string>
    <key>StandardErrorPath</key>
    <string>{Path.home()}/.garmin_readiness/daily.log</string>
</dict>
</plist>"""

    # ── Server plist ─────────────────────────────────────────────────────
    server_label = "com.garmin-readiness.server"
    server_plist_path = Path.home() / "Library" / "LaunchAgents" / f"{server_label}.plist"

    server_plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{server_label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
        <string>--serve</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>DOTENV_PATH</key>
        <string>{env_file}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{Path.home()}/.garmin_readiness/server.log</string>
    <key>StandardErrorPath</key>
    <string>{Path.home()}/.garmin_readiness/server.log</string>
</dict>
</plist>"""

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
        console.print(f"\n  Dashboard: [bold]http://127.0.0.1:8080[/bold]")
        console.print(f"  Email:     daily at 07:00 (or on wake)")
        console.print(f"  Logs:      ~/.garmin_readiness/server.log  /  daily.log")
        console.print(f"\n  Test email: [bold]garmin-readiness --email --dry-run[/bold]")


class _null_ctx:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


if __name__ == "__main__":
    main()
