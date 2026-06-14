"""Nutrition plan data — 4-week repeatable cycle.

Encoded here so the AI coach can reference the exact prescribed meals for
today rather than only seeing what was logged after the fact.

Protein targets are anchored to the athlete's measured bodyweight (carried
from the most recent body_metrics reading via `current_weight_kg()`), not a
hardcoded figure — the Block-A weight-loss reset means weight is falling over
the plan and a static number would drift out of date.
"""
from __future__ import annotations

from datetime import date

# Fallback used only before any body_metrics reading exists. The athlete is
# returning from a sedentary decade with high body fat; targets recalibrate
# automatically once a real weigh-in is logged.
_FALLBACK_WEIGHT_KG = 92.0


def current_weight_kg() -> float:
    """Latest measured bodyweight, falling back to `_FALLBACK_WEIGHT_KG`."""
    try:
        from .history import latest_weight_kg
        w = latest_weight_kg()
        return w if w else _FALLBACK_WEIGHT_KG
    except Exception:
        return _FALLBACK_WEIGHT_KG


# Protein is prescribed on a FAT-FREE-MASS basis, not total bodyweight: at high
# body fat, g/kg of total weight over-prescribes. 2.2–2.6 g/kg of lean mass is
# the evidence-based range to preserve muscle in a deficit, and the upper end
# suits a 50+ athlete (anabolic resistance). Falls back to 1.8 g/kg of total
# weight when no body-fat reading exists to derive lean mass.
_PROTEIN_G_PER_KG_LEAN_LOW = 2.2
_PROTEIN_G_PER_KG_LEAN_HIGH = 2.6


def protein_target_g() -> dict:
    """Daily protein floor/ceiling in grams, on a fat-free-mass basis.

    Returns {"low", "high", "basis", "lean_kg"}; `basis` documents how it was
    derived so the coach text can be honest about the input.
    """
    try:
        from .history import latest_lean_mass_kg
        lean = latest_lean_mass_kg()
    except Exception:
        lean = None
    if lean:
        return {
            "low": round(lean * _PROTEIN_G_PER_KG_LEAN_LOW),
            "high": round(lean * _PROTEIN_G_PER_KG_LEAN_HIGH),
            "basis": f"{_PROTEIN_G_PER_KG_LEAN_LOW:g}–{_PROTEIN_G_PER_KG_LEAN_HIGH:g} g/kg of fat-free mass, ~{lean:.0f} kg",
            "lean_kg": round(lean, 1),
        }
    # No body-fat reading — fall back to total-weight estimate.
    w = current_weight_kg()
    return {
        "low": round(w * 1.8),
        "high": round(w * 2.0),
        "basis": f"1.8–2.0 g/kg of bodyweight, ~{w:.0f} kg — no body-fat reading to derive lean mass",
        "lean_kg": None,
    }

# ── Calorie tiers ─────────────────────────────────────────────────────────────
CALORIE_TIERS = {
    "rest":     {"label": "Rest / Monday",            "kcal": 2050},
    "training": {"label": "Training days (Tue–Fri)",  "kcal": 2350},
    "ruck":     {"label": "Ruck Saturday",             "kcal": 2500},
    "long":     {"label": "Long ride Sunday",          "kcal": 2700,
                 "note": "baseline ~2 h; +150–200 kcal per extra hour beyond 2 h"},
    "recovery": {"label": "Recovery week Mon–Fri",     "kcal": 2050},
}

