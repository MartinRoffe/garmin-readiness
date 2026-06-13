"""Nutrition plan data — 4-week repeatable cycle.

Encoded here so the AI coach can reference the exact prescribed meals for
today rather than only seeing what was logged after the fact.
"""
from __future__ import annotations

from datetime import date

# ── Calorie tiers ─────────────────────────────────────────────────────────────
CALORIE_TIERS = {
    "rest":     {"label": "Rest / Monday",       "kcal": 1980},
    "training": {"label": "Training days (Tue–Fri)", "kcal": 2150},
    "ruck":     {"label": "Ruck Saturday",        "kcal": 2200},
    "long":     {"label": "Long ride Sunday",     "kcal": 2350,
                 "note": "baseline ~2 h; +150–200 kcal per extra hour beyond 2 h"},
    "recovery": {"label": "Recovery week Mon–Fri", "kcal": 1900},
}

# ── Principles ────────────────────────────────────────────────────────────────
PRINCIPLES = [
    "Protein-first: 160–180g/day. Every meal anchored to a protein source. "
    "GetPro at lunch on Mon/Wed/Thu closes the office gap.",
    "Carbs around training: rice/pasta lunches on ride and KB days. Banana 45 min pre-session. "
    "Ben's Paella Thursday fuels evening kettlebell. Saturday's carb-rich Gousto dinner is the "
    "real pre-ride fuel for Sunday — not breakfast.",
    "Sunday fuelling: 100–150 kcal fast carbs on waking only. On-bike from minute 0: "
    "60 g carbs/hr for rides 1–2.5 h, 75–90 g/hr beyond 2.5 h. Recovery meal within 45 min "
    "(chocolate milk first, then big porridge + eggs + banana + PB toast).",
    "Gut training: weeks 5–8 practise 60 g/hr on every ride ≥75 min. "
    "Weeks 9–11 push to 75–90 g/hr on long rides.",
    "Recovery week (W4): protein holds at 160g — cut comes from carbs/fat only. "
    "Snacks reduce to 1–2/day. Lighter Gousto (<500 kcal).",
    "Thursday warning: lowest pre-dinner protein day (94g). "
    "Gousto must be chicken/beef/salmon — not a pasta-only dish.",
]

