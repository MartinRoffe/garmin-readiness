# Performance & Analysis

**Nav:** the **Performance ▾** group → **Performance** and **Analysis**.

These two tabs are about the longer view: how your fitness and fatigue are
trending, and what each individual workout tells you.

---

## Performance tab

**Route:** `/performance`

### What it's for

The big-picture training-load and trend view — are you getting fitter, are you
overcooked, and will you arrive at your event both fit and fresh?

> 📸 *Screenshot: the Performance tab showing the CTL/ATL/TSB chart with the projection overlay.*

### What you'll see

- **The PMC chart (CTL / ATL / TSB).** Your fitness, fatigue, and form over time.
  See [the concept](../concepts.md#training-load-ctl-atl-and-tsb-the-pmc). A
  **projection** continues the lines to your event date using your planned
  sessions, drawn as a dashed amber overlay with a vertical line marking the
  event.
- **Taper scenarios table.** Three pre-computed "what-ifs" over the final two
  weeks — *as planned*, *drop one quality session*, and *halve final-week volume*
  — so you can see how each choice changes the form (TSB) you'll bring to the
  start line. The third scenario is also overlaid on the chart as a blue dashed
  line.
- **Zone 2 cardiac-drift trend.** A scatter of your easy rides only, with a
  best-fit line showing whether your heart rate is drifting up or settling over
  time at the same easy effort — a key aerobic-fitness signal for an HR-based
  athlete.
- **Training polarisation charts.** A stacked bar of time in each zone (Z1–Z5)
  per week, plus a donut of the totals for the block — a check on whether your
  intensity distribution is as polarised as intended.
- **Estimated FTP / W-kg.** A dual-axis trend of your estimated functional
  threshold and watts-per-kilo, derived from VO₂max and weight.
- **Durability drift chart.** For long rides, how much your heart rate drifts in
  the final third versus the first third — a measure of how well your fitness
  holds up late in a ride.
- **Foster monotony / strain chart.** Weekly training monotony and strain, which
  feed the high-monotony fatigue alert.
- **Heat / altitude acclimation tile.** Your current acclimation percentages,
  when Garmin reports them.

### Good to know

- **Read these as trends, not absolutes.** The FTP/W-kg numbers are a
  [VO₂max-based estimate](../concepts.md#why-heart-rate-not-power), and the TSB
  values use Garmin load units, so they won't match power-meter software. The app
  shows a caveat note on the tab to this effect.

---

## Analysis tab

**Route:** `/analysis`

### What it's for

A per-workout review: for each recent activity, an AI coach reads your
heart-rate zone breakdown and writes a short, discipline-aware assessment.

> 📸 *Screenshot: an Analysis card showing HR zones and the AI commentary, with the RPE emoji row.*

### What you'll see

- **One card per activity**, each with the heart-rate zone distribution and a
  Claude-written commentary tailored to the type of session (a long ride reads
  differently from intervals).
- **Compound sessions** (e.g. a kettlebell + stair-climber day) are collapsed
  into a single card showing both halves side by side.

### How to use it

- **Log your RPE.** Each card has a row of effort emoji — 😴 😊 😤 🔥 💀 — to record
  how hard the session *felt* (rate of perceived exertion). This is saved and fed
  to the AI coach so its advice accounts for how you actually experienced the
  work, not just the numbers.
- **Log your fuelling.** For endurance rides you can record how your in-ride
  fuelling went (planned vs actual carbs per hour, whether fluids were on track,
  and a note). The coach uses this too.
- **Regenerate an analysis.** Use the **Refresh** action to pull and analyse any
  activities that haven't been processed yet.

### Good to know

- **Analysis needs the activity to have synced** to Garmin first, and needs your
  Anthropic API key set. Without the key, this tab stays empty.
- Some sessions (FTP tests) **auto-populate your fitness-test history** when
  analysed, which is what keeps the [Readiness](readiness.md) FTP-retest card and
  the Performance FTP trend up to date.
