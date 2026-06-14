#!/usr/bin/env bash
# Generate a single PDF from the Markdown user guide.
#
# Requires pandoc + a LaTeX engine, e.g. on macOS:
#   brew install pandoc basictex
#
# The Markdown files are the source of truth; user-guide.pdf is a build artifact
# (git-ignored) you can regenerate any time.
#
# The default LaTeX engine (pdflatex) can't typeset emoji, the ⓘ/⚠️ symbols,
# arrows, subscripts, or the box-drawing characters used in the directory trees.
# So we sanitise those glyphs to plain-text equivalents *for the PDF only* — the
# Markdown sources keep their emoji (which render fine on GitHub) untouched.
set -euo pipefail
cd "$(dirname "$0")"

FILES=(
  README.md
  getting-started.md
  concepts.md
  tabs/readiness.md
  tabs/performance.md
  tabs/plan.md
  tabs/health.md
  tabs/events.md
  tabs/coach.md
  email-and-automation.md
  faq.md
)

python3 - "${FILES[@]}" <<'PY' | pandoc \
    -o user-guide.pdf \
    --toc --toc-depth=2 \
    -V geometry:margin=1in \
    -V colorlinks=true \
    --metadata title="Garmin Readiness — User Guide"
import sys
import unicodedata

files = sys.argv[1:]

# Glyphs the default (pdflatex) engine can't typeset -> plain-text fallbacks.
REPLACE = {
    "📸": "", "📖": "",
    "🟢": "", "🟠": "", "🔴": "", "⚪": "",   # the colour word follows, so strip
    "⚠️": "[!]", "⚠": "[!]",
    "ⓘ": "(i)",
    "▾": "",
    "✓": "yes",
    "→": "->",
    "₂": "2",            # SpO₂, VO₂  ->  SpO2, VO2
    "×": "x",
    # mathematical operators (NOT the ASCII hyphen / less-than)
    "−": "-",            # U+2212 minus sign
    "≥": ">=", "≤": "<=", "≈": "~", "≠": "!=",
    # box-drawing used in the directory trees
    "├": "|", "└": "\\", "│": "|", "─": "-",
    # RPE effort emoji (handled as a run below, then any stragglers stripped)
    "😴": "", "😊": "", "😤": "", "🔥": "", "💀": "",
}

# Characters above Latin-1 that pdflatex's inputenc DOES map — keep these.
SAFE_ABOVE_FF = set("–—‘’“”…•‹›«»‚„†‡‰′″€™")

def _fallback(ch: str) -> str:
    """Last-resort transliteration so an unmapped glyph can never hard-fail
    the build: ASCII and Latin-1 pass through, known punctuation is kept, and
    anything else is reduced to an ASCII form (or dropped if it has none)."""
    if ord(ch) <= 0xFF or ch in SAFE_ABOVE_FF:
        return ch
    return unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()

def clean(text: str) -> str:
    text = text.replace("😴 😊 😤 🔥 💀", "(easiest -> hardest)")
    for src, dst in REPLACE.items():
        text = text.replace(src, dst)
    return "".join(_fallback(ch) for ch in text)

parts = [clean(open(f, encoding="utf-8").read()) for f in files]
# Page break between sections (raw LaTeX, passed through by pandoc's markdown reader).
print("\n\n\\newpage\n\n".join(parts))
PY

echo "Wrote $(pwd)/user-guide.pdf"