# ── Day-type templates ────────────────────────────────────────────────────────
# Each day type: list of (meal_slot, name, detail, kcal, protein_g, carbs_g)
DAY_TYPES: dict[str, dict] = {

    "rest": {
        "label": "Rest day",
        "calorie_tier": "rest",
        "pre_dinner_protein_g": 120,
        "protein_note": "Easiest day to hit 160g — 120g pre-dinner, any Gousto gets you there.",
        "meals": [
            ("Breakfast", "Fuel10k + GetPro Yoghurt",
             "Grab-and-go. Shake Fuel10k, eat GetPro on the way in.", 330, 35, 39),
            ("AM Snack", "Nature Valley Protein Bar", "Mid-morning 10g protein hit.", 200, 10, 15),
            ("Lunch", "Asda Egg Fried Rice + Chicken 240g + GetPro",
             "Microwave rice. Chicken cold from pack. GetPro alongside.", 680, 73, 70),
            ("PM Snack", "Banana", "Afternoon energy.", 90, 1, 23),
            ("Eve Snack", "Apple or orange", "Light evening snack.", 70, 1, 18),
            ("Dinner", "Gousto — medium protein pick",
             "~600 kcal, target 44g+ protein. Rest day — any decent Gousto hits target.", 600, 44, 55),
        ],
    },

    "training": {
        "label": "Kettlebell + MaxiClimber",
        "calorie_tier": "training",
        "pre_dinner_protein_g": 105,
        "protein_note": "Training day — pick a Gousto with 50g+ protein. Chicken thighs, beef ragu or salmon.",
        "meals": [
            ("Breakfast", "Fuel10k + GetPro Yoghurt", "Same morning routine.", 330, 35, 39),
            ("AM Snack", "Nature Valley Protein Bar", "Mid-morning.", 200, 10, 15),
            ("Lunch", "Asda Egg Fried Rice + Chicken 240g",
             "Full 240g chicken pack. No GetPro needed — lunch protein is strong.", 550, 58, 58),
            ("PM Snack", "Banana", "45 min before evening session.", 90, 1, 23),
            ("Eve Snack", "Apple or orange", "Post-session.", 70, 1, 18),
            ("Dinner", "Gousto — high protein pick",
             "~680 kcal, 50–55g protein. Chicken, beef or salmon. This is a training day.", 680, 52, 70),
        ],
    },

    "bike": {
        "label": "Outdoor Bike 60 min",
        "calorie_tier": "training",
        "pre_dinner_protein_g": 100,
        "protein_note": "Ride day — carb-leaning Gousto fine. Target 50g+ protein from dinner.",
        "meals": [
            ("Breakfast", "Fuel10k + GetPro Yoghurt", "Same morning routine.", 330, 35, 39),
            ("AM Snack", "Nature Valley Protein Bar", "Mid-morning.", 200, 10, 15),
            ("Lunch", "Batchelors Pasta & Sauce + Tuna tin + GetPro",
             "Stir full tin of tuna into pasta. GetPro alongside. Good carbs for the afternoon ride.", 600, 53, 78),
            ("PM Snack", "Banana", "45 min before riding.", 90, 1, 23),
            ("Eve Snack", "Apple or orange", "Post-ride.", 70, 1, 18),
            ("Dinner", "Gousto — high protein, carb-friendly",
             "~680 kcal, 50g+ protein. Pasta, rice or noodle Gousto works well on ride evenings.", 680, 52, 72),
        ],
    },

    "thursday": {
        "label": "Kettlebell + MaxiClimber — Paella Thursday",
        "calorie_tier": "training",
        "pre_dinner_protein_g": 94,
        "protein_note": "⚠ Weakest pre-dinner day (94g). Make Gousto count — choose chicken or beef, NOT a veggie/pasta option.",
        "meals": [
            ("Breakfast", "Fuel10k + GetPro Yoghurt", "Same morning routine.", 330, 35, 39),
            ("AM Snack", "Nature Valley Protein Bar", "Mid-morning.", 200, 10, 15),
            ("Lunch", "Ben's Paella ★ + Prawns 150g + GetPro",
             "Paella Thursday. Prawns stirred in cold. GetPro alongside. Excellent carb base for evening kettlebell.", 480, 47, 62),
            ("PM Snack", "Banana", "45 min before session. Carbs matter tonight.", 90, 1, 23),
            ("Eve Snack", "Apple or orange", "Post-session.", 70, 1, 18),
            ("Dinner", "Gousto — strongest pick of the week",
             "~680 kcal, 52g+ protein. Lowest pre-dinner protein day — Gousto must deliver. Not a pasta-only dish.", 680, 52, 68),
        ],
    },

    "ruck": {
        "label": "Rucking 60–90 min",
        "calorie_tier": "ruck",
        "pre_dinner_protein_g": 49,
        "protein_note": "Lower protein day — Saturday relies heavily on Gousto. Add GetPro to post-ruck plate or lunch if needed.",
        "meals": [
            ("On Waking", "Banana out the door (or fasted)",
             "Very early start — fasted at easy pace is fine. Banana if you want fuel.", 150, 3, 30),
            ("Post-Ruck", "Porridge 80g + Milk + 2 Eggs + Banana",
             "On return — the recovery meal. 80g oats, semi-skimmed milk, 2 eggs, banana.", 530, 22, 72),
            ("Lunch", "3 Scrambled Eggs on 2 Slices Wholegrain Toast + Salad",
             "Lighter midday plate. Add half tin baked beans if hungrier.", 490, 22, 44),
            ("PM Snack", "Banana + Apple", "Afternoon.", 160, 2, 41),
            ("Dinner", "Gousto — carb-rich pick (this is tomorrow's pre-ride meal)",
             "~720 kcal, 48g protein. Bias to carb-rich tonight — butter chicken with rice, pasta, noodles, slow-cooked lamb. This dinner fuels Sunday's ride.", 720, 48, 82),
        ],
    },

    "long": {
        "label": "Long Ride — building to 3h30",
        "calorie_tier": "long",
        "pre_dinner_protein_g": 97,
        "protein_note": "Chocolate-milk-first recovery porridge replaces the pre-ride breakfast. Get it in within 45 min of finishing.",
        "meals": [
            ("On Waking", "Banana + honey toast, or carb drink in bottle 1",
             "100–150 kcal fast carbs only, 10–20 min before rolling. Last night's Gousto dinner was the real pre-ride meal.", 160, 3, 32),
            ("On-Bike", "Carb drink mix + banana + Crunchy bars (from minute 0)",
             "60 g carbs/hr for 1–2.5 h, 75–90 g/hr beyond 2.5 h. Start in first 15 min, something every 20–30 min. 500 ml bottle with 40–60 g carb drink, banana (~25 g), Crunchy bar (~28 g). 500–750 ml fluid/hr.", 450, 6, 104),
            ("Recovery", "Big Porridge + 2 Eggs + Banana + PB Toast + Chocolate Milk",
             "Within 45 min of finishing. Chocolate milk first, then 80g oats + eggs + banana + PB toast.", 700, 40, 82),
            ("Lunch", "Chicken 150g + Rice Pouch + Salad",
             "A few hours after the ride. Tops up protein and glycogen through the afternoon.", 500, 48, 56),
            ("Dinner", "Gousto — protein-rich recovery",
             "~650 kcal, 52g+ protein. Slow-cooked beef ragu, Thai chicken, lemon herb salmon.", 650, 52, 64),
        ],
    },

    "recovery_weekday": {
        "label": "Recovery week Mon–Fri",
        "calorie_tier": "recovery",
        "pre_dinner_protein_g": 90,
        "protein_note": "Recovery week: hold protein at 160g. The calorie cut comes from carbs/fat, not protein.",
        "meals": [
            ("Breakfast", "Fuel10k + GetPro Yoghurt (same)", "Keep breakfast identical.", 330, 35, 39),
            ("Snack", "1–2 snacks only (vs 3)",
             "Keep protein snacks. Drop afternoon apple/orange on rest days. Bar on training days only.", 200, 10, 15),
            ("Lunch", "Same lunch choices — no change",
             "Lunch stays identical. The calorie cut comes from reduced snacks and lighter Gousto.", 500, 45, 60),
            ("Dinner", "Gousto — lighter pick",
             "Under 500 kcal this week. Thai prawn salad, baked cod, chicken & courgette bowl.", 500, 42, 48),
        ],
    },

    "recovery_saturday": {
        "label": "Recovery week Saturday — easy ruck",
        "calorie_tier": "recovery",
        "pre_dinner_protein_g": 51,
        "protein_note": "Recovery Saturday — intentionally lighter. Big breakfast after the ruck.",
        "meals": [
            ("On Waking", "Fasted or banana", "Easy recovery ruck — fasted at easy pace is fine.", 90, 1, 23),
            ("Post-Ruck", "60g Porridge + 2 Eggs",
             "On return — smaller than a build week (60g oats vs 80g).", 420, 28, 48),
            ("Lunch", "2 Eggs on Toast + Large Salad", "Lighter midday plate.", 380, 22, 38),
            ("Dinner", "Gousto — light treat",
             "~600 kcal. Keep lighter than normal Saturday, but bias carbs if Sunday is a longer ride.", 600, 44, 58),
        ],
    },

    "recovery_sunday": {
        "label": "Recovery week Sunday — shorter ride 75–90 min",
        "calorie_tier": "recovery",
        "pre_dinner_protein_g": 87,
        "protein_note": "Shorter recovery ride. Chocolate milk and porridge still the recovery meal.",
        "meals": [
            ("On Waking", "Banana or carb drink", "Small fast carbs only.", 150, 3, 32),
            ("On-Bike", "Carb drink or banana + half bar",
             "Even short recovery ride: ~40–60 g carbs/hr once it passes 75 min.", 150, 3, 32),
            ("Recovery", "Full Sunday Porridge + 2 Eggs + Banana + Chocolate Milk",
             "Chocolate milk first, then 80g oats + 2 eggs + banana.", 600, 36, 74),
            ("Lunch", "Lighter recovery plate",
             "120g chicken (not 150g), half rice pouch, salad.", 530, 48, 56),
            ("Dinner", "Gousto — end-of-cycle pick",
             "~630 kcal. Final meal before cycle repeats. Salmon, chicken or beef.", 630, 48, 62),
        ],
    },
}

