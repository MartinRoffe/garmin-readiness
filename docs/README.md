# User Guide

A complete walkthrough of the Garmin Readiness dashboard — what every tab shows,
how to read each chart, and what the buttons do. If you just want to install and
run the app, start with the [project README](../README.md); come here when you
want to understand and use it day to day.

> 📸 *Screenshot: the dashboard home page with the top navigation bar visible.*

## How this guide is organised

The pages below mirror the app's own navigation bar, so you can read top to
bottom or jump straight to the tab you're looking at.

| Page | Covers |
|------|--------|
| **[Getting Started](getting-started.md)** | Install, configure your `.env`, first run, connecting your Garmin account |
| **[Key Concepts](concepts.md)** | The mental model: readiness score, training load (CTL/ATL/TSB), the HRV traffic light, and why this app uses heart rate instead of power |
| **[Readiness](tabs/readiness.md)** | The home page — your daily readiness score, metric tiles, nutrition card, fatigue alerts, and session modulation |
| **[Performance](tabs/performance.md)** | The **Performance ▾** group: the Performance tab (training-load and trend charts) and the Analysis tab (per-workout AI review) |
| **[Plan](tabs/plan.md)** | The **Plan ▾** group: Calendar, Training, and Compliance |
| **[Health](tabs/health.md)** | The **Health ▾** group: Nutrition, Sleep, and Body |
| **[Events](tabs/events.md)** | The **Events ▾** group: the Tenerife camp and the Haute Route Alpes 2027 plan |
| **[Coach](tabs/coach.md)** | The AI coach chat — context, plan-change proposals, and memory |
| **[Email & Automation](email-and-automation.md)** | The daily email, the Monday briefing, scheduling, and the command-line flags |
| **[FAQ & Troubleshooting](faq.md)** | Missing data, sign-in problems, clearing stale AI text, and protecting the dashboard |

## The navigation bar at a glance

The bar across the top of every page has six items. Four of them are dropdown
groups — hover or tap to reveal the pages inside:

- **Readiness** — the home page (your single-page morning check-in).
- **Performance ▾** → Performance · Analysis
- **Plan ▾** → Calendar · Training · Compliance
- **Health ▾** → Nutrition · Sleep · Body
- **Events ▾** → Tenerife · Haute Route
- **Coach** — chat with your AI coach.

Wherever you see a small **ⓘ** next to a label, hover or tap it for a short
plain-language explanation of that metric.

## Reading the colours

The dashboard uses a consistent colour language throughout:

- 🟢 **Green** — good / on track / a deficit (for calories).
- 🟠 **Amber** — caution / ease off / a small surplus.
- 🔴 **Red** — warning / back right off / a large surplus.

So a red HRV traffic light and a red calorie balance mean opposite things in
training terms but follow the same rule: green is the comfortable end, red is the
"pay attention" end.

## Want a PDF?

The whole guide can be exported to a single PDF:

```bash
bash docs/build-pdf.sh   # produces docs/user-guide.pdf
```

This needs [`pandoc`](https://pandoc.org/) and a LaTeX engine installed
(`brew install pandoc basictex` on macOS). The Markdown files here are the
source of truth — the PDF is just a portable copy you can regenerate any time.
