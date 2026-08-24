---
name: Bootlegger
description: A bootlegger's back-room ledger board — walnut, brass, and typewriter figures for one league.
colors:
  ground: "#17120e"
  panel: "#1e1712"
  panel-2: "#251c14"
  panel-3: "#2d2318"
  line: "#3b2f21"
  line-soft: "#2a2118"
  ink: "#ecdfc6"
  ink-dim: "#b5a483"
  ink-faint: "#91805f"
  brass: "#c9a25c"
  brass-bright: "#e6c684"
  brass-deep: "#8a6d33"
  lamp: "#58a06c"
  lamp-bright: "#79c28c"
  marigold: "#d9a441"
  oxblood: "#cf6152"
  oxblood-deep: "#8e3e33"
  qb: "#b8455f"
  rb: "#3aa96b"
  wr: "#4f8fd8"
  te: "#cb7c2c"
  k: "#8768cf"
  def: "#9c8f38"
  paper: "#e8dcba"
  paper-2: "#e0d2a9"
  paper-ink: "#33291a"
  paper-faint: "#5a4c31"
  paper-rule: "rgba(51, 41, 26, .18)"
  paper-margin: "rgba(164, 82, 63, .55)"
  paper-ok: "#2f5a3d"
  paper-bad: "#7e352b"
typography:
  display:
    fontFamily: "Limelight, Georgia, serif"
    fontSize: "clamp(1.5rem, 3.4vw, 2.1rem)"
    fontWeight: 400
    letterSpacing: "0.14em"
  headline:
    fontFamily: "Barlow Semi Condensed, Arial Narrow, system-ui, sans-serif"
    fontSize: "1.35rem"
    fontWeight: 700
    letterSpacing: "0.05em"
  title:
    fontFamily: "Barlow Semi Condensed, Arial Narrow, system-ui, sans-serif"
    fontSize: "0.95rem"
    fontWeight: 700
    letterSpacing: "0.22em"
  body:
    fontFamily: "Barlow Semi Condensed, Arial Narrow, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "Barlow Semi Condensed, Arial Narrow, system-ui, sans-serif"
    fontSize: "0.7rem"
    fontWeight: 700
    letterSpacing: "0.1em"
  figures:
    fontFamily: "Courier Prime, Courier New, monospace"
    fontSize: "0.84rem"
    fontWeight: 400
    letterSpacing: "0.02em"
  figures-display:
    fontFamily: "Courier Prime, Courier New, monospace"
    fontSize: "1.9rem"
    fontWeight: 700
    letterSpacing: "0.02em"
rounded:
  hairline: "2px"
  tag: "3px"
  control: "5px"
  plate: "6px"
  pill: "999px"
spacing:
  hair: "4px"
  tight: "8px"
  gap-card: "14px"
  pad-plate: "16px"
  gutter: "20px"
  gap-rail: "24px"
  gap-section: "44px"
components:
  button-primary:
    backgroundColor: "{colors.brass}"
    textColor: "{colors.ground}"
    rounded: "{rounded.control}"
    padding: "15px 22px"
    height: "46px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink-dim}"
    rounded: "{rounded.control}"
    padding: "15px 22px"
    height: "46px"
  chip:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink-dim}"
    rounded: "{rounded.pill}"
    padding: "9px 15px"
  chip-active:
    backgroundColor: "{colors.brass}"
    textColor: "{colors.ground}"
    rounded: "{rounded.pill}"
    padding: "9px 15px"
  tab:
    textColor: "{colors.ink-dim}"
    rounded: "6px 6px 0 0"
    padding: "11px 16px 12px"
  tab-active:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.brass-bright}"
    rounded: "6px 6px 0 0"
    padding: "11px 16px 12px"
  stamp:
    textColor: "{colors.ink-faint}"
    typography: "{typography.figures}"
    rounded: "{rounded.tag}"
    padding: "1px 5px"
  stamp-mine:
    backgroundColor: "{colors.brass}"
    textColor: "{colors.ground}"
    typography: "{typography.figures}"
    rounded: "{rounded.tag}"
    padding: "1px 5px"
  sheet:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.paper-ink}"
    rounded: "{rounded.tag}"
    padding: "18px 26px 22px 58px"
  clockplate:
    backgroundColor: "{colors.panel-2}"
    textColor: "{colors.ink}"
    typography: "{typography.figures}"
    rounded: "4px"
    padding: "7px 14px"
