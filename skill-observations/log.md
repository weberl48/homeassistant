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