# ── Principles ────────────────────────────────────────────────────────────────
PRINCIPLES = [
    "Energy strategy (Block A — weight-loss reset): a moderate, LEAN-MASS-SPARING deficit. "
    "Take the deficit from rest and recovery days; keep the long-ride day close to energy "
    "balance so the key endurance session is never under-fuelled. The deficit is safe here — "
    "ample fat reserves mean low under-fuelling risk — but protein and recovery are protected.",
    "Protein target: lean-mass based (≈2.2 g/kg of fat-free mass), held high to preserve muscle "
    "in the deficit. Every meal anchored to a protein source. GetPro at lunch on Mon/Tue/Wed/Thu, "
    "protein shake on Wed/Thu/Sat. Add a pre-sleep casein/dairy dose (~40 g) on training days.",
    "Carbs around training: rice/pasta lunches on ride and KB days. Banana 45 min pre-session. "
    "Ben's Paella Thursday fuels evening kettlebell. Saturday's carb-rich Gousto dinner is the "
    "real pre-ride fuel for Sunday — not breakfast.",
    "Sunday fuelling: 100–150 kcal fast carbs on waking only. On-bike from minute 0: "
    "60 g carbs/hr for rides 1–2.5 h, 75–90 g/hr beyond 2.5 h. Recovery meal within 45 min "
    "(chocolate milk first, then big porridge + GetPro + eggs + banana + PB toast).",
    "Gut training (start early — the gut is trainable and Block B / the alpine event will demand "
    "90+ g/hr): weeks 1–4 practise 60 g/hr on every ride ≥75 min; weeks 5–8 push long rides to "
    "70–80 g/hr; weeks 9+ rehearse 90 g/hr on the long ride (1:0.8 glucose:fructose to raise the "
    "absorption ceiling). The long ride is FUELLED even on the weight-loss block — the calorie "
    "deficit comes from rest/recovery days, never from under-fuelling the key endurance session.",
    "Recovery week (W4): protein holds at 185g minimum — cut comes from carbs/fat only. "
    "Snacks reduce to 1–2/day. Lighter Gousto (<550 kcal). Protein shake stays in.",
    "Thursday warning: lowest pre-dinner protein day (119g). "
    "Gousto MUST be chicken/beef/salmon ≥65g protein — not a pasta-only dish.",
    "Saturday is the highest-risk protein day. Lunch must be chicken+rice (not eggs on toast). "
    "GetPro at post-ruck breakfast. Protein shake as evening snack. These are non-negotiable.",
]

# ── Supplements (evidence-based for 50+; discuss with GP before starting) ─────
# Framed as coach guidance, NOT prescriptions. Each entry: (name, dose, why).
SUPPLEMENTS: list[tuple[str, str, str]] = [
    ("Creatine monohydrate", "3–5 g/day, every day",
     "Best-evidenced supplement for a masters athlete: helps retain lean mass and strength in a "
     "calorie deficit, supports high-intensity work and recovery, and may aid cognition. No loading "
     "needed; take any time of day."),
    ("Vitamin D3", "Per GP / blood test (often 1000–2000 IU/day in winter)",
     "Supports bone density (important for a cyclist — low impact), immune function and muscle. "
     "UK sun is insufficient Oct–Mar; dose to a measured blood level rather than guessing."),
    ("Omega-3 (EPA/DHA)", "~1–2 g combined EPA+DHA/day",
     "Anti-inflammatory; may blunt training soreness and support cardiovascular and joint health. "
     "Oily fish 2–3×/week is an alternative to a capsule."),
]

_SUPPLEMENT_DISCLAIMER = (
    "Guidance only, not medical advice — confirm doses and suitability with your GP, "
    "especially alongside any medication or blood-pressure management."
)