---

# Design System: Bootlegger

## Overview

**Creative North Star: "The Bootlegger's Back Room"**

Bootlegger is the discreet back room of the household's pinned Speakeasy Cinema
world: a walnut desk under one lamp, brass fittings, and paper records — rendered
as a working tool, never a costume. The room is built from exactly two materials.
The **walnut surface** (near-black warm browns, aged-cream ink, hairline brass
rules) carries the live working board; the **ledger paper** (warm cream sheets
and ticker tape with their own darker inks) carries anything the house has
committed to record — rules, the audit trail, the pick tape. Everything that
glows — brass for attention and action, banker's-lamp green for verified good
news — reads as lamplight on metal, not as UI chrome.

The room is dense on purpose. It is read at a glance under a draft clock or
before kickoff, so hierarchy does the work density would otherwise destroy:
tier bands, survival meters, and stamped pick numbers on the board; one loud
brass-edged plate (The Call, the verdict) per screen telling you the single
thing the room thinks. Numbers are the cargo, and every one of them is set in
typewriter type. Two worlds were explicitly refused at direction time and stay
refused: the white-card sports-app dashboard and the neon terminal.

The web surface (`server/app/web/`) is the authority for this system; the
companion Expo/React Native app consumes the same palette via
`mobile/src/theme.ts`, which mirrors these tokens and must follow this file.

**Key Characteristics:**
- Two materials only: dark walnut working surfaces and cream ledger paper, each with its own complete ink set.
- Brass is the voice of attention and action; lamp green is earned (verified/good); oxblood is trouble.
- Six validated categorical hues for positions, never shown without their written label.
- Every figure is typewriter type (Courier Prime); the marquee face belongs to the house name alone.
- Dense, glanceable plates with hairline rules; one loud object per room.
- Motion is a single authored moment (the pick-stamp strike and settle); everything else is quiet.

## Colors

A dark, warm, low-chroma room where the accents are metals and lamplight: brass
leads, lamp green confirms, oxblood objects, and six cooler-and-warmer data hues
mark positions on the board.

### Primary

