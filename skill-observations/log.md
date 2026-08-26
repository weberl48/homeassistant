### Observation 1: WebSearch snippets as fallback when WebFetch gets 403
**Status:** OPEN
**Date:** 2026-08-25
**Session context:** Competitive research on fantasy-football tools (Bootlegger grading)
**Skill:** New skill candidate: web-research-fallbacks (or fold into a future research skill)
**Issue:** fantasysp.com and support.fantasypros.com return 403 to WebFetch (bot blocking), but the same pages' content is fully retrievable via targeted WebSearch queries whose result snippets quote the page text.
**Suggested improvement:** When WebFetch 403s, immediately re-query WebSearch with exact feature/product terms plus the domain instead of retrying the fetch or hunting for mirrors.
**Principle:** Search-index snippets are a legitimate content channel for bot-blocked pages; pivot to them on first 403.

### Observation 2: Code review of a live branch must re-snapshot HEAD before reporting
**Status:** OPEN
**Date:** 2026-08-25
**Session context:** max-effort /code-review of bootlegger 3140be8..HEAD; a new commit (Vegas lines + practice reports) plus uncommitted changes landed mid-review while 10 finder agents were analyzing the frozen patch
**Skill:** code-review (built-in slash skill; process note)
**Issue:** The review scope was frozen to a patch file at t0. The branch advanced mid-review, so finder line numbers drifted and the newest ~200 lines had zero finder coverage; without an explicit re-check the report would have cited stale lines and missed the delta entirely.
**Suggested improvement:** In the review process (Phase 0/Phase 3), after finders return: re-run `git log -1` + `git status`, diff the frozen patch against the current tree, review any delta personally in the sweep phase, and re-map all reported line numbers to the current tree before assembling output.
**Principle:** A review of a moving target is only valid at assembly time — snapshot for the fan-out, but re-anchor scope and line numbers at the end.

### Observation 3: `run` skill should check for a venv before assuming the README's works
**Status:** OPEN
**Date:** 2026-08-26
**Session context:** "launch the site" — booting the Bootlegger FastAPI demo server in a git worktree so the in-flight web redesign could be viewed.
**Skill:** run
**Issue:** The run skill's project-skill probe found nothing, so it fell back to the README. The README's quickstart assumes `server/.venv` exists; in a fresh worktree it does not, and neither did the main checkout. Roughly four minutes went to creating a venv and installing 34 packages before the app could start. The skill's fallback table jumps straight to "launch the server," with no step that verifies the interpreter/dependency environment first.
**Suggested improvement:** In the "Otherwise: match the shape, use the pattern" section, add a pre-flight line before the table: confirm the runtime environment exists (venv/node_modules/build artifacts) and provision it if not — a missing environment is the most common reason a documented launch command fails, and it is cheap to check. Also worth noting that in a git worktree, sibling checkouts do not share gitignored env directories.
**Principle:** A documented run command encodes the author's *already-provisioned* machine. The gap between "the command is correct" and "the command works here" is almost always the environment, so verify it before running, not after the traceback.

### Observation 4: `run` skill's "drive it" step needs a selector-robustness note
**Status:** OPEN
**Date:** 2026-08-26
**Session context:** Driving the Bootlegger web UI's five tabs with Playwright to confirm the redesign rendered.
**Skill:** run
**Issue:** First driver attempt clicked tabs via `get_by_text(label, exact=True)` using labels harvested from `innerText`. Every click timed out, because each tab's innerText is two lines ("THE BOARD\nthe draft") and `exact=True` on a multi-line string matches nothing. Cost one full retry cycle. Separately, a stray click during driving mutated demo state (a pending approval executed), which muddied the state the user would land on.
**Suggested improvement:** In "Drive it, don't just launch it," add: prefer locating interactive elements by role/selector and index over harvested text — text from `innerText` carries newlines and decorative sub-labels that break exact matches. And note that driving a stateful app mutates it; if the app has a seeded demo state the user is meant to see, either drive a throwaway instance or reset afterward.
**Principle:** Driving the app is an intervention, not just an observation — both the locator strategy and the state left behind are part of doing it correctly.

