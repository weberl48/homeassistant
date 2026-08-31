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

### Observation 20: /code-review's diff scope silently became a whole day's work
**Status:** OPEN
**Date:** 2026-08-27
**Session context:** Ran `/code-review` at max effort on the bootlegger branch. Phase 0's `git diff @{upstream}...HEAD` resolved to 19 unpushed commits — 7,600 insertions across 52 files — because origin had simply not been pushed since morning, not because a PR of that size existed.
**Skill:** code-review
**Issue:** Phase 0 assumes `@{upstream}...HEAD` is a PR-sized change. On a long-lived local branch it silently expands to everything since the last push. Ten finder angles at 8 candidates each were then spread thin over a diff ~20x the intended size, and the ≤15-finding cap forced real correctness bugs to compete with cleanup findings from files nobody had asked about. Nothing in the skill measures the scope before fanning out.
**Suggested improvement:** In Phase 0, after computing the range, print `--shortstat` and the commit count. If the diff exceeds a threshold (say >1,500 changed lines or >10 commits), state the measured scope in one line and either (a) ask which subset to review, or (b) default to the newest commit(s) plus the working tree and say so explicitly. Also: `git status --porcelain` lists UNTRACKED files that `git diff HEAD` does not show — Phase 0 should name them as in-scope, since a brand-new file is often the riskiest thing in the change set (it was here).
**Principle:** A review that does not measure its own scope before fanning out spends its budget uniformly on a non-uniform diff.

### Observation 21: A passing gate is not a substitute for a fresh adversarial read
**Status:** OPEN
**Date:** 2026-08-27
**Session context:** Declared News and Trades "complete" on the strength of 245 green tests and 17 green audit checks, all verified against the live box. A `/code-review max` run immediately afterwards confirmed fifteen bugs, six of them introduced by that same day's work — including a null-spread that blanked This Week on exactly the state draft night produces, and a push path that sent four identical DND alarms for one story.
**Skill:** New skill candidate: `review-before-declaring-done` — or an addition to `verification-before-completion`, which currently asks for evidence that the work runs, not for evidence that it is right.
**Issue:** Every gate in that suite was written by the same person who wrote the code, immediately after fixing a defect. So the gates encode the failure modes already imagined and are structurally blind to the rest — three of this session's defects were only reachable on the live board, and none of the six regressions touched an existing assertion. "Tests pass" and "the gate is green" were both true and neither was evidence of correctness.
**Suggested improvement:** Before declaring a milestone complete, run an adversarial review of the day's diff — not the whole branch. Treat the completion claim as the trigger, the way a challenged finding triggers stress-test-findings. The cost is one review; the alternative here was shipping a draft-night crash.
**Principle:** A gate proves you did not break what you thought of. Only a reader who did not write the code can tell you what you did not think of.

### Observation 22: Counterfactual replays must re-run each advisor against its OWN state
**Status:** OPEN
**Date:** 2026-08-30
**Session context:** Bootlegger draft post-mortem — scoring four advisors (engine "THE CALL",
FantasyPros ECR, session assistant, actual picks) on best-legal-starting-lineup points.
**Skill:** stress-test-findings
**Issue:** The replay truncated the draft to each pick and re-ran the board against the ACTUAL
roster state. That re-recommends any player the user declined, creating phantom duplicates. The
post-mortem flagged this for exactly one pick (Jacobs at 65) and treated it as a one-off
"artifact". The same mechanism actually fired at four more places — SESSION@137 (Meyers,
re-recommended after being declined at 128), ECR@128 (Robinson, declined at 113), and CALL@152/@161
(a second K and DEF, which the engine's own `roster_need_multiplier` in
server/app/engines/draft.py would have scored 0.05, not 1.0). A caveat noticed once was assumed to
be local when it was systematic.
**Suggested improvement:** In "The Skeptic's Pass" Move 2, add: when a caveat is granted at one
data point, enumerate every other point where the same mechanism could fire before accepting the
caveat as local. A one-off exception is a hypothesis about scope, and scope claims need the same
n>1 discipline as "always/never" claims (already asserted under Mindset).
**Principle:** An acknowledged artifact is more dangerous than an unnoticed one, because flagging
it once creates the false impression it has been handled.