# ── Day-of-week → day type mapping ────────────────────────────────────────────
# weekday(): 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
# cycle_week: 0=w1, 1=w2, 2=w3, 3=w4(recovery)
_WEEKDAY_TO_TYPE_BUILD = {
    0: "rest",       # Monday
    1: "training",   # Tuesday
    2: "bike",       # Wednesday
    3: "thursday",   # Thursday
    4: "bike",       # Friday
    5: "ruck",       # Saturday
    6: "long",       # Sunday
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
    1: {0: "Ben's Mexican Rice", 4: "Ben's Golden Vegetable Rice"},          # w2
    2: {0: "Ben's Golden Vegetable Rice", 1: "Ben's Mexican Rice"},           # w3
}


def today_day_type(cycle_week: int, weekday: int) -> str:
    if cycle_week == 3:  # w4 = recovery
        return _WEEKDAY_TO_TYPE_RECOVERY[weekday]
    return _WEEKDAY_TO_TYPE_BUILD[weekday]


def nutrition_coach_context(plan_start: date, today: date) -> str:
    """Return a compact text block describing today's prescribed nutrition."""
    days_since_start = (today - plan_start).days
    cycle_week = max(0, days_since_start // 7) % 4  # 0-indexed
    weekday = today.weekday()
    dtype = today_day_type(cycle_week, weekday)
    day_data = DAY_TYPES[dtype]
    tier = CALORIE_TIERS[day_data["calorie_tier"]]
    cycle_label = f"Week {cycle_week + 1}" + (" — Recovery" if cycle_week == 3 else "")

    lines = [
        "## Nutrition Plan — Today's Prescribed Meals",
        f"Cycle: {cycle_label}  |  Day type: {day_data['label']}  |  "
        f"Target: {tier['kcal']} kcal" + (f" ({tier['note']})" if tier.get('note') else ""),
        f"Pre-dinner protein target: {day_data['pre_dinner_protein_g']}g  — {day_data['protein_note']}",
        "",
        "Meals:",
    ]

    for slot, name, detail, kcal, prot, carbs in day_data["meals"]:
        # Note rice swaps for weeks 2/3
        swap = _RICE_SWAP.get(cycle_week, {}).get(weekday)
        if swap and "Rice" in name:
            name = swap
        lines.append(f"  {slot}: {name} — {kcal} kcal, {prot}g protein, {carbs}g carbs")
        lines.append(f"    {detail}")

    lines += [
        "",
        "Key principles:",
    ]
    for p in PRINCIPLES:
        lines.append(f"  • {p}")

    return "\n".join(lines)
