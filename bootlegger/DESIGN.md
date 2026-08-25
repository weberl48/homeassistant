---
name: Bootlegger
description: A Saturday-morning-cartoon playbook — chalkboard turf, chalk ink, penalty yellow, and a referee who celebrates your picks.
colors:
  ground: "#16382b"
  panel: "#1b4234"
  panel-2: "#215040"
  panel-3: "#275c4a"
  line: "rgba(242,247,239,.30)"
  line-soft: "rgba(242,247,239,.14)"
  ink: "#f2f7ef"
  ink-dim: "#c9dccd"
  ink-faint: "#96b6a0"
  accent: "#f7c948"
  accent-bright: "#ffdd6b"
  accent-deep: "#c29a25"
  good: "#58c07c"
  good-bright: "#86e6a8"
  warn: "#ff9838"
  flag: "#ff6b5e"
  flag-deep: "#c23b30"
  qb: "#ff7a95"
  rb: "#52d98b"
  wr: "#6fb1ff"
  te: "#f0954a"
  k: "#b591ff"
  def: "#d8d060"
  paper: "#e8dcba"
  paper-ink: "#33291a"
typography:
  display: { fontFamily: "Permanent Marker, cursive", note: "wordmark + fun accents only" }
  body: { fontFamily: "Patrick Hand, Barlow Semi Condensed, sans-serif" }
  figures: { fontFamily: "Courier Prime, monospace", note: "every number, always" }
---

# Design System: Bootlegger — Chalk & Turf

**Creative North Star: "The Coach's Chalkboard, Saturday-Morning Edition."**
Replaced the Speakeasy back-room world 2026-08-24 at the owner's direction:
straight-to-the-point information with cartoon-playbook fun. A deep
chalkboard-green field (faint yard lines and hashes in the ground itself),
chalk-white ink, penalty-yellow attention, flag-red trouble. Serious numbers,
playful frame — the data never dresses down, only the room does.

## The Rules That Carried Over

- **Typewriter Figures Rule**: every number is Courier Prime. Labels around a
  figure are the hand-drawn gothic (Patrick Hand). Non-negotiable.
- **Written Label Rule**: position hues never appear without their written
  code; status colors (good/warn/flag) never moonlight as data colors.
- **Never a silent failure**: wire lamp, PICK FEED STALE banner, flag-red
  states — all louder in this world, never quieter.
- **No Sideways Page / Pinned Track rules**: unchanged.
- Paper surfaces (ledger, rules, ticker, the Scout's File) keep the manila
  clipboard material and its own ink set.

## The Fun Layer (this world's voice)

- **The Ref** — a cartoon referee lives on The Call: idle bob; TOUCHDOWN
  arms + a double hop when you're on the clock. He reacts; he never talks
  over the figures.
- **The living route** — a chalk O runs its route across The Call on a slow
  loop (`stroke-dashoffset` draw, ~7s).
- **The flyby** — your pick landing sends a football spiraling across the
  row, alongside the chalk strike-through (a chalkboard cross-out now reads
  as native).
- Plate borders are dashed chalk on the loud objects; corners rounded to
  10–12px; the primary button is a chunky yellow arcade key with a hard
  3px press edge.
- `prefers-reduced-motion` removes every character animation and loop; the
  board must still read perfectly as a still.

## Marker Rule (amended)

Permanent Marker sets the wordmark and may appear in small fun accents
(stamps, celebration text). Body and labels are Patrick Hand; density rows
may fall back to Barlow Semi Condensed where legibility demands.

## Provenance

The previous world ("The Bootlegger's Back Room" — walnut, brass, ledger
paper) is preserved in git history at tag-commit b877764 and remains the
aesthetic of the household HA dashboards. The mobile app theme
(`mobile/src/theme.ts`) still mirrors the OLD world and needs a follow-up.