### Observation 23: Rank advisors on startable output, never on raw roster sums
**Status:** OPEN
**Date:** 2026-08-30
**Session context:** Same post-mortem. FantasyPros graded the roster 1st of 12; following ECR
literally produced a roster with zero TEs, zero defenses, one RB and four QBs — three empty
mandatory slots and ~1,775 bench points, scoring 589 points BELOW the actual draft once lineup
legality was enforced.
**Skill:** stress-test-findings
**Issue:** The headline comparison ("ECR ranked this roster 1st") was computed on a quantity
nobody scores. Slot legality and positional redundancy inverted the ranking completely. The same
correction flipped two of three suspected engine defects: D1 (flex inflation) is a real mechanism
that cost ~0.5 points on this board, and D3's "a 179-pt receiver beats a kicker" was wrong here
because that receiver was the 6th WR on a roster starting two.
**Suggested improvement:** In Move 4 (assumption-independent evidence), add a line: when a metric
aggregates units that the system consumes selectively (starters vs bench, billable vs non-billable,
shipped vs staged), recompute under the selection rule before comparing. Prefer structural counts
that hold regardless of the underlying values — "ECR drafted zero tight ends in fifteen rounds"
survives any projection source.
**Principle:** A sum over items only some of which count is not a measurement of anything. Find the
selection rule and apply it before ranking.

### Observation 22: Grade a competing advisor by the code that produced its column, not by its brand
**Status:** OPEN
**Date:** 2026-08-30
**Session context:** Bootlegger draft post-mortem — a lens task asked me to "characterize FantasyPros ECR's decision procedure from its observable behavior" in a comparison table.
**Skill:** stress-test-findings
**Issue:** The "FantasyPros ECR said" column was not FantasyPros' advice. It was our own `brain.get_board()` field `experts_call`, computed as `min(ecr_rank)` over available players — five lines that read neither roster nor survival. Characterizing "ECR's decision procedure" from that column would have described our reduction and attributed it to a vendor. The premise named a brand; the evidence was a local function.
**Suggested improvement:** In Move 2 (name the load-bearing assumptions), add provenance as a standing assumption whenever a finding compares "us" to a named external system: *which code produced the external system's column?* If the comparison values are computed locally, the finding is about our adapter, not about them, and the claim must be scoped to the adapter.
**Principle:** A benchmark against an outside authority is only as external as the last line of code that touched it.

### Observation 23: Run the perturbation before believing a component is inert
**Status:** OPEN
**Date:** 2026-08-30
**Session context:** Same post-mortem. Three "engine defects" were proposed for confirmation, framed as if the engine were pure projections-VBD.
**Skill:** stress-test-findings
**Issue:** Two claims collapsed under cheap runnable tests that no amount of code reading had settled. (1) A constant added to a position pool cancels in `suggestion_score` to within `c x PROD(1-p)` — measured delta 0.0000 on a deep pool — so the alleged positional inflation never reaches the score. (2) Flattening and inverting the ECR blend completely reordered the board's top five, proving a component the finding had treated as absent was in fact load-bearing. Both took under two minutes to run.
**Suggested improvement:** In Move 4 (assumption-independent evidence), add the perturbation test as a named technique alongside "read the persisted artifact": when a finding says a term dominates or a term is absent, *set that term to a constant, and to its inverse, and re-run*. Ordering that does not move refutes "dominant"; ordering that moves refutes "absent".
**Principle:** Reading code tells you a term exists. Only perturbing it tells you whether the answer depends on it.

### Observation 24: A post-mortem brief's own factual premises must be re-derived from source data
**Status:** OPEN
**Date:** 2026-08-30
**Session context:** Bootlegger draft post-mortem — auditing the pick engine's divergences from the picks actually made. The task brief supplied premises ("the news wire had ZERO items on him, 168 items scanned"; "K1-to-K12 spread of only 15.2 points"; "at 65 the replay re-recommended Jacobs... treat as an artifact").
**Skill:** stress-test-findings
**Issue:** Two of the brief's stated premises did not survive contact with the database. "ZERO news items on Josh Jacobs" was false — the `news` table held 25 rows keyed to his player_id, twelve of them reporting a Commissioner's Exempt List placement ingested 5h24m before the draft started. That single check turned the headline conclusion inside out: the finding was not "the human had off-field information the system lacked" but "the system had the information and never routed it to the board." The "artifact at pick 65" caveat was also wrong — the player was genuinely still on the board there. The brief's numbers were sincere summaries from an earlier session, not adversarial, which is exactly why they were easy to accept.
**Suggested improvement:** In the "Separate proof from inference" section, add: when a brief, ticket, or prior session hands you premises, treat every *quantitative or absence* claim in it ("zero X", "N items", "no records") as inference, not proof, and re-derive it from source before building on it. Absence claims deserve priority — they are the cheapest to check (one query) and the most load-bearing when wrong, because a false absence removes an entire causal branch from consideration.
**Principle:** An inherited premise is someone else's finding. Absence claims are the ones that silently delete hypotheses, so they get checked first, not last.