# ── Day-type templates ────────────────────────────────────────────────────────
# Each meal: (slot, name, detail, kcal, protein_g, carbs_g)
DAY_TYPES: dict[str, dict] = {

    "rest": {
        "label": "Rest day",
        "calorie_tier": "rest",
        "pre_dinner_protein_g": 120,
        "protein_note": "120g pre-dinner; Gousto must deliver 60g+ to hit 185g. Easiest day.",
        "meals": [
            ("Breakfast", "Fuel10k + GetPro Yoghurt",
             "Grab-and-go. Shake Fuel10k, eat GetPro on the way in.", 330, 35, 39),
            ("AM Snack", "Nature Valley Protein Bar", "Mid-morning 10g protein hit.", 200, 10, 15),
            ("Lunch", "Asda Egg Fried Rice + Chicken 240g + GetPro",
             "Microwave rice. Chicken cold from pack. GetPro alongside.", 680, 73, 70),
            ("PM Snack", "Banana", "Afternoon energy.", 90, 1, 23),
            ("Eve Snack", "Apple or orange", "Light evening snack.", 70, 1, 18),
            ("Dinner", "Gousto — 60g+ protein pick",
             "~660 kcal, 60g+ protein. Rest day — any strong Gousto (chicken, beef, salmon). "
             "Veggie or pasta-only dishes don't hit target.", 660, 60, 55),
        ],
    },

    "training": {
        "label": "Kettlebell + MaxiClimber",
        "calorie_tier": "training",
        "pre_dinner_protein_g": 120,
        "protein_note": "120g pre-dinner (GetPro now added to lunch). Gousto at 65g closes the day at 185g.",
        "meals": [
            ("Breakfast", "Fuel10k + GetPro Yoghurt", "Same morning routine.", 330, 35, 39),
            ("AM Snack", "Nature Valley Protein Bar", "Mid-morning.", 200, 10, 15),
            ("Lunch", "Asda Egg Fried Rice + Chicken 240g + GetPro",
             "Full 240g chicken pack + GetPro alongside. Same as rest day lunch — protein floor "
             "needs the GetPro on training days too.", 700, 73, 70),
            ("PM Snack", "Banana", "45 min before evening session.", 90, 1, 23),
            ("Eve Snack", "Apple or orange", "Post-session.", 70, 1, 18),
            ("Dinner", "Gousto — 65g protein pick",
             "~730 kcal, 65g protein. Chicken thighs, beef ragu or salmon. Training day — "
             "recovery depends on this meal.", 730, 65, 70),
        ],
    },

    "bike": {
        "label": "Outdoor Bike 60 min",
        "calorie_tier": "training",
        "pre_dinner_protein_g": 125,
        "protein_note": "125g pre-dinner (includes protein shake). Gousto at 65g closes the day at 190g.",
        "meals": [
            ("Breakfast", "Fuel10k + GetPro Yoghurt", "Same morning routine.", 330, 35, 39),
            ("AM Snack", "Nature Valley Protein Bar", "Mid-morning.", 200, 10, 15),
            ("Lunch", "Batchelors Pasta & Sauce + Tuna tin + GetPro",
             "Stir full tin of tuna into pasta. GetPro alongside. Good carbs for the afternoon ride.", 600, 53, 78),
            ("PM Snack", "Banana", "45 min before riding.", 90, 1, 23),
            ("Protein Shake", "Whey/casein shake — 25g protein",
             "Post-ride or between PM snack and dinner. Closes the protein gap on ride days "
             "where lunch is tuna/pasta rather than the higher-protein chicken lunch.", 150, 25, 5),
            ("Eve Snack", "Apple or orange", "Post-ride.", 70, 1, 18),
            ("Dinner", "Gousto — 65g protein pick",
             "~730 kcal, 65g protein. Pasta, rice or noodle Gousto works on ride evenings "
             "if the protein source is chicken, fish or beef.", 730, 65, 72),
        ],
    },

    "thursday": {
        "label": "Kettlebell + MaxiClimber — Paella Thursday",
        "calorie_tier": "training",
        "pre_dinner_protein_g": 119,
        "protein_note": "⚠ 119g pre-dinner — lowest pre-dinner day. Gousto MUST be ≥65g protein. No exceptions.",
        "meals": [
            ("Breakfast", "Fuel10k + GetPro Yoghurt", "Same morning routine.", 330, 35, 39),
            ("AM Snack", "Nature Valley Protein Bar", "Mid-morning.", 200, 10, 15),
            ("Lunch", "Ben's Paella ★ + Prawns 150g + GetPro",
             "Paella Thursday. Prawns stirred in cold. GetPro alongside. Excellent carb base "
             "for evening kettlebell.", 480, 47, 62),
            ("PM Snack", "Banana", "45 min before session. Carbs matter tonight.", 90, 1, 23),
            ("Protein Shake", "Whey/casein shake — 25g protein",
             "Between session and dinner — closes the protein gap left by the lower-protein "
             "paella lunch. Non-negotiable on Thursdays.", 150, 25, 5),
            ("Eve Snack", "Apple or orange", "Post-session.", 70, 1, 18),
            ("Dinner", "Gousto — strongest pick of the week, ≥65g protein",
             "~730 kcal, 65g protein. Lowest pre-dinner protein day — Gousto must deliver. "
             "Chicken, beef or salmon. NOT a pasta-only or veggie dish.", 730, 65, 68),
        ],
    },

    "ruck": {
        "label": "Rucking 60–90 min",
        "calorie_tier": "ruck",
        "pre_dinner_protein_g": 137,
        "protein_note": "Saturday was the biggest protein gap (old plan: 97g, need 197g). "
                        "Chicken lunch + GetPro at breakfast + shake are all required to hit target.",
        "meals": [
            ("On Waking", "Banana out the door (or fasted)",
             "Very early start — fasted at easy pace is fine. Banana if you want fuel.", 150, 3, 30),
            ("Post-Ruck", "Porridge 80g + Milk + 2 Eggs + Banana + GetPro Yoghurt",
             "On return — the recovery meal. 80g oats, semi-skimmed milk, 2 eggs, banana, "
             "GetPro pot. GetPro is essential here — without it Saturday protein collapses.", 680, 37, 72),
            ("Lunch", "Chicken 240g + Rice Pouch + Salad",
             "Full 240g chicken pack — NOT eggs on toast (22g vs 58g protein). "
             "This single swap adds 36g protein to Saturday. Non-negotiable.", 550, 58, 46),
            ("PM Snack", "Banana + Apple + Nature Valley Protein Bar",
             "Afternoon. Add the protein bar on Saturday to keep the total moving.", 360, 12, 56),
            ("Protein Shake", "Whey/casein shake — 25g protein",
             "Evening, before dinner. Saturday's structure makes it hard to hit 185g without "
             "a shake — treat this as a planned meal, not optional.", 150, 25, 5),
            ("Dinner", "Gousto — carb-rich pick (this is tomorrow's pre-ride meal)",
             "~730 kcal, 62g protein. Bias to a carb-rich Gousto tonight — butter chicken with "
             "rice, pasta, noodles, slow-cooked lamb. This dinner fuels Sunday's ride.", 730, 62, 82),
        ],
    },

    "long": {
        "label": "Long Ride — building to 3h30",
        "calorie_tier": "long",
        "pre_dinner_protein_g": 126,
        "protein_note": "126g pre-dinner (GetPro now in recovery meal + 240g chicken at lunch). "
                        "Gousto at 65g closes the day at ~190g.",
        "meals": [
            ("On Waking", "Banana + honey toast, or carb drink in bottle 1",
             "100–150 kcal fast carbs only, 10–20 min before rolling. Last night's Gousto dinner "
             "was the real pre-ride meal.", 160, 3, 32),
            ("On-Bike", "Carb drink mix + banana + Crunchy bars (from minute 0)",
             "60 g carbs/hr for 1–2.5 h, 75–90 g/hr beyond 2.5 h. Start in first 15 min, "
             "something every 20–30 min. 500 ml bottle with 40–60 g carb drink, banana (~25 g), "
             "Crunchy bar (~28 g). 500–750 ml fluid/hr.", 450, 6, 104),
            ("Recovery", "Big Porridge + GetPro + 2 Eggs + Banana + PB Toast + Chocolate Milk",
             "Within 45 min of finishing. Chocolate milk first, then 80g oats + GetPro + "
             "2 eggs + banana + PB toast. GetPro is added here — Sunday recovery meal was "
             "40g protein before, now 55g.", 850, 55, 82),
            ("Lunch", "Chicken 240g + Rice Pouch + Salad",
             "A few hours after the ride. Increased from 150g to 240g chicken — adds 10g protein "
             "and improves afternoon recovery before the next training day.", 550, 58, 56),
            ("Dinner", "Gousto — 65g protein recovery pick",
             "~700 kcal, 65g protein. Slow-cooked beef ragu, Thai chicken, lemon herb salmon. "
             "Strong protein close to a big ride day.", 700, 65, 64),
        ],
    },

    "recovery_weekday": {
        "label": "Recovery week Mon–Fri",
        "calorie_tier": "recovery",
        "pre_dinner_protein_g": 115,
        "protein_note": "Recovery week: protein holds at 185g minimum — cut from carbs/fat only. "
                        "Protein shake stays in even on recovery week.",
        "meals": [
            ("Breakfast", "Fuel10k + GetPro Yoghurt (same)", "Keep breakfast identical.", 330, 35, 39),
            ("Snack", "Protein bar on training days, skip on rest days",
             "Keep protein snacks. Drop fruit snacks on rest days. Bar on training days only.", 200, 10, 15),
            ("Lunch", "Same lunch choices — no change",
             "Lunch stays identical to Weeks 1–3. The calorie cut comes from lighter Gousto "
             "and snack reduction, not lunch.", 500, 45, 60),
            ("Protein Shake", "Whey/casein shake — 25g protein",
             "Keep the shake even in recovery week — protein target doesn't drop.", 150, 25, 5),
            ("Dinner", "Gousto — lighter pick, but still 55g+ protein",
             "~560 kcal, 55g protein. Thai prawn salad, baked cod with greens, chicken & "
             "courgette bowl. Lean protein, less carb. Don't pick a low-protein option "
             "just because it's recovery week.", 560, 55, 48),
        ],
    },

    "recovery_saturday": {
        "label": "Recovery week Saturday — easy ruck",
        "calorie_tier": "recovery",
        "pre_dinner_protein_g": 117,
        "protein_note": "Recovery Saturday still needs 165g+ protein. Chicken lunch and GetPro "
                        "at breakfast are required — same rules as build-week Saturday.",
        "meals": [
            ("On Waking", "Fasted or banana", "Easy recovery ruck — fasted at easy pace is fine.", 90, 1, 23),
            ("Post-Ruck", "60g Porridge + 2 Eggs + GetPro Yoghurt",
             "On return — smaller than a build week (60g oats vs 80g) but GetPro stays in.", 570, 43, 48),
            ("Lunch", "Chicken 150g + Rice Pouch + Salad",
             "Chicken not eggs on toast — same rule as build Saturday. 150g chicken (not 240g) "
             "reflects the lighter recovery day.", 450, 40, 42),
            ("Protein Shake", "Whey/casein shake — 25g protein",
             "Saturday protein gap exists even in recovery week. Shake stays in.", 150, 25, 5),
            ("Dinner", "Gousto — lighter recovery pick, still 58g+ protein",
             "~650 kcal, 58g protein. Bias carbs if Sunday is still a ride.", 650, 58, 58),
        ],
    },

    "recovery_sunday": {
        "label": "Recovery week Sunday — shorter ride 75–90 min",
        "calorie_tier": "recovery",
        "pre_dinner_protein_g": 119,
        "protein_note": "GetPro added to recovery meal; Gousto raised to 62g. "
                        "Closes the day at ~165g — acceptable for recovery week.",
        "meals": [
            ("On Waking", "Banana or carb drink", "Small fast carbs only.", 150, 3, 32),
            ("On-Bike", "Carb drink or banana + half bar",
             "Even short recovery ride: ~40–60 g carbs/hr once it passes 75 min.", 150, 3, 32),
            ("Recovery", "Full Sunday Porridge + GetPro + 2 Eggs + Banana + Chocolate Milk",
             "Chocolate milk first, then 80g oats + GetPro + 2 eggs + banana. "
             "GetPro added here — was missing from recovery Sunday.", 750, 51, 74),
            ("Lunch", "Chicken 150g + Rice Pouch + Salad",
             "Recovery ride: 150g chicken (lighter than build week 240g). "
             "Still essential to get protein in post-ride.", 480, 42, 46),
            ("Dinner", "Gousto — end-of-cycle pick, 62g+ protein",
             "~680 kcal, 62g protein. Final meal before cycle repeats. Salmon, chicken or beef. "
             "Raise the protein target vs old plan (was 48g, now 62g).", 680, 62, 62),
        ],
    },
}

