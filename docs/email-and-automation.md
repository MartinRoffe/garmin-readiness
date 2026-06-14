# Email & Automation

Beyond the live dashboard, the app can email you each morning and run itself in
the background. This page covers the email, the weekly briefing, scheduling, and
the full set of command-line flags.

## The daily readiness email

Each morning the app can send an HTML email summarising your day:

- Your **readiness score** and the metrics behind it.
- The **planned workout** for the day (including any override you've applied).
- A **recent-activity summary**.
- Any **HIGH fatigue alerts**, pushed to the very top as a callout, followed by
  an HRV amber/red modulation note when relevant.

If you've set an Anthropic API key, the advice text is written by Claude;
otherwise it falls back to clear rule-based advice.

> 📸 *Screenshot: a daily readiness email in an inbox.*

Send one on demand:

```bash
garmin-readiness --email            # send now
garmin-readiness --email --dry-run  # preview in the terminal without sending
```

You can also trigger it from the **Send Email** action on the
[Readiness](tabs/readiness.md) page.

### Why the email sometimes waits

The email deliberately holds off until your overnight data (sleep score and
morning body battery) has synced from the watch. If that data isn't there yet,
the run exits and — under the scheduled setup below — retries about 30 minutes
later. This stops you getting an empty 7 a.m. email before your watch has synced.

## The weekly briefing

On Mondays the email leads with a short coach briefing — a summary of your
current form, the key session of the week, and an execution cue. It's generated
once per week and cached, so it's consistent all week.

## Running it in the background (macOS)

On macOS the app installs two `launchd` agents — a daily 7 a.m. email and an
always-on web server:

```bash
garmin-readiness --setup-schedule
```

After changing any code, restart the background server so it picks up the change:

```bash
launchctl kickstart -k "gui/$(id -u)/com.garmin-readiness.server"
```

> **Background runs need their own environment.** `launchd` starts without your
> shell's environment, so copy your `.env` to `~/.garmin_readiness/.env` as well —
> that's where the scheduled runs read your credentials from.

## All command-line flags

| Command | What it does |
|---|---|
| `garmin-readiness` | Fetch today's data and print a readiness report in the terminal |
| `garmin-readiness --serve` | Start the web dashboard at **http://127.0.0.1:8743** |
| `garmin-readiness --email [--dry-run]` | Send the daily email (or preview it) |
| `garmin-readiness --backfill 30` | Pull the last *N* days from Garmin to build your baseline |
| `garmin-readiness --workouts [--dry-run]` | Upload the plan's structured cycling workouts to Garmin Connect and schedule them on their dates |
| `garmin-readiness --setup-schedule` | Install the macOS launchd agents (daily email + server) |

### About `--workouts`

This builds structured cycling workouts for each distinct session type in the
plan, uploads them to Garmin Connect once, and schedules each on its planned
dates — so the workouts appear on your watch ready to follow. Use `--dry-run`
first to see what would be uploaded.