### Observation 25: Check the working tree again before proposing a fix on a shared branch
**Status:** OPEN
**Date:** 2026-08-30
**Session context:** Designing one surgical engine fix from the draft post-mortem (Bootlegger). Mid-task, `app/brain.py` gained an uncommitted `flex_repl` / "ONE SLOT, ONE RULER" block from a second Claude session on the same worktree — the exact fix for the post-mortem's defect D1, which I had independently derived and was about to propose.
**Skill:** delegation-triage (and the memory `claude_worktree_concurrency`)
**Issue:** My anchors for a scripted patch stopped matching, which is the only reason I noticed. Had I patched by line number or proposed from my first read, I would have shipped a duplicate of work already in progress and clobbered it. The existing memory warns that HEAD can move; it does not warn that the *uncommitted working tree* can move, which is the more dangerous case because `git log` looks unchanged.
**Suggested improvement:** In `delegation-triage`, add to the pre-implementation checklist: re-run `git status --short` and re-read the target function immediately before writing the change, not only at task start — and treat a dirty file you did not dirty as a hard stop for that file. The check that catches violations is cheap: patch by content anchor with an assert, never by line number, so a moved file fails loudly.
**Principle:** On a shared worktree, a read is only valid for as long as you hold no write. Anchor-based edits are self-verifying; line-based edits fail silently.

### Observation 26: A three-defect brief may have one shared root, and the arithmetic will say which
**Status:** OPEN
**Date:** 2026-08-30
**Session context:** Post-mortem listed three suspected engine defects (flex VBD inflation, static room calibration, supply-blind need weighting) and asked for one fix.
**Skill:** stress-test-findings
**Issue:** Two of the three (D1 flex inflation, D3 supply-blind K/DEF) turned out to be the same root — raw VBD compared across positions whose replacement baselines sit at different point levels — surfacing at two different call sites. Separately, D1's headline evidence (a TE with 42% more VBD at half a point fewer) proved to be largely *cosmetic in the score*, because `suggestion_score` is a within-pool difference and a constant added to a whole pool cancels to within Π(1−survival). The brief's framing invited fixing the displayed number rather than the decision.
**Suggested improvement:** Add a step to `stress-test-findings`: before accepting a defect list as independent, ask whether the same quantity is being misused at more than one call site, and check whether the suspect term actually reaches the output — trace it to the decision, not the display. A term inside a difference over a shared pool is invariant to level shifts; only terms added at absolute level (here, the endgame nudge) can carry a baseline error into the ranking.
**Principle:** Find where a wrong quantity is used at absolute level. That is where it can hurt; everywhere else it may cancel.

### Observation 27: "Nothing changed between A and B" is a measurable claim, not a premise
**Status:** OPEN
**Date:** 2026-08-30
**Session context:** Adversarial regression-risk review of a Bootlegger draft-engine fix (`brain.py` endgame starvation guard). The proposal proved a bug by algebra over two observed picks: "at 128 urgency was 0.5 and DEF won; at 137 urgency was 1.0 and K won; nothing about the two pools changed between them, so only urgency could have flipped it."
**Skill:** stress-test-findings
**Issue:** The derivation was internally valid but rested on an unstated invariant — that the compared quantity is constant across the two observations. Instrumenting the actual code and replaying showed the invariant is false: the cliff term is computed against a horizon (`my_after`) that moves with each pick, so with the candidate pools *provably frozen* (same count, same top value at both picks) the measured cliff still tripled, 1.86 -> 5.75. Two inequalities in what were assumed to be two unknowns are actually two inequalities in four, and imply nothing. Re-arguing the algebra would never have found this; only measuring the intermediate quantity did.
**Suggested improvement:** In the "attack the load-bearing assumptions" section, add a named check: **invariance claims**. When a finding's proof compares two observations and asserts that everything but one variable held constant, treat that as the primary hypothesis to attack — identify every input to the compared quantity and measure it at both points, rather than reasoning about whether it plausibly moved. Time-, horizon-, and window-dependent terms are the usual culprits, alongside the existing timezone/effective-date/snapshot-timing entries.
**Principle:** A differencing argument is only as strong as its controls. "Only X moved" is an empirical claim about every other input, and it is usually cheaper to instrument the code and read the intermediate value at both points than to argue about it.