# ── Day-of-week → day type mapping ────────────────────────────────────────────
# weekday(): 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
# cycle_week: 0=w1, 1=w2, 2=w3, 3=w4 (recovery)
_WEEKDAY_TO_TYPE_BUILD = {
    0: "rest",
    1: "training",
    2: "bike",
    3: "thursday",
    4: "bike",
    5: "ruck",
    6: "long",
}

_WEEKDAY_TO_TYPE_RECOVERY = {
    0: "recovery_weekday",
    1: "recovery_weekday",
    2: "recovery_weekday",
    3: "recovery_weekday",
    4: "recovery_weekday",
    5: "recovery_saturday",
    6: "recovery_sunday",
}

# Rice variety rotations (weeks 2+3 swap rice at Mon/Tue/Fri, same macros)
_RICE_SWAP: dict[int, dict[int, str]] = {
    1: {0: "Ben's Mexican Rice", 4: "Ben's Golden Vegetable Rice"},
    2: {0: "Ben's Golden Vegetable Rice", 1: "Ben's Mexican Rice"},
}


def today_day_type(cycle_week: int, weekday: int) -> str:
    if cycle_week == 3:
        return _WEEKDAY_TO_TYPE_RECOVERY[weekday]
    return _WEEKDAY_TO_TYPE_BUILD[weekday]