### Observation 5: `run-skill-generator` assumes a Linux container; needs a host-adaptation step
**Status:** OPEN
**Date:** 2026-08-26
**Session context:** Authoring `bootlegger/.claude/skills/run-bootlegger/` on Windows 11 / PowerShell.
**Skill:** run-skill-generator
**Issue:** The generator is written throughout for a headless Linux container — `apt-get` prerequisite lines, `xvfb-run`, tmux `send-keys`, and "for a web app the driver already exists: `chromium-cli`, no custom driver needed." On this Windows host none of that holds: `chromium-cli` is absent, tmux is absent, and the correct driver was a committed Playwright Python script. The instruction "for web apps the heredoc in SKILL.md IS the harness" would have produced a skill that does not run here. I adapted, but nothing in the skill signals that adaptation is expected, so a less careful pass would ship a Linux-shaped skill onto a Windows repo.
**Suggested improvement:** Add a short "First: identify the host" step before §1 Discover — detect OS/shell, then state the substitutions (`chromium-cli` → committed Playwright driver; `xvfb-run` → not needed headless; tmux → harness background execution; `apt-get` → the platform's installer or "none required"). The project-type table's "Browser-driven" row should read "chromium-cli **where available**, else a committed Playwright driver."
**Principle:** A skill that hard-codes one platform's tooling silently degrades into wrong instructions on another. Name the host-dependent assumptions and give the substitution explicitly, rather than letting each session rediscover them.

### Observation 6: Generator's §4 Verify step earned its keep — keep it non-optional
**Status:** OPEN
**Date:** 2026-08-26
**Session context:** Same — verifying the finished run-bootlegger skill by following it literally.
**Skill:** run-skill-generator
**Issue:** Following my own SKILL.md verbatim surfaced a documented command that silently produced empty output: I had written the rec-status check as `"status": "notified"`, but the API field is `state`. The PowerShell one-liner returned nothing rather than erroring, so it would have read as "server not up" to the next agent. Only literal execution caught it — reading the doc back would not have, because the claim was plausible and the failure mode was silence, not an exception.
**Suggested improvement:** In §4 Verify, add: prefer commands that fail loudly over ones that can return empty; when a documented check reads a field out of a JSON payload, run it and confirm it prints the expected value, since a wrong field name yields empty output rather than an error. Worth calling out as the single highest-yield check in the whole verify pass.
**Principle:** The dangerous doc bug is not the command that errors — it is the one that quietly returns nothing and gets read as a different failure. Verification must assert on output, not just on exit status.

### Observation 7: A green test written from the same mental model as the fix proves nothing about the real system
**Status:** OPEN
**Date:** 2026-08-26
**Session context:** Fixing the Bootlegger demo "PICK FEED STALE" false positive — `drafts.updated_at` never refreshed while the sim ran.
**Skill:** superpowers:test-driven-development (and superpowers:verification-before-completion)
**Issue:** I ran a clean TDD cycle: wrote a failing test ("recording a pick refreshes the heartbeat"), watched it fail for the right reason, fixed `record_pick`, watched it pass, ran the full suite — 68 passed. By every checkbox in the TDD skill, done. Then I drove the actual running app and the banner still fired: the sim deliberately idles 16s on the user's clock, longer than the UI's 10s threshold, so a heartbeat that only moves when a pick lands still false-alarms. The test and the fix shared one wrong assumption — that the heartbeat means "a pick landed" — so the test could never catch it. The real semantics (visible in live mode's `etl_draft_picks`, which stamps on *every poll* regardless of picks) meant the fix belonged in `tick()`, not `record_pick`.
**Suggested improvement:** In TDD's "Verify GREEN" section, add a step after "all tests pass": for a bugfix, reproduce the *original user-visible symptom* against the running system, not just the test. If the symptom was observed in a running app, the fix is not verified until it is re-observed absent there. Also worth a line in the RED step: when a bug involves a value another component *interprets*, read that consumer's contract (threshold, cadence, semantics) before writing the assertion — the test's assumption is the thing most likely to be wrong.
**Principle:** A test authored by the same reasoning that produced the fix inherits its blind spot. Only an independent observation — the real symptom, at the real surface — can falsify a shared assumption.

### Observation 8: WebFetch is blind to client-rendered SPAs — decompile the bundle
**Status:** OPEN
**Date:** 2026-08-26
**Session context:** Competitive teardown of draftdoctor.app (a Next.js fantasy-draft
app) to find features worth borrowing for Bootlegger.
**Skill:** New skill candidate: `competitive-teardown` (or an IMPROVE note on
`stress-test-findings` Move 4 — "code says what should happen, the artifact says
what did").
**Issue:** WebFetch on the app's own sample-report page returned "no Draft Health
Report, grades, tiers, ADP, or VBD metrics" — a false negative, because the page is
client-rendered and WebFetch only sees the HTML shell. The real feature taxonomy
(11 diagnosis categories, 13 named signals, 3 severities, every user-facing string)
came from `curl`-ing the page, extracting `/_next/static/chunks/*.js` srcs, downloading
them, and grepping for `category:"…"`, `signal:"…"`, `severity:"…"`, and quoted
sentence-shaped strings. That took four commands and produced a complete answer where
two WebFetch calls produced a misleading one.
**Suggested improvement:** Codify the sequence — (1) curl raw HTML, (2) if the body
is a shell, extract script srcs, (3) download chunks, (4) grep for object-literal keys
that name domain concepts (`category:`, `id:`, `severity:`, `type:`) and for
`"[A-Z][^"]{20,140}"` to harvest UI copy. Also: never accept a WebFetch "feature not
present" as evidence of absence on a JS app — that's an unproven negative.
**Principle:** An absence claim is only as good as the surface you could actually
observe. When a fetch tool reports "not found," first prove the tool could have seen
it — the same discipline `stress-test-findings` applies to data findings applies to
recon.

### Observation 8: Probe data availability BEFORE the clarifying question, not after
**Status:** OPEN
**Date:** 2026-08-26
**Session context:** Designing a league overview page for Bootlegger; the user chose between three shapes.
**Skill:** superpowers:brainstorming
**Issue:** The skill's bounded path is "explore project context → ask clarifying questions". I read the schema and ingest layer first and found that `etl_rosters` silently drops Sleeper's `settings` block, so no win/loss data existed anywhere in the DB. That single fact turned a vague question ("what should be on it?") into a real one with priced options — "scouting view, free" vs "standings, needs a schema + ingest change and reads 0-0 until Week 1". The user could choose knowingly. Had I asked first, "standings" would have looked free and the cost would have surfaced mid-implementation, where the skill's own ratchet rule would have forced a re-classification.
**Suggested improvement:** In the bounded and architectural checklists, sharpen step 1 from "explore project context — check files, docs, recent commits" to explicitly include: verify the data/APIs the feature would need actually exist, and price any that don't. Then state those costs inside the options you present. A clarifying question whose options carry no cost estimate invites the user to pick the expensive one by accident.
**Principle:** A choice offered without its price isn't a real choice. Explore far enough to cost each option before asking which one the user wants.

### Observation 9: A sampled regression test can pass against corrupted state
**Status:** OPEN
**Date:** 2026-08-26
**Session context:** Implementing an ADP-residual "position run" detector in
Bootlegger, with a CI test asserting it stays quiet on a draft that contains no
runs by construction.
**Skill:** `superpowers:test-driven-development` (its `writing-good-tests.md`
rules), with a secondary tie to `stress-test-findings`.
**Issue:** The test advanced a simulated draft with `_run_draft(conn, n)` for n
in (12, 24, 36, ...), and that helper replayed picks from 1 every call. Because
the simulator skips already-taken players, the second pass handed pick 1 a
different player and scrambled the draft — so the test was asserting the
property against garbage and passed. Sweeping every window instead revealed the
detector firing in 13.5% of windows, which forced a threshold recalibration
(3.0 -> 4.0). Two independent defects hid behind one green check: a
state-corrupting fixture helper, and a sampled assertion too sparse to notice.
**Suggested improvement:** Add to `writing-good-tests.md`: after writing a test
that asserts an absence ("stays quiet", "never fires", "no errors"), prove it
can fail — flip the threshold/config it depends on and watch it go red before
trusting the green. And for tests that drive a stateful simulator, assert the
state advanced as intended (here: `COUNT(*) FROM draft_picks == n`) rather than
assuming the helper is idempotent.
**Principle:** A green absence-assertion is the easiest test to fake and the
hardest to notice faking. The Iron Law's "watch it fail" applies not just when
writing the test but whenever it guards a tunable constant — the constant is
the mutation that proves the test works.

### Observation 10: Archived run artifacts beat commit messages — and successive runs separate "cherry-pick" from "real fix"
**Status:** OPEN
**Date:** 2026-08-26
**Session context:** Grading Bootlegger against FantasyPros/Draft Sharks/etc. The load-bearing draft-algorithm claim ("beat FP ECR 7/8 sims, +2.8–4.1%") existed only in a commit message.
**Skill:** stress-test-findings
**Issue:** Move 4a says the persisted output is the assumption-independent proof for "the code does X". Here the deploy target held FOUR archived runs of the same harness (`/data/h2h.out … h2h4.out`). Reading only the latest would have confirmed the claim; reading all four did something stronger — the *opponent's* total was near-constant across runs (2157.9–2159.3) while ours jumped 2178→2232 at one run boundary. That pattern distinguishes "a real engine fix landed" from "they ran it four times and reported the best", which is the sharp reviewer's competing hypothesis and the one Move 3 asks for.
**Suggested improvement:** In Move 4's "(a) persisted output" bullet, add: when several archived runs of the same harness exist, read them ALL, not the latest — a control series that stays flat while the treated series steps is assumption-independent evidence the delta is a change, not variance; a treated series that wanders with no flat control is evidence of cherry-picking. Add to Move 3 as the standard distinguishing test for "best-of-N reporting."
**Principle:** One artifact proves what happened; a series of artifacts proves whether it was caused.

### Observation 11: A 200 response is not evidence a new URL parameter took effect
**Status:** OPEN
**Date:** 2026-08-26
**Session context:** Adding weekly CBS projections to Bootlegger by parameterising an existing season scraper with a `week` segment.
**Skill:** stress-test-findings
**Issue:** The week-3 URL returned HTTP 200 and 410 well-formed rows, so every check a normal integration does passed. The rows were SEASON totals (Josh Allen 419 pts) — the site ignored the new segment. Had the consensus not carried an independent weekly-median sanity band, every lineup call for the week would have been an order of magnitude high, silently. The request succeeded; the parameter did nothing.
**Suggested improvement:** In Move 4 ("assumption-independent evidence"), add a line for parameterised fetches: when a new dimension is added to an existing request (week, region, scoring, date), the proof that it took effect is a change in the OUTPUT's own distribution — scale, row count, or a value that must differ — never the status code or a successful parse. State the expected shift before fetching and check it.
**Principle:** A source that ignores your parameter answers 200 and hands you the wrong question's answer.

### Observation 12: A rule written through a code generator needs its escaping checked, not just its logic
**Status:** OPEN
**Date:** 2026-08-26
**Session context:** Adding regex guards to Bootlegger's news classifier by rewriting the module through a python heredoc.
**Skill:** New skill candidate: generated-code-escaping (or a rule inside an existing implementation skill)
**Issue:** Two word-boundary escapes were written through a NON-raw Python string in the generator, so each became a literal backspace (chr 8). The patterns compiled, the module imported, the source looked correct in every render — the control character is invisible — and both new guards silently matched nothing. Three debugging rounds; found only by printing the pattern with repr. It then recurred immediately, in this same session, writing a one-line pointer into MEMORY.md the same way.
**Suggested improvement:** Any generated edit whose inserted text contains a backslash must use a raw string, or be verified afterwards. The check is one line and worth making standard: assert the written file contains no control characters outside newline/carriage-return/tab. Better: prefer the Edit tool over a generator script for any content containing backslashes — it does not re-interpret them.
**Principle:** A generator can produce a file that is wrong in a way no reader can see. Check the bytes, not the rendering.

### Observation 14: A long-lived dev server serves NEW static assets over OLD Python
**Status:** ACTIONED — bootlegger/.claude/skills/run-bootlegger/SKILL.md, Gotchas (2026-08-26)
**Date:** 2026-08-26
**Session context:** Grading the Bootlegger web UI. A uvicorn on :8484 was already
listening from an earlier session. `/assets/app.js` hashed identical to the
worktree file, so the server looked current — but `/api/wire?limit=30` returned
404 on every tab, six console errors, and THE BEAT rendered as an outage.
**Skill:** `bootlegger/.claude/skills/run-bootlegger` (Gotchas / Troubleshooting)
**Issue:** `StaticFiles` reads from disk per request, so HTML/CSS/JS are always
current; the Python modules are whatever was imported at process start. A server
started before a commit that added routes serves a NEW frontend calling OLD
routes. The failure presents as a broken feature in the code under review, not
as a stale process — I nearly logged "the wire endpoint 404s" as a defect.
**Suggested improvement:** Add a Gotcha: "A running server is not proof it is
running THIS code. Hashing `/assets/app.js` against the worktree proves nothing
— static files are read per request. Verify a route added by a recent commit
(`curl -o NUL -w '%{http_code}' .../api/<newest-route>`) or restart before any
review. `driver.py check` surfaces this as generic 404 console errors."
**Principle:** When a process and its assets have different reload semantics,
asset freshness is not evidence of process freshness — probe the half that is
pinned at startup.

### Observation 15: Committed review screenshots age into wrong findings
**Status:** ACTIONED — bootlegger/.claude/skills/run-bootlegger/SKILL.md, Gotchas (2026-08-26)
**Date:** 2026-08-26
**Session context:** Same review. `bootlegger/.impeccable/review/*.png` were last
written at commit db7c753, four commits back. `parlor-desktop.png` showed the
duplicate-deal problem (one deal repeated under every throw-in) that commit
050e467's `trades.shortlist()` had since fixed — the live app shows 8 distinct
deals and "70 packages found; 8 worth reading". Grading from the committed PNGs
would have reported a fixed bug as live.
**Skill:** `bootlegger/.claude/skills/run-bootlegger` — relates to the existing
`read_live_state_before_designing` memory, which covers config drift, not
committed rasters.
**Suggested improvement:** In the run-bootlegger skill, next to the `shots`
command: "Committed review PNGs under `.impeccable/review/` are provenance for
the commit that wrote them, not the current build. Before grading or critiquing
UI, `git log -1 -- .impeccable/review/` and recapture if any commit touched
`server/app/` since."
**Principle:** A screenshot is a dated artifact. Treat it like any status claim —
if it carries no date, verify it before it carries an argument.

### Observation 16: Check auto-memory for the user's environment facts before designing logic that depends on them
**Status:** OPEN
**Date:** 2026-08-26
**Session context:** Building Bootlegger's League room — a per-position "surplus" read for a fantasy league.
**Skill:** superpowers:brainstorming (context-exploration step)
**Issue:** The surplus rule counts only dedicated roster slots (RB=2, WR=2), deliberately excluding FLEX because FLEX belongs to no single position. That is correct for the demo fixture, which has one FLEX. The user's real league has **two**, so a seat legitimately starting three or four RB/WR reads as "deep at RB" when it is not. The fact was not obscure: `bootlegger_deployment.md` in auto-memory already recorded the exact league shape — "QB/2RB/2WR/TE/**2 FLEX**/K/DEF+5BN" — before the rule was written. I explored the repo thoroughly (schema, ingest, API, frontend) and never opened the memory that described the production environment the feature targets. The demo fixture structurally cannot surface the gap, so tests passed and live verification (pre-draft, empty rosters) exercised none of it.
**Suggested improvement:** In the context-exploration step, add: when the feature's behavior depends on the user's real-world configuration (league settings, device inventory, account tiers, schema variants), read the relevant auto-memory *and* the live config before choosing thresholds — not only the repo. The repo shows what the code does; memory and live config show what it will meet. Pairs with [[Observation 8]]: that one says price each option before asking, this one says check what is already known about the target environment before designing against it.
**Principle:** A demo fixture encodes one shape of the world. Anything calibrated against it inherits that shape as a hidden assumption, and the fixture can never falsify it — so the check has to come from outside the fixture.

### Observation 17: An assertion that only holds when the system is idle fails when it matters
**Status:** OPEN
**Date:** 2026-08-26
**Session context:** Building `driver.py audit` for Bootlegger. Wrote a check
for a real defect — a live region announcing on every 1 Hz poll — as "zero
mutations across a 10s window".
**Skill:** New skill candidate: `writing-regression-gates` (or an addition to
`stress-test-findings`, which already attacks findings but not the ASSERTIONS
written to defend them).
**Issue:** "Zero mutations" is only true on an idle board. During a live draft
the clock legitimately changes every couple of seconds, so the check would
have failed for the system doing its job — on draft night, the one occasion
the check exists to protect. It passed in testing purely because the 10s
window happened to land inside the demo's on-the-clock pause. The invariant I
actually wanted was one announcement per MEANING change, which holds in both
states.
**Suggested improvement:** When writing a gate, name the busy state as well as
the quiet one and check the assertion holds in both. A gate verified only
against a resting system encodes "nothing is happening" as if it were
"nothing is wrong". Verify in both directions: break the fix and confirm the
gate fails; exercise the busy path and confirm it does not.
**Principle:** An assertion is a claim about an invariant, not about a moment.
If it only holds while nothing is happening, it is measuring stillness.

### Observation 18: Fixture shape decides which bugs are reachable
**Status:** OPEN
**Date:** 2026-08-26
**Session context:** Same session. A 15-check gate passed 15/15 against the
local demo and failed the moment it was pointed at the live box — twice, on
two different defects.
**Skill:** `bootlegger/.claude/skills/run-bootlegger` has the app-specific note
already; the generalizable half belongs with test/gate guidance.
**Issue:** Bootlegger's demo seeds an EMPTY slip and a COMPLETED draft, and
rosters 168 of its 182 players. Three consequences, all invisible locally:
the slip's reorder buttons never render (they shipped at 20x22px); The Call
never carries a long name over a stat row (it painted 51px over the first
board column); and the free-agent pool is five men, where a percentile bug
that collapses the FAAB ladder at realistic pool sizes cannot appear. Each
was a real defect the fixture was structurally incapable of producing.
**Suggested improvement:** Before trusting a gate, enumerate what the fixture
CANNOT produce — empty vs populated collections, short vs long strings, small
vs large N, each lifecycle state — and either extend the fixture or run the
gate against a real instance. For the FAAB case the test ships its own
300-player fixture rather than relying on the demo's five.
**Principle:** A fixture is not a smaller version of production; it is a
different shape. Bugs live in the shapes it does not have.

### Observation 19: Observation 12 recurred — a generated regex needs a positive match, not a parse
**Status:** OPEN
**Date:** 2026-08-26
**Session context:** Same session, writing a token-drift check through a
Python generator script.
**Skill:** Existing entry — Observation 12 ("A rule written through a code
generator needs its escaping checked, not just its logic").
**Issue:** `\b` in the generator's string collapsed into a literal backspace
byte (0x08) in the written file, so the regex demanded a backspace before
every token name and matched nothing. The file parsed, imported, and ran
clean; the check simply reported "no shared tokens found" and I first went
hunting for a path bug. Three further repair attempts failed for the same
reason before switching to `bytes([8])` to avoid writing an escape at all.
**Suggested improvement:** Strengthen Observation 12's enforcement from
"check the escaping" to a concrete gate: after generating any regex, run it
once against a known-positive sample and assert a non-empty match, in the same
step that writes it. Parsing is not evidence; matching is.
**Principle:** A rule that cannot fire is indistinguishable from a rule that
found nothing. Only a known-positive sample tells them apart.