### Observation 28: A fix that changes nothing on all available data has not been verified
**Status:** OPEN
**Date:** 2026-08-30
**Session context:** Same review. The proposal reported "full suite 280 passed" and a reproduction "on a doctored demo endgame" as its verification.
**Skill:** stress-test-findings
**Issue:** A green suite and a purpose-built synthetic scenario are both consistent with a fix that is inert. Sweeping the only real draft in the repo (12 seats x 15 picks = 180 board builds, baseline vs patched) showed **0 changed recommendations** — the patch reinforced orderings the engine already had and never flipped one. The claimed target behaviour was unreachable in the data because the fix's precondition (the flat position carrying the higher standing value) never held. A synthetic scenario built by the fix's author to demonstrate the fix will demonstrate the fix; it is not independent evidence that the condition occurs in reality.
**Suggested improvement:** Add to the evidence-sufficiency section: when a change is justified by a specific past incident, sweep it across *all* comparable historical states available, and report the count of decisions changed. Zero changes on real data means the mechanism is unexercised and the diagnosis is unconfirmed, regardless of test-suite colour. Treat an author-constructed reproduction as a statement of the fix's precondition, then test whether that precondition is satisfiable in the real data.
**Principle:** Passing tests show a change is not harmful. Only a sweep over real historical states shows it does anything, and a synthetic repro authored alongside the fix is a restatement of the hypothesis, not a test of it.

### Observation 29: Executing a throwaway probe beats a verifier agent
**Status:** OPEN
**Date:** 2026-08-31
**Session context:** Max-effort `/code-review` of 9 commits on the bootlegger sleeper-design branch (1607-line diff, 10 finder angles + sweep).
**Skill:** code-review
**Issue:** The skill's Phase 2 prescribes verification by dispatching one verifier *agent* per candidate, which returns a reasoned CONFIRMED/PLAUSIBLE/REFUTED. In this review the decisive evidence for 7 of the top findings came instead from writing a ~20-line throwaway pytest against the repo's own `conn` fixture and running it: the news-suppression regression, the `sheet_as_of` MAX-vs-MIN inversion, the `read_out`/`adjust_adp` divergence, `draft_picks.pos` being NULL for 40/40 live picks, two wire-regex gaps, and the `DNR`-not-in-`INactive` cross-surface split. Each produced a failing assertion with real values — evidence a reasoning agent cannot match, at a fraction of the tokens (a verifier agent cost ~140k; a probe cost ~2k). Reading alone would have graded several of these PLAUSIBLE rather than CONFIRMED.
**Suggested improvement:** In Phase 2 ("Verify"), add a rule ahead of the agent dispatch: *if the repo has a runnable test harness, first try to confirm the candidate by executing it — a throwaway test file, or a REPL call against the changed function. A failing assertion with concrete values is a CONFIRMED vote and needs no verifier agent. Delete the probe file afterwards; never commit it.* Reserve verifier agents for candidates that cannot be executed (deploy scripts, CSS, cross-process timing, deployment-state claims). Also note the corollary for Phase 0: run the existing suite once up front — a fully green suite is itself a finding when the diff claims to fix bugs, because it tells you the new tests do not cover the mechanism.
**Principle:** In a repo with a test harness, the cheapest verifier is the interpreter. Reasoning about what code does is a fallback for when you cannot make it do it.

