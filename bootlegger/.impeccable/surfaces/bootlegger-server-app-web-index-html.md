---
version: 1
slug: "bootlegger-server-app-web-index-html"
primary_target: "bootlegger/server/app/web/index.html"
related_targets: []
---

Scope: the web fallback surface (bootlegger/server/app/web/) — The Board,
This Week, Waivers, The Parlor, The Ledger. Visitor mode: Operate.

Audience/job: the league owner alone, under time pressure — draft night
(60s decisions) and Sunday mornings (one-thumb approvals on the phone).
A Bills fan; the room is allowed to know it.

Action/task: read the board at a glance; approve/snooze/ignore a lineup rec;
watch it verify. Proof/content: live engine output (tiers, VBD, survival,
kickoff/weather/implied-total context, suggestion reasons), the rec state
stepper, the audit ledger.

Advisory layer (added 2026-08-26, `engines/advisories.py`): the shelf carries
bye-collision and same-team findings under its slot chips, a pressure row sits
on the ground above the paper ticker, and the room's scoring priors close the
shelf. All three are READINGS, never bids — nothing here reaches the suggestion
score or survival, and `tests/test_advisories.py` pins that boundary. Findings
use marigold for caution and lamp for a strength; Bills red is deliberately
absent, since a bye collision is a caution and red stays reserved for the wire
going down and a swap going wide right.

The beat (added 2026-08-26, `alerts.py` + `engines/wire.py`): a news feed rides
This Week in every state, INCLUDING pre-draft where it is the only live thing
that room has to say. It is called THE BEAT, never "the wire" — this product
already uses "the wire" for the Sleeper connection ("CIRCLE THE WAGONS — the
wire is down"), and a board that says the wire is down about two different
things has taught its owner nothing. The feed is a paper surface (the game
programme sheet, same material as the ledger and the Scout's File) and switches
to that sheet's ink set entirely: `paper-bad` for out/doubtful, a dark amber for
questionable/practice, `paper-ok` for a role change. Your own men are marked by
a heavier royal margin rule, never by a second colour. On lineup rows the beat's
word is a chip on the CONTEXT line, softer than `.hurt` — the beat is faster
than Sleeper's tag, so the API's confirmed word must still outrank it.

The matchup (same date, `engines/matchup.py`): a plate above the lineups
carrying both projected scores in the typewriter face, a royal odds meter, and
which of three lineups the week actually wants. Royal here is attention, not
status — an odds bar is a reading, so it never borrows lamp green or Bills red.
The alternative lineup only renders when it both differs from the
expected-points one AND moves the odds by at least two points.

Constraints: no build step, no CDN, fonts self-hosted; every failure state
visible (wire banner, and the beat's own staleness/missed-item line); works offline against demo seed; reduced-motion
strips every character animation; figures stay Courier Prime; position hues
never appear without written codes.

Chosen direction (user-pinned 2026-08-25, replacing Chalk & Turf):
ORCHARD PARK NIGHT — a Bills night game as a broadcast-grade interface.
Stadium navy ground with faint yard lines, electric royal for everything
touchable, Bills red strictly for trouble, white game-program paper for the
ledger surfaces. Anton (italic, red drop) for the house name and room
titles; Barlow Semi Condensed as the working voice; Permanent Marker ONLY
for bedsheet-banner meme stamps. One zubaz stripe (masthead + report card).
Fun layer, full send: the buffalo idles on The Call and stampedes on your
clock; the telestrator draws the route; your pick hurdles the row; THE TABLE
in the shelf corner folds when your pick lands ("FOLDED" tag) and always
stands back up; MAFIA pick stamps; "CIRCLE THE WAGONS" wire banner; a failed
swap goes "WIDE RIGHT". Memorable moment: the on-the-clock stampede plus the
table folding when the pick verifies.

Motion grammar: broadcast cut — rooms rise-settle in 200ms, rows stagger
capped at 180ms, all transform/opacity, reduced-motion-safe.

Unresolved: the draft-overlay extension does not yet mirror the advisory
layer, so the shelf findings and pressure row exist on the web surface only —
a known divergence from this file's authority rule, not an accident; the same
is now true of the beat, the matchup plate and the room read, which exist on
the web board and in neither the extension nor `mobile/`;
on-the-clock stampede not yet captured live (2s demo window);
report-card zubaz strip unverified post-draft in a screenshot (same token as
the verified masthead strip).
