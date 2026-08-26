---
name: Bootlegger
description: ORCHARD PARK NIGHT — a Bills stadium-night broadcast board. Navy under the lights, electric royal for everything you can touch, Bills red strictly for trouble, memes in bedsheet marker.
colors:
  ground: "#06122e"
  panel: "#0b1c44"
  panel-2: "#112552"
  panel-3: "#172e62"
  line: "rgba(236, 242, 255, .28)"
  line-soft: "rgba(236, 242, 255, .13)"
  ink: "#f2f6ff"
  ink-dim: "#c2cde6"
  ink-faint: "#8499c4"
  accent: "#5b8cff"
  accent-bright: "#93b4ff"
  accent-deep: "#2e5fd9"
  good: "#3ed98a"
  good-bright: "#7fedb6"
  warn: "#ffa23d"
  flag: "#ff5d66"
  flag-deep: "#c60c30"
  qb: "#ff7a95"
  rb: "#52d98b"
  wr: "#59d3f2"
  te: "#f0954a"
  k: "#c39bff"
  def: "#e4de6b"
  paper: "#f2f5fd"
  paper-2: "#e4eaf8"
  paper-ink: "#17233e"
  paper-faint: "#4c5c82"
  paper-rule: "rgba(23, 35, 62, .16)"
  paper-margin: "rgba(198, 12, 48, .5)"
  paper-ok: "#1f7a4d"
  paper-bad: "#b01230"
typography:
  display: { fontFamily: "Anton, Barlow Semi Condensed, sans-serif", note: "wordmark (italic, red drop shadow) + room/plate titles" }
  body: { fontFamily: "Barlow Semi Condensed, Arial Narrow, sans-serif", note: "the working voice — prose, labels, buttons, tabs" }
  figures: { fontFamily: "Courier Prime, monospace", note: "every number, always" }
  banner: { fontFamily: "Permanent Marker, Comic Sans MS, cursive", note: "memes only — the Mafia stamps and THE TABLE's tag; never data, labels, or controls" }
---

# Design System: Bootlegger — Orchard Park Night

**Creative North Star: "A Bills Night Game as a Working Interface."**
Replaced Chalk & Turf 2026-08-25 at the owner's direction (user-pinned:
Stadium Night + the full-send Mafia meme layer). Deep stadium navy with faint
yard lines and hash ticks drawn into the ground itself, white ink, grain over
everything. Electric royal is everything you can touch; Bills red is trouble;
the fandom lives in one zubaz stripe, one cartoon buffalo, and a folding
table — never in the figures. All color pairs verified AA 2026-08-25. In
`styles.css` the variable NAMES keep their old spellings (`--brass` = royal
accent, `--lamp` = good, `--marigold` = warn, `--oxblood` = flag) so every
downstream rule keeps working; this file's role slugs map onto them.

## The Rules That Carried Over

- **Typewriter Figures Rule**: every number is Courier Prime — stamps, meters,
  clocks, tape, bids, steppers. Labels around a figure stay the condensed
  gothic. Non-negotiable, unchanged.
- **Written Label Rule**: position hues never appear without their written
  code; status colors (good/warn/flag) never moonlight as data colors.
  Unchanged.
- **Never a silent failure**: the wire-down banner now reads **"CIRCLE THE
  WAGONS"** over the stale board's age; scrimmage and the pilot get their own
  sticky banners; failed steps go flag-red. Louder in this world, never
  quieter.
- **No Sideways Page**: `overflow-x: clip` on body; the ticker, stepper, and
  report card scroll inside their own containers. Unchanged.
- **Paper surfaces**: the ledger, rules, ticker tape, and Scout's File are now
  white game-program paper — a cool white sheet with the red ledger margin
  rule at 42px — carrying its own ink set (`paper-ink`/`paper-faint`/
  `paper-ok`/`paper-bad`, each ≥4.5:1 on the sheet's darkest stop). Crossing
  materials still means switching ink sets.

## This World's Own Rules

- **The One Zubaz Rule.** The zubaz is an authored SVG tile — irregular
  hand-cut red/royal jags on white (real zebra print, not a candy stripe),
  90px repeat. Exactly two things wear it: the masthead (7px strip beneath
  it) and the report card (headband). Nowhere else, ever.
- **Royal Is Touchable, Red Is Trouble.** Royal (`accent`) marks the
  interactive world — tabs, chips, the primary key, focus rings, links, your
  rows, the burning survival track. Bills red (`flag`/`flag-deep`) marks
  trouble only — wire down, failed, hurt, struck through. The two semantics
  never blur. The meme layer's red (the zubaz, the wordmark's drop shadow,
  the bedsheet stamps) is fandom, not status: it never colors a figure, a
  label, or a control.
- Plate borders stay dashed on the loud objects (The Call in royal, the shelf
  in hairline); corners run 10–12px; the primary button is the royal key —
  white 700 text on the royal gradient (5.2:1 at the mid-stop), a hard 3px
  press edge, ≥46px tall. The lace-seam under titles runs its cross-laces
  royal.

## The Fun Layer (this world's voice)

- **The Buffalo** — an original cartoon bison (royal body, red speed streak)
  lives on The Call; he keeps the old ref's `.ref`/`.is-td` class contract.
  Idle: a slow breathing bob (3.6s). On your clock: he stampedes across the
  plate twice, dust puffing behind him (1.15s runs). He reacts; he never
  talks over the figures.
- **The telestrator** — the booth's glowing royal pen draws the route across
  The Call on a 7s `stroke-dashoffset` loop.
- **The ball hurdle** — your pick landing sends a football up and over the
  row (1s, 2.5 turns), alongside the strike-through and the stamp settle.
- **THE TABLE** — a folding table stands in the shelf's corner all season.
  When your pick lands it folds (legs at ±72°, "FOLDED" tag slam) and stands
  back up after 3.2s — there is always another table. A verified swap on This
  Week slams the bedsheet stamp **"Table's folded ✓"** once (first render of
  that rec only; re-polls never re-slam).
- **The Bedsheet Marker Rule.** Permanent Marker appears ONLY in these stamps
  (`.mafia-stamp` and THE TABLE's smash tag). Anywhere else it is a defect.

## Motion: The Broadcast Cut

One grammar: rooms cut in like broadcast graphics — rise and settle. Rooms:
200ms ease-out, opacity + 6px translateY. Rows: 280ms on the settle spring
(`cubic-bezier(.16,1,.3,1)`) with a 30ms stagger capped at 180ms
(`nth-child(n+7)` all share the cap). Transform/opacity only, and every entry
keys off the `[hidden]` toggle's display restart — tab switches and column
rebuilds animate; the 1 Hz poll patches never re-fire anything. The pick
moment (strike + stamp settle) rides the same spring.

`prefers-reduced-motion` strips the characters rather than freezing them:
dust, telestrator, and ball flyby go `display: none`; the buffalo stays as a
static mascot; entries and stamp slams render instantly. The board must read
perfectly as a still.

## Provenance

- Chalk & Turf (chalkboard green, Patrick Hand, the cartoon ref) served
  2026-08-24→25 and is preserved in git history. It had replaced the
  Speakeasy back room, which still governs the household HA dashboards.
- Limelight and Patrick Hand are retired from this surface; their
  `@font-face` blocks and woff2 files remain for git history.
- The draft-overlay extension (v1.4.0) and the mobile app
  (`mobile/src/theme.ts`) mirror THIS world — both updated the same day,
  2026-08-25.