### Observation 30: A new test that passes on the fixture is not a pinned invariant
**Status:** OPEN
**Date:** 2026-08-31
**Session context:** Same review. Several new tests asserted broad invariants but were satisfied by the particular fixture rather than by the code.
**Skill:** code-review
**Issue:** Three separate defects in this diff hid behind tests that pass: `test_the_shelf_and_the_math_now_agree` asserts "whatever shifts the board is said aloud" but only exercises a fixture that happens to yield <=3 spoken lines, while the same file's `test_still_capped_at_three_lines` fixture breaks the invariant outright; `test_the_luxury_markdown_does_not_promote_negatives` asserts `luxury_markdown(-30.0) < -2.0`, which is trivially true of the identity branch and so cannot fail; `test_irrelevant_players_do_not_pad_the_score` passes only because it seeds a 1.0-point season projection that no real source emits. The finder angles surfaced these only because one angle was explicitly told to look for unfalsifiable assertions.
**Suggested improvement:** Add to the Phase 1 angle list (or fold into the sweep's focus list): *for every NEW test in the diff, ask whether it would still pass against the pre-fix code, and whether its assertion is satisfiable by the fixture rather than the mechanism. Try to construct an input inside the test's own stated rule that the test does not cover — the diff's other fixtures are the first place to look.*
**Principle:** A test added alongside a fix is written to pass. Whether it constrains anything is a separate question, and asking it is a distinct review angle.

### Observation 31: A rendered surface can contradict the payload that fed it — the gate can't see it
**Status:** ACTIONED — `bootlegger/.claude/skills/run-bootlegger/audit.py` (`audit_provenance`, 4 checks) + SKILL.md, 2026-08-31. Mutation-checked: restoring the unconditional "P100 of this room's book" caption fails the gate (1 of 29).
**Date:** 2026-08-31
**Session context:** Comparative review of the live Bootlegger board (accuracy + usefulness vs the big fantasy sites), focused on draft and waivers.
**Skill:** `bootlegger/.claude/skills/run-bootlegger` (audit.py, the "A++ gate")
**Issue:** The live Waivers page prints "P100 of this room's book" under each bid and "the bid is that percentile of 0 winning bids this room has actually paid", while `/api/waivers` returned `history_n: 0` and `pricing: "no bid history on the books — score-proportional"`. `data.pricing` is not referenced anywhere in `app.js` — the server's own honest disclaimer is computed and dropped. `driver.py check`/`shots` pass (page paints, no JS errors) and `audit`'s 18 checks pass, because every one of them asks about the DOM in isolation. Nothing in the gate ever asks whether the page's claims agree with the JSON that produced them. The house principle this violates ("never silently fail", "say only what the evidence pays for") is the product's stated principle #1.
**Suggested improvement:** Add a check class to `audit.py`: for each tab, fetch the API payload the tab renders and assert a small set of payload↔DOM consistency invariants — starting with (a) any provenance phrase naming a source of evidence ("this room's book", "N winning bids", "P<n>") must not render when the payload's own `*_n`/`pricing`-style field says that evidence is absent, and (b) every explanatory string the API computes for the UI must appear in the DOM or be deliberately listed as unused. Cheapest general form: assert no rendered text interpolates a count that equals 0 into a sentence asserting evidence exists.
**Principle:** A screenshot proves the page rendered; it cannot prove the page is telling the truth about where its numbers came from. When the server computes a caveat string, the gate should verify the client consumed it — an unreferenced disclaimer field is a silent failure wearing the costume of an honest one.

### Observation 32: Reconcile the served payload against the database before blaming the code
**Status:** OPEN — escalated: edits `~/.claude/skills/stress-test-findings`, a user-scope skill. See [[stress_test_findings_pending_dropins]] for the same blocker.
**Date:** 2026-08-31
**Session context:** Same review — establishing *why* the live waiver board showed 2 targets when 455 free agents carried projections.
**Skill:** `stress-test-findings` (Move 4, assumption-independent evidence)
**Issue:** The obvious path was "read `brain.waiver_targets`, explain the filter". That path is unfalsifiable here: `run-bootlegger`'s own gotchas warn that the running container's Python is whatever was imported at process start, so a source reading can describe code the live board is not executing. What actually settled it was arithmetic: the served `fa_score` values (6.5, 1.1) were reproduced to the decimal from the live DB (CLE 101.925 − my worst DEF 95.465 = 6.460; KC 96.549 − 95.465 = 1.084), which proves the deployed binary implements that rule regardless of which revision it is. Move 4 already says "the persisted output, not a re-reading of the code" — but it frames that as *logs/artifacts*, and this case is a third form: recomputing the served response from the inputs.
**Suggested improvement:** In Move 4's specialization (a) — currently "when the finding is 'the code does X', the assumption-independent proof is the pipeline's persisted output" — add the API/response variant: *for a running service, recompute the served response from its own datastore. A numeric match between payload and inputs proves which rule the deployed process is executing, without needing to know which revision it is running.* Name the failure it routes around (stale process serving old code behind fresh static assets).
**Principle:** When the artifact under test is a live response rather than a batch output, the assumption-independent proof is a reconciliation, not a log line. Match the number, not the source file.