def nutrition_coach_context(plan_start: date, today: date) -> str:
    """Return a compact text block describing today's prescribed nutrition."""
    days_since_start = (today - plan_start).days
    cycle_week = max(0, days_since_start // 7) % 4
    weekday = today.weekday()
    dtype = today_day_type(cycle_week, weekday)
    day_data = DAY_TYPES[dtype]
    tier = CALORIE_TIERS[day_data["calorie_tier"]]
    cycle_label = f"Week {cycle_week + 1}" + (" — Recovery" if cycle_week == 3 else "")

    total_protein = sum(m[4] for m in day_data["meals"])
    pt = protein_target_g()

    lines = [
        "## Nutrition Plan — Today's Prescribed Meals",
        f"Cycle: {cycle_label}  |  Day type: {day_data['label']}  |  "
        f"Target: {tier['kcal']} kcal" + (f" ({tier['note']})" if tier.get("note") else ""),
        f"Protein floor: {pt['low']}–{pt['high']}g/day ({pt['basis']}), distributed ~0.4 g/kg "
        f"across 4+ meals plus a ~40 g pre-sleep casein/dairy dose to preserve muscle in the deficit.",
        f"Today's meals deliver: {total_protein}g total  |  "
        f"Pre-dinner: {day_data['pre_dinner_protein_g']}g  — {day_data['protein_note']}",
        "",
        "Meals:",
    ]

    for slot, name, detail, kcal, prot, carbs in day_data["meals"]:
        swap = _RICE_SWAP.get(cycle_week, {}).get(weekday)
        if swap and "Rice" in name:
            name = swap
        lines.append(f"  {slot}: {name} — {kcal} kcal, {prot}g protein, {carbs}g carbs")
        lines.append(f"    {detail}")

    lines += ["", "Key principles:"]
    for p in PRINCIPLES:
        lines.append(f"  • {p}")

    lines += ["", f"Supplements ({_SUPPLEMENT_DISCLAIMER}):"]
    for name, dose, why in SUPPLEMENTS:
        lines.append(f"  • {name} — {dose}: {why}")

    return "\n".join(lines)