- **Brass** (#c9a25c): the house metal. Attention, action, and "yours": the primary button, active tab underlay, active filter chip, tier labels, survival-meter fill, links, focus rings, selection, caret, "MINE" stamps.
- **Bright Brass** (#e6c684): polished highlights — the wordmark, headline figures (the delta), the active tab's lettering, gradient tops of brass objects.
- **Deep Brass** (#8a6d33): tarnished edges — borders on brass-tinted plates, hover borders, gradient bottoms, "done" steps. The border color that says "brass lives here" without shouting.

### Secondary

- **Banker's-Lamp Green** (#58a06c): the good lamp. Wire-live dot, verified states, "lineup optimal" plate borders, the `is-in` row wash. Status only.
- **Bright Lamp** (#79c28c): lamp text on dark ground ("verified", "Draft complete") where #58a06c would sit too dim.

### Tertiary

- **Marigold** (#d9a441): the warning flag ("BIG SWING — CONFIRM TWICE"). Rare by design.
- **Oxblood** (#cf6152): trouble — injuries and OUT tags, strike-through rules on picked players, wire-down states, holds. Status only, never decorative.
- **Deep Oxblood** (#8e3e33): trouble's structure — failed-step fills, banner borders, out-row strike-throughs.

### Position Hues (categorical data colors)

Six dataviz-validated categorical colors, tuned for the dark walnut surface
(all hold ≥3:1 against the panel they sit on): **QB** (#b8455f), **RB**
(#3aa96b), **WR** (#4f8fd8), **TE** (#cb7c2c), **K** (#8768cf), **DEF**
(#9c8f38). They appear as an 8px rounded swatch beside a written position
label (`.pos`), in column heads, call metadata, rosters, tickers, and lineup
rows. They are series colors, not status colors.

### Neutral

- **Walnut Ground** (#17120e): the page itself and everything "cut into" it (active tab wells, scrollbar tracks).
- **Walnut Panels** (#1e1712 / #251c14 / #2d2318): the raise ladder — `panel` for plates, `panel-2` for heads, hovers, and gradient tops, `panel-3` reserved for the highest lift.
- **Hairlines** (#3b2f21 line, #2a2118 line-soft): every rule and border on walnut; `line` structures, `line-soft` subdivides rows.
- **Aged-Cream Ink** (#ecdfc6): primary text on walnut (14.1:1 on ground).
- **Dim Ink** (#b5a483) and **Faint Ink** (#91805f): secondary prose and whisper-level labels/counts (7.6:1 and 4.8:1 on ground — faint ink is already the floor; go no dimmer for text).
- **Ledger Paper** (#e8dcba top, #e0d2a9 low stop): the sheet and tape material, always rendered as a top-lit gradient.
- **Paper Inks** — ink (#33291a), faint (#5a4c31), ok (#2f5a3d), bad (#7e352b): the paper's own text set; see the Paper Ink Rule.
- **Paper Rule** (rgba(51, 41, 26, .18)) and **Paper Margin** (rgba(164, 82, 63, .55)): the ledger's feint row rules and its red margin line.

### Named Rules

**The Written Label Rule.** A position hue never appears without its written
label beside it — the swatch-plus-text `.pos` tag is the only way position
identity is drawn. Color alone identifies nothing, anywhere: injury, failure,
and verification likewise always pair color with a word or icon.

**The Reserved Status Rule.** Lamp green (good/verified), marigold (warning),
and oxblood (trouble/OUT) are status colors. They are never used as series,
position, or decorative colors — and the six position hues are never used to
signal status. The two vocabularies do not mix.

**The Paper Ink Rule.** Paper surfaces carry their own ink tokens
(`paper-ink`, `paper-faint`, `paper-ok`, `paper-bad`), each holding ≥4.5:1
against the sheet gradient's darkest stop (#e0d2a9). Walnut-surface inks and
accents never sit directly on paper; paper inks never sit on walnut. Crossing
a material means switching ink sets.

## Typography

**Display Font:** Limelight (with Georgia, serif)
**Body Font:** Barlow Semi Condensed (with Arial Narrow, system-ui, sans-serif)
**Figure/Ledger Font:** Courier Prime (with Courier New, monospace)

**Character:** A marquee face for the sign over the door, an industrial
semi-condensed gothic doing all the talking, and a typewriter carrying all the
numbers. All three are self-hosted woff2 (`/assets/fonts/`, `font-display:
swap`) — the room renders with no network dependency.

### Hierarchy

- **Display** (400, clamp(1.5rem, 3.4vw, 2.1rem), .14em tracking): the BOOTLEGGER wordmark. Nothing else — see the Marquee Rule.
- **Headline** (700, 1.3–1.55rem): room titles (uppercase, .05em), the verdict line, and The Call's player name. Set in the working gothic, never the marquee face.
- **Title** (700, .95rem, .22em tracking, uppercase): plate titles ("THE CALL", "MY SHELF"). The widest tracking in the room; it reads as engraved plate lettering.
- **Body** (400, 15px, 1.45): prose, rationale, notes. Long-form text keeps a measure cap (62–70ch).
- **Label** (600–700, .62–.78rem, .06–.22em tracking, uppercase): the whisper layer — position tags, column stat labels, table headers, tabs, needs chips. Weight and tracking scale with size: smaller labels track wider.
- **Figures** (Courier Prime 400–700, .72–.95rem): every number — VBD, ADP, survival %, projections, pick numbers, bids, timestamps — plus all ledger/tape/stepper/clock text. Bold (700) marks the emphasized figure (VBD, totals, the bid pill).
- **Figures Display** (Courier Prime 700, 1.9rem, 1.6rem ≤900px): the verdict delta, the one number allowed to be loud, with a matching brass or lamp text-glow.

### Named Rules

**The Marquee Rule.** Limelight is the house name only. It sets the wordmark
and nothing else — no headings, no numbers, no empty states. Everything that
works for a living is Barlow Semi Condensed; everything counted is Courier
Prime.

**The Typewriter Figures Rule.** If it is a number, it is Courier Prime — in
tables, meters, stamps, steppers, clocks, and tape. UI labels around a figure
stay in the gothic (`.lbl` beside a Courier value), so figures always look
typed onto the surface rather than styled.

## Layout

A 1560px max-width shell (masthead, main, colophon) with 20px side gutters
(14px ≤900px); record-keeping rooms (Waivers, The Ledger) narrow to 860px, and
This Week to 980px. The Board is a two-track grid: a 300px left rail — The
Call first, where reading starts, with My Shelf beneath — then the four-column
tier board scanning left-to-right away from it. The rail is sticky at 96px
below the sticky masthead; each position column caps at `calc(100vh - 210px)`
and scrolls internally (`overscroll-behavior: contain`) with its own sticky
column head.

Spacing runs on a loose 4px grain with deliberate odd-pixel optical nudges
(7/9/11px paddings inside plates) — there is no rigid scale. The anchors:
14px between board columns, 16px plate padding, 20px page gutter, 22–24px
between rail objects and layout tracks, 44px between stacked room sections.
Density is the point; whitespace separates plates, hairlines separate rows.

Stacking order is fixed: ambient light (z 0, fixed) → content (z 1) → sticky
column heads (z 2) → wire-down banner (z 29) → masthead (z 30) → film grain
overlay (z 40, above everything) → skip link on focus (z 100).

Breakpoints, all max-width: **1180px** — the board drops to two columns
(column cap 460px); **900px** — everything single-column: the masthead
re-stacks (marque + wire / full-width clockplate / scrollable tab row), the
rail unsticks and precedes the board (call first on the phone), position
filter chips appear, columns lose their internal scroll, buttons stretch
full-width; **420px** — the lifecycle stepper compresses (.58rem, 7px links)
so all five states fit one 390px line — the verified terminal may never hide
off-screen on the one-thumb surface.

### Named Rules

**The Pinned Track Rule.** Every flexible grid track is `minmax(0, 1fr)`, and
any grid/flex item that can host a wide strip carries `min-width: 0`
(`.board-main`, `.rail`, `.col`, `.ticker-wrap`). Intrinsic-width blowouts
from the ticker and stepper are the known hazard; a bare `1fr` is how they get
loose.

**The No Sideways Page Rule.** The page never scrolls horizontally (`body {
overflow-x: clip }`). Wide strips — the ticker tape, the stepper, the mobile
tab row — scroll inside their own containers instead, with their own
material-matched scrollbars. On phones the stepper compresses rather than
wraps: a broken chain drops orphan connectors.

## Elevation & Depth

Depth is lamplight, not floating cards. A fixed overhead glow
(`radial-gradient` of rgba(214,166,88,.10) from above) and a darkness pool
below sit under the content, and an SVG-noise film grain (5% opacity,
`mix-blend-mode: overlay`) sits fixed over everything, unifying both
materials. Surfaces lift with soft, purely black shadows plus a top-lit
gradient (`panel-2 → panel` on plate heads); paper additionally gets a warm
inset top highlight so the sheet edge catches the lamp. Colored glow is
never ambient — it is state: brass glow when it's your turn or your action,
lamp glow when the wire is live or the swap verified.

### Shadow Vocabulary

- **Plate** (`box-shadow: 0 2px 6px rgba(0,0,0,.45), 0 8px 24px rgba(0,0,0,.35)` — `--shadow-plate`): the loud objects — masthead, The Call, the verdict, the all-good plate.
- **Raise** (`box-shadow: 0 1px 3px rgba(0,0,0,.5), 0 6px 18px rgba(0,0,0,.3)` — `--shadow-raise`): working furniture — shelf, board columns, tables.
- **Paper drop** (`0 3px 8px rgba(0,0,0,.55), 0 14px 34px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,250,232,.85)`; the tape uses a shallower `0 2px 6px / 0 8px 20px` pair): sheets float highest — paper on the desk, not paint on the wall.
- **Brass glow** (`0 2px 10px rgba(201,162,92,.35)` on the on-the-clock plate; `0 6px 16px rgba(201,162,92,.22)` under the primary button; matching text-shadows on wordmark and delta): "this is yours, act."
- **Lamp glow** (`0 1px 6px rgba(88,160,108,.8)` on the wire dot): the small green all-clear.

### Named Rules

**The One Lamp Rule.** Light comes from above and is warm: gradients run dark
downward, insets highlight top edges only, and shadows are soft and black.
Colored glow is a state signal (brass = yours/on the clock, lamp = live/
verified, oxblood dot-glow = wire down) — never decoration at rest.

## Shapes

Tight, machined corners: 2px on the smallest cuts (ticker tape, survival
track, focus rings), 3px on tags and paper (stamps, needs, steps, bids, the
sheet itself), 5px on buttons, 6px on plates and columns. The clockplate sits
between at 4px. The only pills are the mobile filter chips (999px) and the
46×24px toggle; the only circles are lamp dots and the toggle knob. Tabs
round their top corners only — file-drawer tabs whose active member merges
into the ground below and takes a 2px inset brass bar across its top.

Two drawn gestures give the room its hand: the **strike** — a 2px oxblood
line rotated -1.1° through a picked player's name (brass-deep when the player
is yours), and the ledger sheet's **red margin line** — 1px of `paper-margin`
at 42px from the sheet's left edge (32px ≤900px), with content indented
beyond it. Hairline rules do all remaining structure; heavy borders don't
exist — emphasis borders just change hue (brass-deep, lamp, oxblood).

## Components

### Masthead & Clock Plate
The proscenium: sticky top bar on a dark walnut gradient with the Limelight
wordmark and its "est. 2026 · the back room" subline, the clockplate, the
room tabs, and the wire lamp. The **clockplate** is the draft's heartbeat: a
4px-radius plate (min-width 250px, `aria-live="polite"`) with a Courier
status line and a dim sub-line; when you're on the clock it takes a brass
border, brass-bright text, and the brass glow. The **wire lamp** is a 9px
glowing dot + uppercase label ("wire live"); failure turns dot and text
oxblood and drops a sticky oxblood **wire-banner** (`role="alert"`) under the
masthead: "WIRE DOWN — showing the last board from Ns ago." Never silence.

### Tabs (Rooms)
File-drawer tabs, top-rounded, uppercase .86rem labels. Rest state sits on a
dark gradient in dim ink; the active tab brightens to brass-bright, merges
with the page ground, and carries the 2px inset brass top bar plus
`aria-current="page"`. ≤900px the row stretches and scrolls in its own
container.

### Buttons
Confident hardware, uppercase, 700. **Primary**: brass gradient
(`linear-gradient(180deg, brass-bright, brass 55%, #b58c47)`), ground-dark
text, brass-deep border, 5px radius, 15px 22px padding, min-height 46px, a
black drop + brass underglow + warm inset top edge. Hover brightens 7%;
active presses down 1px. **Ghost**: transparent with a hairline border, dim
ink; hover re-inks and warms the border to brass-deep. **Small** variant:
10px 14px, min-height 38px, .8rem. Disabled is 45% opacity. In-flight
buttons embed the 12px Courier-adjacent spinner ("Working…", "The hands are
moving…"). Approval actions stay ≥44px tall and go full-width on the phone.

### Filter Chips
Mobile-only position filter (hidden until ≤900px): pill chips, uppercase
.84rem, panel ground with hairline border; hover warms the border; active is
solid brass with ground text and `aria-pressed`.

### Position Tag
The categorical key: an 8px, 2px-radius swatch in the position hue (via
`--pc`) beside the written position code in .7rem 700 uppercase dim ink. Used
in column heads, call meta, shelf, ticker, and lineup rows; on paper the
label re-inks to paper-faint. Never the swatch without the word.

### Board Row & Tier Bands
The board's unit: a two-line row under a hairline — line 1: rank/pick stamp,
bold name (ellipsized), oxblood injury tag (`icon + "OUT"/"Q"`); line 2: team
· bye in faint caps, then Courier figures right-aligned (`vbd` bold, `adp`)
with gothic micro-labels; line 3: the **survival meter**, a 3px track filled
with a brass gradient plus a Courier percentage. Rows hover to panel-2. **Tier
bands** split each column: brass .68rem uppercase .2em labels between hairline
flanks ("TIER 2"; the untiered remainder is "The deep shelf" in faint ink).
**Picked** rows drop to 50% opacity, hide the meter, and take the -1.1°
oxblood strike plus a Courier stamp ("P34"); **yours** stay full-opacity on a
brass wash with a solid-brass "MINE · P34" stamp and a brass-deep strike.
This is the home of the room's one authored motion moment (see the rule
below): the strike draws left-to-right (`strike`, .5s) and the stamp slams
and settles (`settle`, .55s, 1.7→.95→1 scale with counter-rotation), both on
`cubic-bezier(.16, 1, .3, 1)`, applied via `.just-picked` for 1.4s.

### Pick Ticker
The wire prints to paper: a single-row tape of recent picks on the paper
gradient with the paper drop shadow, scrolling horizontally in its own
container with paper-calibrated scrollbars (#b9a87e thumb on paper-2 track).
Courier .84rem in a dark tape ink (#4a3d28), pick numbers in paper-faint,
your picks in bold paper-bad. Position tags re-ink for paper.

### Ledger Sheet
The world's core material: a paper-gradient page (3px radius, paper drop
shadow, warm inset top edge) with the red margin line and a 58px left gutter
(44px/32px ≤900px). Everything on it uses paper inks, and `::selection`
inverts (paper-ink behind, paper text). **Rule rows** (House Rules): 600-weight
paper-ink names, Courier threshold notes in paper-faint, hairline `paper-rule`
separators, and a 46×24px **paper toggle** — tan track and khaki knob when
off, brass-deep track with brass-bright knob when on, focus ring re-colored to
paper-bad. **Ledger lines** (audit): Courier .84rem — timestamp, rec number,
and a bold step name inked paper-ink, paper-ok, or paper-bad by outcome.
Empty state stays in voice: "Nothing on the books yet."

### Lifecycle Stepper
The actuation state machine drawn as a chain: five Courier .72rem uppercase
tags — proposed → notified → approved → executed → verified — joined by 18px
hairline links, never wrapping (scrolls if it must; compresses at 420px so
all five fit a 390px line). States: done = brass text/border; now = solid
brass; verified = solid lamp; failed = solid deep-oxblood with white text,
replacing the terminal tag. The stepper always shows all five states — the
promise, not just the position.

### Tables
Walnut furniture (lineup pairs, waiver table): 6px plates with gradient
header bands, .72rem uppercase faint-ink column heads, hairline row rules,
hover to panel-2, Courier numerals right-aligned. Lineup rows wash lamp-green
(`rgba(88,160,108,.08)`) for players coming in and oxblood
(`rgba(207,97,82,.07)`) with a line-through name for players going out.
Waiver bids are brass pills in Courier 700; big swings carry the marigold
flag + "BIG SWING — CONFIRM TWICE".

### Iconography
Three hand-drawn inline SVGs only — cross (injury), hold (triangle), flag —
16-unit grid, `stroke="currentColor"`, stroke-width 1.8, drawn at 10–14px,
`aria-hidden`, always beside a word. No icon fonts, no glyph dumps, no
icon-only meaning.

### Browser Surfaces
The theme reaches every native surface, per material. Walnut: brass
`::selection` with ground text, brass caret, 10px scrollbars (ground track,
line thumb, brass-deep hover), 2px brass `:focus-visible` outline (offset
2px, 2px radius), and a brass skip-link plate on focus. Paper: inverted
selection, paper-calibrated scrollbar, paper-bad focus ring on the toggle.
`color-scheme: dark` is declared; there is deliberately no light theme — the
back room has one lighting condition.

### Empty & Loading States
Always in the house voice, dry and in-world, never blank: "TAPPING THE
WIRE…" (Courier loading plate), "Reading the room…", "Working the phones…",
"Nothing on the shelf yet — your first pick lands at #7", "Nobody on the
street worth a dollar this week", the dashed-border "draft complete" plate.
Failure states name the fallback ("Set it in Sleeper") — never a dead end.

### Named Rules

**The One Authored Moment Rule.** The pick-stamp strike-and-settle is the
room's single piece of theater, with the on-the-clock brass glow as its
supporting light cue. Everything else moves functionally or not at all:
≤150ms filter/transform micro-transitions on buttons and toggles, one 12px
spinner. `prefers-reduced-motion` removes the theater entirely (animations
off, transitions off, spinner slowed to 1.5s) — the room must read perfectly
as a still photograph.

## Do's and Don'ts

### Do:
- **Do** pair every position hue with its written label (`.pos` swatch + code) and every status color with a word or icon — color alone carries no meaning anywhere.
- **Do** set every number in Courier Prime — figures, stamps, timestamps, bids, percentages — with gothic micro-labels beside them.
- **Do** use `minmax(0, 1fr)` for flexible tracks and `min-width: 0` on anything that hosts a wide strip; let the ticker and stepper scroll inside their own containers.
- **Do** switch to the paper ink set (`paper-ink`/`paper-faint`/`paper-ok`/`paper-bad`, all ≥4.5:1 on #e0d2a9) the moment content sits on a sheet or tape, including selection, scrollbars, and focus rings.
- **Do** keep approval actions at least 44px tall (46px built), full-width on the phone, and reachable with one thumb.
- **Do** make every degraded state visible and worded in the house voice — a wire-down banner with the data's age, never a silent stall.
- **Do** light from above: top-lit gradients, warm inset top edges, soft black drops; save brass and lamp glow for state.

### Don't:
- **Don't** set anything but the BOOTLEGGER wordmark in Limelight.
- **Don't** use lamp green, marigold, or oxblood as series/position/decoration colors, or position hues as status colors.
- **Don't** let the page scroll horizontally, ever — `overflow-x: clip` stays on `body`, and a bare `1fr` track is a bug waiting for a wide ticker.
- **Don't** put walnut-surface inks or accents on paper, or paper inks on walnut — crossing materials means switching ink sets.
- **Don't** drift toward the refused worlds: no white cards, no cool grays, no neon-terminal chrome, no borderless floating dashboards.
- **Don't** add a second theatrical animation; new motion is functional, ≤150ms, and dies under `prefers-reduced-motion`.
- **Don't** write cute copy near money or kickoffs — the voice is dry, confident, a little clandestine.
