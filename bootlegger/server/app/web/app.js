/* BOOTLEGGER front room. No framework, no build step — the wire must not
   depend on a toolchain. Polls the API, keeps the board hot, never fails
   silently. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const POS_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"];

/* Every feed-derived string (player names, teams, rationale) passes through
   esc() before innerHTML — a poisoned upstream name must not break or script
   the board. */
const esc = (v) => String(v ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* THE STILLNESS RULE (DESIGN.md, Motion): "the 1 Hz poll patches never
   re-fire anything." Every room here repaints by replacing innerHTML, and
   every replaced element replays its room-in entry animation — so a room that
   redraws on unchanged data flashes on its own poll interval. This Week did it
   every 1500ms and read as THE BEAT blinking; the others do it more slowly and
   were no more correct for it.

   changed(key, data) is the whole fix: a room draws when it has something new
   to say and holds still when it does not. Returns true (and remembers) only
   when the payload differs from the last one drawn. */
const _drawn = new Map();
function changed(key, data, alsoWhenEmpty) {
  const sig = JSON.stringify(data);
  if (_drawn.get(key) === sig && !alsoWhenEmpty) return false;
  _drawn.set(key, sig);
  return true;
}

/* THE LIVE-REGION RULE: a live region may only announce when its MEANING
   changes. The board repaints at 1 Hz during a draft, and renderClock used to
   assign textContent unconditionally — which, inside an aria-live="polite"
   plate, made a screen reader read "PICK 23 OF 180 · ROUND 2" aloud once a
   second for the entire draft. Assigning the identical string is still a
   mutation to the accessibility tree, so "the text didn't change" is not the
   same as "nothing was announced". Every write into a live region goes
   through here; `driver.py audit` fails the build if an idle poll mutates one. */
function setLive(el, text) {
  if (!el) return;
  const next = String(text ?? "");
  if (el.textContent === next) return;
  el.textContent = next;
}

/* Optional shared secret for mutating routes (set BOOTLEGGER_API_TOKEN on the
   server, then localStorage.setItem('bootlegger.token', ...) on each device). */
let TOKEN = "";
try { TOKEN = localStorage.getItem("bootlegger.token") || ""; } catch { /* fine */ }
// Fallback only — the board payload carries the league's real roster_positions.
const SLOTS_NEEDED = { QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1, K: 1, DEF: 1 };

function slotsNeeded(board) {
  const rp = board.roster_positions;
  if (!Array.isArray(rp) || !rp.length) return SLOTS_NEEDED;
  const n = {};
  for (const s of rp) {
    if (["BN", "IR", "TAXI"].includes(s)) continue;
    const k = ["SUPER_FLEX", "SUPERFLEX", "WRRB_FLEX", "REC_FLEX"].includes(s) ? "FLEX" : s;
    n[k] = (n[k] || 0) + 1;
  }
  return n;
}

const state = {
  tab: localStorage.getItem("bootlegger.tab") || "board",
  board: null, week: null, health: null,
  lastSync: null, wireDown: false, pickFeedStale: false,
  weekSig: null,            // last rendered week card — see pollWeek
  beatSig: null,            // last rendered wire items — see loadBeat
  rowIndex: new Map(),      // player id -> row element
  builtPickCount: -1,
  approving: false,
  fastWeekUntil: 0,
  booted: false,
};

/* The IA is phase-aware: the season has rooms that matter and rooms that are
   dark. Pre-draft and draft night the Board is the house; once the draft is
   in the books, This Week is. When the phase turns a corner the right room
   meets you at the door — otherwise your remembered tab is respected. */
const PHASE_ROOM = { pre_draft: "board", drafting: "board", complete: "week" };
let _practicePhase = null;
/* A room asked for BY NAME outranks the phase router. Rooms became linkable
   the same day this guard was written: without it, following a #waivers link
   on a cold browser lands you on This Week a beat later, because the first
   board poll sees a phase it has never recorded and shows you the door it
   thinks you want. A draft going live still moves you — that one is urgent
   enough to overrule a link — but nothing else does. */
let roomPinned = false;
function applyPhase(status, practice) {
  if (!status) return;
  if (practice) {
    // A scrimmage may grab the board when it goes live — but it never writes
    // the remembered season phase, and its "complete" moves nobody.
    if (status === "drafting" && _practicePhase !== "drafting") setTab("board");
    _practicePhase = status;
    return;
  }
  _practicePhase = null;
  if (status === localStorage.getItem("bootlegger.phase")) return;
  localStorage.setItem("bootlegger.phase", status);
  // Mid-session, only a draft going live is urgent enough to move you.
  if (status === "drafting") { roomPinned = false; setTab("board"); return; }
  if (!state.booted && !roomPinned) setTab(PHASE_ROOM[status] || "board");
}

/* ---------------------------------- icons -------------------------------- */
/* The Buffalo: an original cartoon bison, head down, red speed streak. Keeps
   the ref's class contract (.ref / .is-td) so every call site still works —
   idle he breathes, on your clock he stampedes across the plate. */
const REF_SVG = (td) => `
<svg class="ref ${td ? "is-td" : ""}" viewBox="0 0 60 44" aria-hidden="true">
  <g class="dust" fill="#c2cde6">
    <circle cx="54" cy="38" r="3"/><circle cx="57" cy="33" r="2.2"/><circle cx="52" cy="30" r="1.6"/>
  </g>
  <path d="M14 13 L 48 7" stroke="#c60c30" stroke-width="2.6" stroke-linecap="round" fill="none" opacity=".9"/>
  <path d="M10 22 C 12 12, 24 8, 34 10 C 44 11, 50 16, 51 22 C 52 27, 48 30, 44 31
           L 44 40 L 40 40 L 39 32 L 25 32 L 24 40 L 20 40 L 20 31
           C 14 30, 9 28, 10 22 Z" fill="#2e5fd9"/>
  <path d="M10 20 C 3 21, 1 28, 5 32 C 8 34, 12 33, 14 30 L 13 22 Z" fill="#1f47ad"/>
  <path d="M5 22 C 1 20, 1 15, 5 13" stroke="#f2f6ff" stroke-width="1.8"
    stroke-linecap="round" fill="none"/>
  <circle cx="9.5" cy="25" r="1.1" fill="#f2f6ff"/>
  <path d="M51 22 C 54 21, 55 23, 54 25" stroke="#2e5fd9" stroke-width="2"
    stroke-linecap="round" fill="none"/>
</svg>`;

/* The telestrator: the booth's glowing pen, forever drawing the route. */
const CHALK_LIVE = `
<svg class="chalk-live" viewBox="0 0 150 100" aria-hidden="true" fill="none"
  stroke="#5b8cff" stroke-width="2.2" stroke-linecap="round">
  <circle cx="22" cy="82" r="7" stroke-opacity=".55"/>
  <path class="route" d="M28 76 C 52 66, 60 48, 78 44 S 118 30, 138 12" stroke-dasharray="7 8"/>
  <path d="M138 12l-11 1M138 12l-4 10" stroke-opacity=".8"/>
</svg>`;

/* THE TABLE lives in the shelf's corner (static markup in index.html). It
   folds when a pick of yours lands, and always stands back up — there is
   always another table. */
function foldTable() {
  document.querySelectorAll(".folding-table").forEach((t) => {
    t.classList.add("is-folded");
    setTimeout(() => t.classList.remove("is-folded"), 3200);
  });
}

const icon = {
  cross: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M6 2h4v4h4v4h-4v4H6v-4H2V6h4z"/></svg>`,
  hold: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M8 1.5 15 14H1z"/><path d="M8 6v4M8 12.2v.3"/></svg>`,
  flag: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M3 15V2m0 .8c3-1.8 7 1.8 10 0v7c-3 1.8-7-1.8-10 0"/></svg>`,
};

/* ---------------------------------- net ----------------------------------- */
async function fetchJSON(url, opts = {}) {
  if (TOKEN) opts.headers = Object.assign({ "X-Bootlegger-Token": TOKEN }, opts.headers);
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

function wireOK() {
  state.lastSync = Date.now();
  if (state.wireDown) { state.wireDown = false; renderWire(); }
}
function wireFail() {
  if (!state.wireDown) { state.wireDown = true; renderWire(); }
}
function renderWire() {
  const wire = $("#wire"), banner = $("#wire-banner");
  const down = state.wireDown, stale = state.pickFeedStale;
  wire.classList.toggle("is-down", down || stale);
  // A thinned consensus shows on the lamp itself, not just the colophon —
  // during a draft nobody reads the basement.
  $("#wire-text").textContent = down ? "wire down" : stale ? "wire stale"
    : state.sourcesShort ? `wire live · ${state.sourcesShort} sources` : "wire live";
  banner.hidden = !(down || stale);
  if (!banner.hidden)
    banner.innerHTML = down
      ? `WIRE DOWN — showing the last board from <span id="wire-age">—</span> ago. Retrying.`
      : `PICK FEED STALE — the wire answers but no fresh picks in over 10 seconds.
         The poller may be down; picks shown may be behind the room.`;
}
setInterval(() => {
  const el = $("#wire-age");
  if (el && state.wireDown && state.lastSync)
    el.textContent = `${Math.round((Date.now() - state.lastSync) / 1000)}s`;
}, 1000);

/* ---------------------------------- tabs ---------------------------------- */
const ROOMS = ["board", "week", "waivers", "league", "parlor", "ledger"];

document.querySelectorAll(".tab").forEach((b) =>
  b.addEventListener("click", () => { roomPinned = true; setTab(b.dataset.tab); }));

/* Arrow keys are not a nicety here — a tablist without them is an incomplete
   pattern, and a screen-reader user who lands on the strip expects Left/Right
   to move between rooms rather than Tab. Roving tabindex keeps the whole strip
   a single tab stop. */
$("#tabs").addEventListener("keydown", (e) => {
  const keys = { ArrowLeft: -1, ArrowRight: 1, Home: "first", End: "last" };
  if (!(e.key in keys)) return;
  e.preventDefault();
  const i = ROOMS.indexOf(state.tab);
  const move = keys[e.key];
  const next = move === "first" ? 0
    : move === "last" ? ROOMS.length - 1
    : (i + move + ROOMS.length) % ROOMS.length;
  roomPinned = true;
  setTab(ROOMS[next]);
  $(`#tab-${ROOMS[next]}`).focus();
});

/* Six rooms do not fit a phone, so the strip scrolls. The caret at its right
   edge is the only thing that says so in a still frame — and it leaves once
   there is nothing further to scroll to. */
(() => {
  const strip = $("#tabs"), wrap = $("#tabs-wrap");
  const sync = () => wrap.classList.toggle(
    "at-end", strip.scrollLeft >= strip.scrollWidth - strip.clientWidth - 2);
  strip.addEventListener("scroll", sync, { passive: true });
  addEventListener("resize", sync);
  sync();
})();

/* The room is in the URL. Back leaves the room you came from instead of the
   app, and a room can be bookmarked or sent to your own phone. localStorage
   stays as the fallback for a bare URL. */
addEventListener("hashchange", () => {
  const want = location.hash.replace(/^#/, "");
  if (ROOMS.includes(want) && want !== state.tab) { roomPinned = true; setTab(want, { push: false }); }
});

function setTab(tab, { push = true } = {}) {
  if (!ROOMS.includes(tab)) tab = "board";
  state.tab = tab;
  try { localStorage.setItem("bootlegger.tab", tab); } catch { /* private mode */ }
  if (push && location.hash.replace(/^#/, "") !== tab) location.hash = tab;
  document.querySelectorAll(".tab").forEach((b) => {
    const active = b.dataset.tab === tab;
    b.classList.toggle("is-active", active);
    b.setAttribute("aria-selected", String(active));
    b.tabIndex = active ? 0 : -1;
  });
  for (const room of ROOMS)
    $(`#room-${room}`).hidden = room !== tab;
  if (tab === "waivers") loadWaivers();
  if (tab === "league") loadLeague();
  if (tab === "parlor") loadParlor();
  if (tab === "ledger") loadLedger();
  if (tab === "week") pollWeek();
}

/* ------------------------------- draft board ------------------------------ */
function posOf(p) { return p.pos === "DST" ? "DEF" : p.pos; }

function fmtSurv(s) { return `${Math.round(s * 100)}%`; }

/* Significant figures, not fixed ones. A VBD of 166.3 against 166 is a
   distinction nobody drafts on, while 4.6 against 5 is — and the tenth on the
   big numbers is exactly what pushed the widest rows past their column on the
   live board, where a four-figure negative VBD exists and the demo's fixture
   never produced one. Precision where it decides something, width where it
   does not. */
function fmtVbd(v) {
  if (v == null) return "–";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return Math.abs(n) >= 100 ? n.toFixed(0) : n.toFixed(1);
}

function playerRow(p) {
  const el = document.createElement("div");
  el.className = "prow";
  el.dataset.id = p.id;
  el.tabIndex = 0;
  el.setAttribute("role", "button");
  el.setAttribute("aria-label", `Open the scout's file — ${p.name}`);
  el.addEventListener("click", () => openDossier(p.id));
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDossier(p.id); }
  });
  const injury = p.injury
    ? `<span class="hurt">${icon.cross}${esc(p.injury.toUpperCase())}</span>` : "";
  el.innerHTML = `
    <div class="l1">
      <span class="stamp" data-stamp>–</span>
      <span class="name" title="${esc(p.name)}">${esc(p.name)}</span>
      ${injury}
    </div>
    <div class="l2">
      <span class="team">${esc(p.team ?? "")} · bye ${esc(p.bye ?? "–")}</span>
      <span class="nums">
        <span title="value over the last starter at his position"><span class="lbl">vbd</span><b data-vbd>${fmtVbd(p.vbd)}</b></span>
        <span title="average draft position across the sources on the wire"><span class="lbl">adp</span><span data-adp>${p.adp ?? "–"}</span></span>
      </span>
    </div>
    <div class="surv" data-surv hidden>
      <div class="surv-bar"><div class="surv-fill" data-fill style="width:0%"></div></div>
      <span class="surv-pct" data-pct></span>
    </div>`;
  return el;
}

function updateRow(el, p, justPicked) {
  const stamp = el.querySelector("[data-stamp]");
  const surv = el.querySelector("[data-surv]");
  if (p.pick_no) {
    el.classList.add("is-picked");
    el.classList.toggle("is-mine", !!p.mine);
    stamp.textContent = p.mine ? `MAFIA · P${p.pick_no}` : `P${p.pick_no}`;
    surv.hidden = true;
    if (justPicked) {
      el.classList.add("just-picked");
      setTimeout(() => el.classList.remove("just-picked"), 1400);
      if (p.mine) foldTable(); // your pick lands: the table does not survive
    }
  } else {
    el.classList.remove("is-picked", "is-mine");
    stamp.textContent = el.dataset.rank;
    if (p.survival != null) {
      surv.hidden = false;
      el.querySelector("[data-fill]").style.width = `${Math.round(p.survival * 100)}%`;
      el.querySelector("[data-pct]").textContent = fmtSurv(p.survival);
    } else surv.hidden = true;
  }
}

function buildColumns(board) {
  const wrap = $("#columns");
  wrap.textContent = "";
  state.rowIndex.clear();
  for (const pos of POS_ORDER) {
    const players = board.players.filter((p) => posOf(p) === pos)
      .sort((a, b) => b.pts - a.pts);
    if (!players.length) continue;
    const col = document.createElement("section");
    col.className = "col";
    col.dataset.pos = pos;
    col.innerHTML = `<div class="col-head"><span class="pos pos-${pos}"></span>
      <h3>${pos === "DEF" ? "D/ST" : pos}</h3>
      <span class="count">${players.length} listed</span></div>
      <div class="col-body"></div>`;
    const body = col.querySelector(".col-body");
    let lastTier;
    let rank = 0;
    for (const p of players) {
      rank += 1;
      const tier = p.tier ?? "depth";
      if (tier !== lastTier) {
        const rule = document.createElement("div");
        rule.className = "tier-rule" + (tier === "depth" ? " depth" : "");
        rule.textContent = tier === "depth" ? "The deep shelf" : `Tier ${tier}`;
        body.appendChild(rule);
        lastTier = tier;
      }
      const row = playerRow(p);
      row.dataset.rank = String(rank);
      body.appendChild(row);
      state.rowIndex.set(p.id, row);
    }
    wrap.appendChild(col);
  }
  // Mobile: the deep shelf starts folded — even one position ran ~12k px tall
  // with it open. The tier rule itself is the toggle (CSS folds only ≤900px).
  wrap.querySelectorAll(".tier-rule.depth").forEach((rule) => {
    const col = rule.closest(".col");
    // A column that is ALL shelf (K/DEF) must not fold to an empty box.
    if (rule === rule.parentElement.firstElementChild) { col.classList.add("shelf-open"); return; }
    const n = [...rule.parentElement.querySelectorAll(".prow")].filter(
      (el) => rule.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING).length;
    // It folds a dozen players away, so it is a control, not a caption — and a
    // control the keyboard cannot reach is a control half the room does not
    // have. Swapped from <div> + click handler to a real button with state.
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = rule.className;
    btn.setAttribute("aria-expanded", "false");
    btn.innerHTML = `The deep shelf <span class="shelf-n">${n} more</span>`;
    btn.addEventListener("click", () => {
      const open = col.classList.toggle("shelf-open");
      btn.setAttribute("aria-expanded", String(open));
    });
    rule.replaceWith(btn);
  });
  applyPosFilter();
}

function renderBoard(board) {
  const prev = state.board;
  const rebuilt = !prev || board.draft.current_pick < prev.draft.current_pick
    || state.builtPickCount === -1;
  if (rebuilt) { buildColumns(board); state.builtPickCount = 0; }

  const prevPicked = new Set(
    (prev && !rebuilt ? prev.players : []).filter((p) => p.pick_no).map((p) => p.id));
  for (const p of board.players) {
    const row = state.rowIndex.get(p.id);
    if (row) updateRow(row, p, p.pick_no && !prevPicked.has(p.id) && !rebuilt ? true : false);
  }

  renderClock(board.draft);
  renderCall(board);
  renderShelf(board);
  renderShelfFindings(board.shelf);
  renderPressure(board.pressure);
  renderPriors(board.priors);
  renderRoomRead(board);
  renderTicker(board.recent_picks);
  state.board = board;

  // C1 guard: during a live draft, an answering API with an aging pick feed
  // means the poller died — say so instead of showing a stale board as live.
  const stale = board.draft.status === "drafting" && board.draft.synced_at
    ? Date.now() - Date.parse(board.draft.synced_at) > 10000 : false;
  if (stale !== state.pickFeedStale) { state.pickFeedStale = stale; renderWire(); }
}

function renderClock(d) {
  const plate = $("#clockplate");
  plate.classList.toggle("is-mine", !!d.on_the_clock_me);
  if (d.status === "pre_draft") {
    setLive($("#clock-line"), "AWAITING KICKOFF");
    setLive($("#clock-sub"), "the draft hasn't started — the board warms up");
    return;
  }
  if (d.status === "complete") {
    setLive($("#clock-line"), "DRAFT COMPLETE");
    setLive($("#clock-sub"), `${d.rounds} rounds in the books`);
  } else if (d.on_the_clock_me) {
    setLive($("#clock-line"), `YOU'RE ON THE CLOCK — PICK ${d.current_pick}`);
    const pick = state.board?.suggestions?.[0];
    setLive($("#clock-sub"), pick
      ? `the room likes ${pick.name}` : "the room is thinking");
  } else {
    setLive($("#clock-line"), `PICK ${d.current_pick} OF ${d.total_picks} · ROUND ${d.round}`);
    setLive($("#clock-sub"), d.my_next_pick
      ? `slot ${d.on_clock_slot} on the clock · you pick at #${d.my_next_pick}`
      : "no picks left for you");
  }
}

let _callSig = "";

function renderCall(board) {
  const body = $("#call-body");
  const s = board.suggestions;
  // Rebuild only when the plate's content actually changes — at draft-time
  // 1 Hz polling an unconditional innerHTML rebuild restarts the ref's bob
  // and the route-draw every second (they'd never finish a loop).
  const sig = JSON.stringify([
    board.draft.status, board.draft.on_the_clock_me, board.experts_call?.id,
    (s || []).slice(0, 5).map((r) => [r.id, r.score]),
  ]);
  if (sig === _callSig) return;
  _callSig = sig;
  if (board.draft.status === "complete") {
    body.innerHTML = `<div class="call-done">
      <svg class="chalk-goal" viewBox="0 0 64 62" fill="none" stroke="currentColor"
        stroke-width="2" stroke-linecap="round" aria-hidden="true">
        <path d="M32 58V38M14 38h36M14 38V10M50 38V10"/>
        <path d="M4 54C14 36 22 24 38 8" stroke-dasharray="4 5"/>
        <ellipse cx="41" cy="6" rx="5" ry="3.2" transform="rotate(-38 41 6)"/>
      </svg>
      <div class="call-done-copy"><p><strong>Draft complete.</strong></p>
      <p class="muted">The shelf is stocked — the room pours one out. Season mode
      takes it from here.</p></div></div>`;
    return;
  }
  if (!s || !s.length) {
    body.innerHTML = `<p class="muted">Reading the room…</p>`;
    return;
  }
  const top = s[0];
  const sheet = board.experts_call;
  const disagree = sheet && sheet.id !== top.id;
  const runners = s.slice(1, 5).map((r) => `
    <li><span class="pos pos-${posOf(r)}">${posOf(r)}</span>
      <span>${esc(r.name)}</span>
      <span class="r-score">${r.score}</span></li>`).join("");
  body.innerHTML = `
    ${REF_SVG(!!board.draft.on_the_clock_me)}
    ${CHALK_LIVE}
    <div class="call-top">
      <div class="call-name">${esc(top.name)}</div>
      <div class="call-meta">
        <span class="pos pos-${posOf(top)}">${posOf(top)} · ${esc(top.team ?? "")}</span>
        <span class="stat" title="the room's regret math — points lost if you pass and take the next-best at your return pick"><span>score</span>${top.score}</span>
        <span class="stat" title="value over the last starter at his position, season-total"><span>vbd</span>${top.vbd}</span>
        <span class="stat" title="odds he's still on the board at your next pick"><span>survives</span>${fmtSurv(top.survival ?? 0)}</span>
      </div>
      <p class="call-reason">${esc(top.reason)}</p>
      ${disagree ? `<p class="call-sheet">The experts' sheet says
        <b>${esc(sheet.name)}</b> <span class="pos pos-${posOf(sheet)}">${posOf(sheet)}</span>
        <span class="sheet-ecr">ECR ${sheet.ecr}</span></p>` : ""}
    </div>
    <ul class="runners">${runners}</ul>`;
  body.querySelector(".call-name")?.addEventListener("click", () => openDossier(top.id));
  body.querySelectorAll(".runners li").forEach((li, i) =>
    li.addEventListener("click", () => openDossier(s[i + 1].id)));
  if (disagree)
    body.querySelector(".call-sheet b")?.addEventListener("click", () => openDossier(sheet.id));
}

function renderShelf(board) {
  const slots = slotsNeeded(board);
  const counts = {};
  for (const p of board.my_roster) counts[p.pos] = (counts[p.pos] || 0) + 1;
  const needs = [];
  let flexUsed = 0;
  for (const pos of ["QB", "RB", "WR", "TE", "K", "DEF"]) {
    const want = slots[pos] || 0;
    if (!want) continue;
    const have = counts[pos] || 0;
    if (["RB", "WR", "TE"].includes(pos) && have > want)
      flexUsed += have - want;
    needs.push({ pos, have: Math.min(have, want), want });
  }
  if (slots.FLEX)
    needs.push({ pos: "FLEX", have: Math.min(flexUsed, slots.FLEX), want: slots.FLEX });
  $("#shelf-needs").innerHTML = needs.map((n) =>
    `<span class="need ${n.have < n.want ? "is-open" : ""}">${n.pos} ${n.have}/${n.want}</span>`).join("");
  const list = $("#shelf-list");
  if (!board.my_roster.length) {
    list.innerHTML = `<li class="shelf-empty">Nothing on the shelf yet — your first
      pick lands at #${board.draft.my_next_pick ?? "–"}.</li>`;
    return;
  }
  list.innerHTML = board.my_roster.map((p) => `
    <li><span class="rd">${p.round}</span>
      <span class="pos pos-${posOf(p)}">${posOf(p)}</span>
      <span>${esc(p.player)}</span></li>`).join("");
}

/* ------------------------------ the advisory layer ------------------------
   Readings, never bids. Nothing rendered here feeds back into the suggestion
   score or survival — that boundary is enforced server-side and pinned by
   tests/test_advisories.py. Status hues follow the house: marigold cautions,
   lamp is a strength, and Bills red stays reserved for actual trouble. */

/* Typewriter Figures Rule: the figures inside a finding's label ("WK 9") are
   Courier; the letters around them stay the condensed gothic. */
function figures(label) {
  return esc(label).replace(/(\d+)/g, "<b>$1</b>");
}

function renderShelfFindings(shelf) {
  const el = $("#shelf-findings");
  if (!el) return;
  const rows = [];
  /* Sleeper ships no byes at all — they are backfilled from the schedule
     mirror. If that has not run, say so. A silent all-clear is the one
     failure this house does not allow. */
  if (shelf && shelf.byes_known === false) {
    rows.push(`<li class="finding is-unknown">
      <span class="f-tag">BYES</span>
      <span class="f-detail">Bye weeks haven't landed yet — this read is
      incomplete, not clean.</span></li>`);
  }
  for (const f of (shelf && shelf.findings) || []) {
    rows.push(`<li class="finding is-${esc(f.level)} k-${esc(f.kind)}">
      <span class="f-tag">${figures(f.label)}</span>
      <span class="f-detail">${esc(f.detail)}</span></li>`);
  }
  el.innerHTML = rows.join("");
}

function renderPressure(rows) {
  const el = $("#pressure");
  if (!el) return;
  if (!rows || !rows.length) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  el.innerHTML = rows.map((r) => {
    const n = Math.abs(r.residual);
    const word = r.direction === "run" ? "run" : "sliding";
    return `<span class="press press-${esc(r.direction)}">
      <span class="pos pos-${esc(posOf(r))}">${esc(posOf(r))}</span>
      <span class="press-word">${word}</span>
      <b>${r.direction === "run" ? "+" : "−"}${n.toFixed(1)}</b>
      <span class="press-word">vs the market</span></span>`;
  }).join("");
}

function renderRoomRead(board) {
  const el = $("#room-read");
  if (!el) return;
  const lines = board.room?.read || [];
  el.hidden = !lines.length;
  if (!lines.length) return;
  el.innerHTML = `<h3 class="priors-title">What this room actually does</h3>
    <ul>${lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>
    <p class="room-src">Measured against
      ${board.room.tendencies.length ? board.room.tendencies[0].drafts : 0}
      of this league's own past drafts — survival odds on the board are shifted
      to match.</p>`;
}

function renderPriors(lines) {
  const el = $("#priors");
  if (!el) return;
  if (!lines || !lines.length) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  el.innerHTML = `<h3 class="priors-title">What this room's rules do</h3>`
    + lines.map((l) => `<p>${esc(l)}</p>`).join("");
}

function renderTicker(picks) {
  // Before the first pick there is no tape. An empty paper strip across the
  // bottom of the board reads as a rendering fault, not as "nothing has
  // happened yet" — so the tape doesn't exist until it has something on it.
  const el = $("#ticker");
  el.closest(".ticker-wrap")?.classList.toggle("is-empty", !picks.length);
  el.innerHTML = picks.map((p) => `
    <li class="${p.mine ? "t-mine" : ""}">
      <span class="t-no">R${p.round}·P${p.pick_no}</span>
      <span>${esc(p.player)}</span>
      <span class="pos pos-${posOf(p)}">${posOf(p)}</span>
    </li>`).join("");
}

/* ------------------------------ scout's file ------------------------------ */
async function openDossier(pid) {
  let d;
  try { d = await fetchJSON(`/api/draft/player/${encodeURIComponent(pid)}`); }
  catch { return; }
  const srcRows = Object.entries(d.sources).map(([s, v]) =>
    `<span>${esc(s)}</span><span class="rule"></span><b>${v}</b>`).join("");
  const surv = d.survival_next != null
    ? `<div class="d-src"><span>reaches your pick #${d.my_next_pick}</span><span class="rule"></span><b>${Math.round(d.survival_next * 100)}%</b>
       ${d.survival_wait != null ? `<span>lasts until your next turn</span><span class="rule"></span><b>${Math.round(d.survival_wait * 100)}%</b>` : ""}</div>` : "";
  const bal = d.balance.map((b) => {
    const w1 = Math.min(b.before, 100), w2 = Math.min(b.after, 100);
    const add = w2 > w1 ? `<div class="d-bar-add" style="left:${w1}%;width:${w2 - w1}%"></div>` : "";
    const num = b.after !== b.before ? `${b.before}→<b>${b.after}%</b>` : `${b.before}%`;
    return `<div class="d-bal-row"><span class="blb">${esc(b.pos)} ${b.have}/${b.want}</span>
      <div class="d-bar"><div class="d-bar-fill" style="width:${w1}%"></div>${add}</div>
      <span class="num">${num}</span></div>`;
  }).join("");
  $("#dossier-body").innerHTML = `
    <div class="d-file">Scout's file · tier ${esc(d.tier ?? "–")}</div>
    <div class="d-head">
      <span class="d-name">${esc(d.name)}</span>
      <span class="pos pos-${esc(d.pos === "DST" ? "DEF" : d.pos)}">${esc(d.pos)}</span>
      <span class="d-meta">${esc(d.team ?? "")} · bye ${esc(d.bye ?? "–")}${d.injury ? " · " + esc(d.injury) : ""}</span>
    </div>
    ${d.insights.length ? `<div class="d-title">The read</div>
      <div class="d-insights">${d.insights.map((i) => `<div>${esc(i)}</div>`).join("")}</div>` : ""}
    <div class="d-title">The figures</div>
    <div class="d-src">${srcRows}
      <span class="d-cons">consensus</span><span class="rule"></span><b class="d-cons">${d.consensus ?? "–"}</b>
      ${d.spread ? `<span>spread</span><span class="rule"></span><b>±${d.spread}</b>` : ""}
      ${d.vbd != null ? `<span>value over replacement</span><span class="rule"></span><b>${d.vbd}</b>` : ""}
      ${d.ecr ? `<span>experts' sheet rank</span><span class="rule"></span><b>${d.ecr}</b>` : ""}
      ${d.street_adp ? `<span>the street drafts him</span><span class="rule"></span><b>${d.street_adp}</b>` : ""}
      ${d.ds_floor ? `<span>sharks' floor / ceiling</span><span class="rule"></span><b>${d.ds_floor} – ${d.ds_ceiling}</b>` : ""}
      ${d.injury_risk != null ? `<span>injury risk (sharks)</span><span class="rule"></span><b>${Math.round(d.injury_risk)}%${d.proj_games ? " · " + Math.round(d.proj_games) + " gm" : ""}</b>` : ""}
    </div>
    ${surv ? `<div class="d-title">The odds</div>${surv}` : ""}
    ${bal ? `<div class="d-title">Your shelf, with him</div><div class="d-bal">${bal}</div>` : ""}`;
  // Remember who opened it. A file you close should hand focus back to the row
  // you were reading, not dump you at the top of the board.
  dossierOpener = document.activeElement?.closest?.(".prow") || null;
  $("#dossier").showModal();
}

/* A real <dialog> + showModal(): the focus trap, Escape, and the inertness of
   everything behind it come from the platform. This DELETED the hand-rolled
   Escape and backdrop handlers rather than adding to them — the old ones
   closed the file but never moved focus in, never trapped it, and left the
   whole board tabbable underneath. */
let dossierOpener = null;
$("#dossier-close").addEventListener("click", () => $("#dossier").close());
$("#dossier").addEventListener("click", (e) => {
  // ::backdrop clicks land on the dialog itself; a click on the sheet does not.
  if (e.target === $("#dossier")) $("#dossier").close();
});
$("#dossier").addEventListener("close", () => {
  dossierOpener?.focus();
  dossierOpener = null;
});

/* position filter (mobile chips) */
let activePos = "ALL";
$("#pos-filters").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  // A human tap is a preference and sticks; the boot-time auto-default isn't.
  if (e.isTrusted) localStorage.setItem("bootlegger.pos.chosen", "1");
  activePos = chip.dataset.pos;
  document.querySelectorAll("#pos-filters .chip").forEach((c) => {
    c.classList.toggle("is-active", c === chip);
    c.setAttribute("aria-pressed", String(c === chip));
  });
  applyPosFilter();
});

/* On a phone, ALL stacks every column into a ~14,000px scroll. Until the user
   picks a chip themselves, open on the position The Call is pointing at —
   that's where the decision is. */
function chipDefault() {
  if (!matchMedia("(max-width: 900px)").matches) return;
  if (localStorage.getItem("bootlegger.pos.chosen")) return;
  const top = state.board?.suggestions?.[0];
  if (!top) return;
  document.querySelector(`#pos-filters .chip[data-pos="${posOf(top)}"]`)?.click();
}
function applyPosFilter() {
  document.querySelectorAll(".col").forEach((col) =>
    col.classList.toggle("pos-hidden", activePos !== "ALL" && col.dataset.pos !== activePos));
}

async function pollBoard() {
  try {
    renderBoard(await fetchJSON("/api/draft/board"));
    const status = state.board?.draft?.status;
    const practice = !!state.board?.draft?.practice;
    $("#practice-banner").hidden = !practice;
    // The server hard-refuses binds during the live league draft (409);
    // hiding the box removes the affordance for the misclick entirely.
    $("#scrimmage").hidden = status === "drafting" && !practice;
    applyPhase(status, practice);
    maybeLoadGrades();
    // Wayfinding: rooms that have nothing until the season starts read dim.
    // The honest signal is whether I OWN PLAYERS — not which draft is bound.
    // (Keying off the draft made a bound scrimmage dim the real season's
    // rooms, and a finished scrimmage light them; the roster never lies.)
    const seasonOn = (state.board?.my_roster || []).length > 0;
    document.querySelectorAll(".tab").forEach((b) => {
      if (["week", "waivers", "league", "parlor"].includes(b.dataset.tab))
        b.classList.toggle("is-dormant", !seasonOn);
    });
    wireOK();
  } catch { wireFail(); }
}

/* -------------------------------- this week ------------------------------- */
const STEPS = ["proposed", "notified", "approved", "executed", "verified"];

function stepper(recState) {
  const failed = recState === "failed";
  // a snoozed rec still sits at "notified" on the chain — never an unlit
  // stepper; a dry_run terminal parks at approved (nothing executed).
  const shown = recState === "snoozed" ? "notified"
    : recState === "dry_run" ? "approved" : recState;
  const idx = failed ? STEPS.length : STEPS.indexOf(shown);
  return `<div class="stepper" aria-label="Recommendation state">` + STEPS.map((s, i) => {
    let cls = "step";
    if (failed && i >= 2) cls += " is-failed";
    else if (s === shown) cls += s === "verified" ? " is-verified" : " is-now";
    else if (i < idx) cls += " is-done";
    return `${i ? '<span class="step-link"></span>' : ""}<span class="${cls}">${failed && s === "verified" ? "failed" : s}</span>`;
  }).join("") + `</div>`;
}

function kickoffShort(iso) {
  // "Sun 1:00" in the viewer's own timezone; the schedule stores UTC.
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
}

function gameChip(r) {
  // Opponent + kickoff (+ implied total, lock, weather) — the schedule and
  // market context per row. `imp` is Vegas's expected score for his team.
  if (!r.opp) return "";
  const imp = r.imp != null ? ` · imp ${r.imp}` : "";
  const wx = (r.wx || []).length
    ? ` <span class="hurt">${icon.cross}${esc(r.wx.join(", ").toUpperCase())}</span>` : "";
  const lock = r.locked ? ` <span class="hurt">${icon.cross}LOCKED</span>` : "";
  return `<span class="team">${esc(r.opp)} ${esc(kickoffShort(r.kickoff_utc))}${esc(imp)}</span>${lock}${wx}`;
}

/* The market's read on the game a man is standing in. Shown as a delta on the
   projection rather than as a raw implied total, because the reader's question
   is "why is this number what it is", not "what is Buffalo's team total". A
   spot the books have not priced shows nothing at all — silence is the honest
   rendering of an average expectation, and a 0% chip on two thirds of the
   roster would be noise. */
function envMark(r) {
  const e = r.env;
  if (!e || !e.known || Math.abs(e.pct) < 1) return "";
  const cls = e.pct > 0 ? "env-up" : "env-down";
  return `<span class="envmark ${cls}" title="${esc(e.reason)}">`
    + `${e.pct > 0 ? "+" : ""}${e.pct.toFixed(0)}%</span>`;
}

function lineupTable(title, rows, total, marks, news) {
  const tr = rows.map((r) => {
    const mark = marks.get(r.id) || "";
    const hurt = r.injury ? `<span class="hurt">${icon.cross}${esc(r.injury.toUpperCase())}</span>`
      : r.bye ? `<span class="hurt">${icon.cross}BYE</span>`
      : r.practice && r.practice !== "FULL" ? `<span class="hurt">${icon.cross}${esc(r.practice)}</span>` : "";
    // The beat is FASTER than Sleeper's own tag — a man ruled out at noon
    // shows here before the API knows. Both are shown; neither is dropped.
    const beat = beatChip((news || {})[r.id]);
    return `<tr class="${mark}"><td class="slot">${esc(r.slot)}</td>
      <td><span class="pname">${esc(r.name)}</span>
        <span class="team">${esc(r.team ?? "")}</span> ${hurt}
        <div class="row-context"><span class="pos pos-${posOf(r)}">${posOf(r)}</span>
          ${gameChip(r)} ${beat}</div></td>
      <td class="proj">${r.proj.toFixed(1)}${envMark(r)}</td></tr>`;
  }).join("");
  return `<div class="lineup"><h3>${title}<span class="total">${total.toFixed(1)}</span></h3>
    <table><tbody>${tr}</tbody></table></div>`;
}

function renderWeek(card) {
  const wrap = $("#week-layout");
  // The DOM is about to be replaced, so the beat's own guard is stale.
  state.beatSig = null;
  if (!card.ready) {
    // Pre-draft this room has no lineup to show, but the beat is already
    // running and is exactly what a manager wants in August.
    wrap.innerHTML = `
      <div class="room-empty">
        <h2 class="room-title">This Week</h2>
        <p class="room-note">${esc(card.note || "No roster on file yet.")}</p>
      </div>
      ${beatPanel()}`;
    loadBeat();
    return;
  }
  const rec = card.rec;
  const recState = rec ? rec.state : null;

  if (!card.material && (!rec || ["verified", "ignored", "failed", "dry_run", null].includes(recState))) {
    const verifiedLine = recState === "verified"
      ? `The last swap was <strong>verified against the API</strong>. ` : "";
    // The season's payoff lands HERE: a verified swap slams the bedsheet
    // stamp once (first render of this rec only — re-polls don't re-slam).
    let stamp = "";
    if (recState === "verified") {
      const fresh = state.celebratedRec !== rec.rec_id;
      stamp = `<span class="mafia-stamp${fresh ? " slam" : ""}">Table's folded ✓</span>`;
      state.celebratedRec = rec.rec_id;
    }
    wrap.innerHTML = `
      ${matchupBlock({...card.matchup, slate: card.slate})}
      <div class="allgood"><span class="lampdot"></span>
        <div><p><strong>Lineup optimal.</strong> ${verifiedLine}Projected
        ${card.actual_total.toFixed(1)} for week ${card.week} — the room is satisfied.${stamp}</p></div>
      </div>
      ${sameStarters(card)
        ? `<div class="week-pair">${lineupBlock(card)}${beatPanel()}</div>`
        : `${lineupBlock(card)}${beatPanel()}`}`;
    loadBeat();
    return;
  }

  const swapsLines = card.swaps.map((s) =>
    `${esc(s.in.name)} in for ${esc(s.out.name)} (${esc(s.slot)}, ${s.gain > 0 ? "+" : ""}${s.gain})`
    + (s.risk ? ` — <em>${esc(s.risk)}</em>` : "")).join(" · ");
  const wxLine = (card.wx_concerns || []).length
    ? `<p class="holds">${icon.hold} Weather on the slate: ${esc(card.wx_concerns.join("; "))}.</p>` : "";
  const holds = (rec && JSON.parse(rec.payload_json || "{}").rules_fired) || [];
  const holdNote = holds.length
    ? `<p class="holds">${icon.hold} Held by house rule: ${esc(holds.join(", "))} — the hands
       will not move; do it in Sleeper if you agree.</p>` : "";

  let actions = "";
  if (!rec) {
    actions = `<div class="actions"><button class="btn btn-primary" disabled>
      Writing it up…</button></div>`;
  } else if (["proposed", "notified", "snoozed"].includes(recState)) {
    const dis = state.approving ? "disabled" : "";
    actions = `<div class="actions">
      <button class="btn btn-primary" id="btn-approve" ${dis}>
        ${state.approving ? '<span class="spin"></span>Working…' : "Approve &amp; Execute"}</button>
      <button class="btn btn-ghost" id="btn-snooze" ${dis}>Snooze 30m</button>
      <button class="btn btn-ghost" id="btn-ignore" ${dis}>Ignore</button>
    </div>`;
  } else if (["approved", "executed"].includes(recState)) {
    actions = `<div class="actions"><button class="btn btn-primary" disabled>
      <span class="spin"></span>The hands are moving…</button></div>`;
  } else if (recState === "failed") {
    actions = `<p class="holds">${icon.hold} WIDE RIGHT — the swap missed. Every failure
      degrades to this notice, never to silence. <a href="https://sleeper.com" rel="noopener">Set it in Sleeper</a>.</p>`;
  } else if (recState === "dry_run") {
    actions = `<p class="holds">${icon.hold} Dry run — the hands touched nothing.
      <a href="https://sleeper.com" rel="noopener">Set it in Sleeper</a> if you agree.</p>`;
  }

  wrap.innerHTML = `
    <div class="verdict ${recState === "verified" ? "is-good" : ""}">
      <div class="verdict-head">
        <span class="verdict-title">${card.injury_flag ? "You have trouble in the lineup." : "The room found points."}</span>
        <span class="delta">${card.delta > 0 ? "+" : ""}${card.delta.toFixed(1)}</span>
      </div>
      <p class="rationale">${rec ? esc(rec.rationale) : swapsLines}</p>
      ${wxLine}
      ${holdNote}
      ${rec ? stepper(recState) : ""}
      ${actions}
    </div>
    ${matchupBlock({...card.matchup, slate: card.slate})}
    ${lineupBlock(card)}
    ${beatPanel()}`;
  loadBeat();

  const approveBtn = $("#btn-approve");
  if (approveBtn && rec) {
    approveBtn.addEventListener("click", async () => {
      state.approving = true; renderWeek(card);
      try {
        await fetchJSON(`/api/recs/${rec.rec_id}/approve`, { method: "POST" });
        state.fastWeekUntil = Date.now() + 30000;
      } catch { wireFail(); }
      state.approving = false;
      pollWeek();
    });
    $("#btn-snooze")?.addEventListener("click", async () => {
      try { await fetchJSON(`/api/recs/${rec.rec_id}/snooze`, { method: "POST" }); } catch {}
      pollWeek();
    });
    $("#btn-ignore")?.addEventListener("click", async () => {
      try { await fetchJSON(`/api/recs/${rec.rec_id}/ignore`, { method: "POST" }); } catch {}
      pollWeek();
    });
  }
}

/* --------------------------------- the beat ------------------------------
   News is the one input this board used to lack entirely, and the one that
   decides Sunday. It is THE BEAT, not "the wire" — the wire is already this
   product's word for the connection to Sleeper, and a board that says "the
   wire is down" about two different things has taught its owner nothing. Three treatments, because three different things are true:
   a grade (how urgent), an audience (whose man), and the feed's own health
   (a stalled wire must never read as a quiet news day). */

const SEV_LABEL = { out: "OUT", doubtful: "DOUBTFUL", questionable: "QUESTIONABLE",
                    practice: "PRACTICE", role: "ROLE", info: "NOTE" };

function beatAgo(iso) {
  if (!iso) return "";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (!Number.isFinite(mins) || mins < 0) return "";
  if (mins < 60) return `${mins}m ago`;
  const h = Math.round(mins / 60);
  return h < 48 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

/* The row chip: only grades that change a decision earn one. A "participated
   in practice" chip on a row that already shows a projection is clutter. */
function beatChip(n) {
  if (!n || !["out", "doubtful", "questionable", "practice"].includes(n.severity)) return "";
  // out/doubtful borrow .hurt (Bills red — this IS trouble); the softer grades
  // get their own marigold chip so the API's confirmed word still outranks the
  // beat's faster one.
  const cls = ["out", "doubtful"].includes(n.severity) ? "hurt" : "beat-note";
  const tail = n.ailment ? ` · ${n.ailment}` : "";
  return `<span class="${cls}" title="${esc(n.headline)}">${icon.cross}${esc(SEV_LABEL[n.severity])}${esc(tail)}</span>`;
}

function renderBeat(feed) {
  const el = $("#beat-feed");
  if (!el) return;
  const items = feed.items || [];
  const stale = feed.last_ok ? (Date.now() - new Date(feed.last_ok).getTime()) > 45 * 60000 : true;
  const health = stale
    ? `<p class="beat-health is-bad">The wire has not answered since
       ${esc(beatAgo(feed.last_ok) || "the board came up")} — these items may be old.</p>`
    : feed.missed_total
      ? `<p class="beat-health is-bad">${feed.missed_total} item${feed.missed_total === 1 ? "" : "s"}
         went past while the board was not looking. The feed only ever holds five.</p>`
      : `<p class="beat-health">${esc(feed.source)} · checked ${esc(beatAgo(feed.last_ok) || "just now")}.</p>`;
  const rows = items.map((n) => `
    <li class="beat-item beat-${esc(n.severity)} aud-${esc(n.audience)}">
      <span class="beat-grade">${esc(SEV_LABEL[n.severity] || "NOTE")}</span>
      <div class="beat-body">
        <p class="beat-line"><b>${esc(n.name)}</b>${n.pos ? ` <span class="beat-pos">${esc(posOf(n))}${n.team ? ` · ${esc(n.team)}` : ""}</span>` : ""}
          — ${esc(n.headline)}${n.ailment ? ` <i>(${esc(n.ailment)})</i>` : ""}</p>
        ${n.body ? `<p class="beat-detail">${esc(n.body)}</p>` : ""}
        <p class="beat-meta">${esc(beatAgo(n.published_at))}
          ${n.audience === "mine" ? '<span class="beat-tag is-mine">yours</span>'
            : n.audience === "league" ? '<span class="beat-tag">in the league</span>'
            : '<span class="beat-tag">on the street</span>'}
          ${n.departure ? '<span class="beat-tag is-open">a job just opened</span>' : ""}</p>
      </div></li>`).join("");
  el.innerHTML = `${health}${items.length
    ? `<ul class="beat-list">${rows}</ul>`
    : `<p class="muted">Nothing on the wire yet.</p>`}`;
}

async function loadBeat() {
  try {
    const feed = await fetchJSON("/api/wire?limit=30");
    // Same rule one level down: an unchanged wire must not repaint the sheet.
    const sig = JSON.stringify(feed.items || []);
    if (sig !== state.beatSig || !$("#beat-feed")?.childElementCount) {
      renderBeat(feed);
      state.beatSig = sig;
    }
    wireOK();
  } catch { wireFail(); }
}

/* ------------------------------ the matchup ------------------------------
   The optimizer maximises expected points, which is the right objective only
   in a close week. This block is what makes that visible: who you're playing,
   the odds, and — when it actually moves the odds — the floor or ceiling
   lineup the week calls for instead. */

function probMeter(p) {
  const pct = Math.round(p * 100);
  return `<div class="prob" role="img" aria-label="${pct}% to win">
    <div class="prob-bar"><span style="width:${Math.max(2, Math.min(98, pct))}%"></span></div>
    <span class="prob-num">${pct}%</span></div>`;
}

function matchupBlock(m) {
  if (!m) return "";
  const lead = m.margin >= 0 ? "up" : "down";
  const alt = m.alt;
  const altRows = alt ? alt.rows.filter((r) => r.swap_in).map((r) =>
    `<li><span class="pos pos-${posOf(r)}">${posOf(r)}</span> <b>${esc(r.name)}</b>
      <span class="alt-band">floor ${r.floor} · proj ${r.proj} · ceiling ${r.ceiling}</span></li>`).join("") : "";
  const altBlock = alt ? `
    <div class="alt-plan">
      <p class="alt-head">${esc(m.strategy.label)} — swap in:</p>
      <ul class="alt-list">${altRows}</ul>
      <p class="alt-foot">Takes the lineup to ${alt.total} expected
        (floor ${alt.floor}, ceiling ${alt.ceiling}) and your odds to
        <b>${Math.round(alt.win_prob * 100)}%</b>, from ${Math.round(m.win_prob * 100)}%.</p>
    </div>` : "";
  return `
    <div class="matchup">
      <div class="matchup-head">
        <div class="side"><span class="side-label">you</span>
          <span class="side-num">${m.my_proj.toFixed(1)}</span></div>
        <div class="side-vs">vs</div>
        <div class="side"><span class="side-label">${esc(m.opponent)}</span>
          <span class="side-num">${m.opp_proj.toFixed(1)}</span></div>
      </div>
      ${probMeter(m.win_prob)}
      <p class="matchup-line"><b>${esc(m.strategy.label)}.</b> ${esc(m.strategy.line)}</p>
      <p class="matchup-meta">${lead === "up" ? "Up" : "Down"}
        ${Math.abs(m.margin).toFixed(1)} on the projections · your range
        ${m.bands.floor}–${m.bands.ceiling} · his number is ${esc(m.opp_basis)} ·
        spread ±${m.sigma}, ${esc(m.sigma_note)}.</p>
      ${slateLine(m.slate)}
      ${altBlock}
    </div>`;
}

/* Whether the market has anything to say about this week at all. A board that
   applied no adjustment because nothing was priced must not look identical to
   one where every game happened to be average — most of a season sits at
   "nothing priced", and that is a fact about the board, not a blank. */
function slateLine(sl) {
  if (!sl || !sl.teams) return "";
  if (!sl.priced) {
    return `<p class="matchup-meta slate-none">No game on this slate carries a
      betting line yet — every man is being read as an average spot.</p>`;
  }
  const part = sl.priced < sl.teams
    ? ` The other ${sl.teams - sl.priced} are unpriced and read as average.` : "";
  return `<p class="matchup-meta">Matchups priced from the market on
    <b>${sl.priced}</b> of ${sl.teams} clubs, averaging ${sl.mean} implied
    (${esc(sl.note)}).${part}</p>`;
}

/* The beat rides along on This Week in every state, including pre-draft —
   in August it is the only live thing this room has to say. */
function beatPanel() {
  return `<section class="beat">
    <h3 class="beat-title">The Beat</h3>
    <p class="room-note">What the reporters filed, matched to this league.
    Your men ring the phone; everyone else's just reads here.</p>
    <div class="sheet"><div id="beat-feed"><p class="muted">Working the phones…</p></div></div>
  </section>`;
}

/* The same eleven men, shuffled between slots, is not a recommendation — a WR
   in your FLEX and a WR in your WR2 score exactly the same. A second table
   with different-looking rows and an identical total invites the reader to go
   fix something that isn't broken. Shared so the layout and the copy can never
   disagree about whether there are two lineups. */
function sameStarters(card) {
  return card.actual.length === card.optimal.length
    && card.actual.every((r) => card.optimal.some((o) => o.id === r.id));
}

function lineupBlock(card) {
  const inIds = new Set(card.swaps.map((s) => s.in_id));
  const outIds = new Set(card.swaps.map((s) => s.out_id));
  const actualMarks = new Map(card.actual.map((r) => [r.id, outIds.has(r.id) ? "is-out" : ""]));
  const optimalMarks = new Map(card.optimal.map((r) => [r.id, inIds.has(r.id) ? "is-in" : ""]));

  if (sameStarters(card)) {
    return `<div class="lineup-tables lineup-solo">
      ${lineupTable(`Your week ${card.week}`, card.actual, card.actual_total, actualMarks, card.news)}
    </div>
    <p class="optimal-note">This is already the optimal set — the Hungarian assignment
    starts the same eleven men. It would file two of them under different slot names,
    which scores identically, so there is no second table to compare against.</p>`;
  }

  return `<div class="lineup-tables">
    ${lineupTable(`Actual — week ${card.week}`, card.actual, card.actual_total, actualMarks, card.news)}
    ${lineupTable("Optimal", card.optimal, card.optimal_total, optimalMarks, card.news)}
  </div>
  <p class="optimal-note">Optimal is the Hungarian assignment over your roster's
  projections; actual is what the API says is started right now.</p>`;
}

async function pollWeek() {
  try {
    const card = await fetchJSON("/api/week/current");
    // DESIGN.md's motion rule: "the 1 Hz poll patches never re-fire anything."
    // The Board honours it by patching rows in place; This Week rebuilt its
    // whole innerHTML every 1500ms, so .sheet's room-in entry animation
    // replayed on identical data — measured at five rebuilds in seven seconds
    // with a byte-identical payload, which reads as THE BEAT flashing. A room
    // with nothing new to say should say nothing.
    const sig = JSON.stringify(card);
    if (!state.approving && sig !== state.weekSig) {
      renderWeek(card);
      state.weekSig = sig;
    }
    state.week = card;
    wireOK();
  } catch { wireFail(); }
}

/* --------------------------------- waivers -------------------------------- */
async function loadWaivers() {
  try {
    const data = await fetchJSON("/api/waivers");
    if (!changed("waivers", data, !$("#waivers-body")?.childElementCount)) { wireOK(); return; }
    const nextUp = (t) => {
      // The bid traps first: a target who can't help the week you bought him.
      if (t.bye_now) return `<span class="confirm-flag">${icon.flag}ON BYE NOW</span>`;
      if (t.bye_next) return `<span class="street">bye next wk</span>`;
      if (!t.opp) return "–";
      const imp = t.imp != null ? ` · imp ${t.imp}` : "";
      const wx = (t.wx || []).length ? ` <span class="street">${esc(t.wx.join(", "))}</span>` : "";
      return `${esc(t.opp)}${esc(imp)}${wx}`;
    };
    // The reason half of these are worth a bid isn't their score — it's that
    // somebody's starter left. Say so on the row, in the beat's own words.
    const why = (t) => {
      if (t.opening) return `<span class="opening">${icon.flag}JOB OPEN — ${esc(t.opening.name)}:
        ${esc(t.opening.headline.toLowerCase())}</span>`;
      if (t.news) return `<span class="street" title="${esc(t.news.headline)}">${esc(t.news.headline)}</span>`;
      return t.hard_confirm ? `<span class="confirm-flag">${icon.flag}BIG SWING — CONFIRM TWICE</span>` : "";
    };
    // A column of nothing but dashes is furniture. The street (Sleeper's
    // trending adds) is empty before the season and Next up is empty until the
    // schedule is loaded — in both cases the honest thing is to not draw the
    // column at all rather than rule six rows of "–" across the board.
    const hasStreet = data.targets.some((t) => t.heat);
    const hasNextUp = data.targets.some((t) => t.bye_now || t.bye_next || t.opp);

    const rows = data.targets.map((t) => `
      <tr><td><span class="pname">${esc(t.name)}</span> <span class="team">${esc(t.team ?? "")}</span>
        <div><span class="pos pos-${posOf(t)}">${posOf(t)}</span></div></td>
      <td class="num" title="${t.fa_score} more season points than the weakest ${esc(posOf(t))} on your shelf. Measured against YOUR ${esc(posOf(t))}s only — it does not compare across positions.">${t.fa_score}<div class="bid-why">over your ${esc(posOf(t))}s</div></td>
      <td class="num"><span class="bid">$${t.bid}</span>${t.value_pct == null ? ""
        : `<div class="bid-why">P${Math.round(t.value_pct * 100)} of this room's book</div>`}</td>
      ${hasStreet ? `<td class="num street">${t.heat ? `${t.heat.toLocaleString()} adds` : "–"}</td>` : ""}
      <td class="num">${t.lineup_gain == null ? "–"
        : t.lineup_gain > 0 ? `<b>starts · +${t.lineup_gain}</b>` : `<span class="street">depth</span>`}</td>
      ${hasNextUp ? `<td class="num">${nextUp(t)}</td>` : ""}
      <td>${why(t)}</td></tr>`).join("");
    // Every add is a drop. A bid the owner can't execute is half an answer.
    const drop = data.targets[0]?.drop;
    const dropLine = drop
      ? `<p class="drop-line">To make room: <b>${esc(drop.name)}</b>
         <span class="pos pos-${esc(drop.pos)}">${esc(drop.pos)}</span> — cutting him costs the
         optimal lineup ${drop.cost === 0 ? "nothing" : `${drop.cost} pts`}${
           drop.injury ? `, and he's carrying a ${esc(drop.injury)} tag` : ""}.</p>`
      : `<p class="drop-line">No spare body on the shelf — an add here means cutting a starter.</p>`;
    $("#waivers-body").innerHTML = data.targets.length ? `
      <table class="wtable">
        <thead><tr><th>Player</th>
        <th style="text-align:right" title="Season points over the weakest body at his own position on your shelf. Each row names the position it is measured against, because two of these are only comparable when they share one.">Over your worst</th>
        <th style="text-align:right">Bid</th>
        ${hasStreet ? `<th style="text-align:right">The street</th>` : ""}
        <th style="text-align:right">Your lineup</th>
        ${hasNextUp ? `<th style="text-align:right">Next up</th>` : ""}
        <th></th></tr></thead>
        <tbody>${rows}</tbody></table>
      ${dropLine}
      <p class="optimal-note">Ranked by what each man is worth <b>to this shelf</b> — the lineup
      he'd crack today, plus a share of the column on the left for the bye and the injury you
      haven't had yet. That ranking is the percentile under each bid, and the bid is that
      percentile of ${data.history_n} winning bids this room has actually paid, rounded to the
      +$1 over a round number. Depth pays half a starter's price — which is why the biggest
      number on the left is not always the biggest number in the middle.</p>`
      : `<p class="muted">${esc(data.note || "Nobody on the street worth a dollar this week.")}</p>`;
    wireOK();
  } catch { wireFail(); }
}

/* --------------------------------- the league ----------------------------- */
const LEAGUE_POS = ["QB", "RB", "WR", "TE", "K", "DEF"];

/* The grid's glyphs are cut straight from the z-score, so a cell says how far
   off the field that room sits — not merely which side of average it's on. */
function zCell(z) {
  if (z >= 1.5) return { g: "++", c: "g-pp" };
  if (z >= 0.8) return { g: "+", c: "g-p" };
  if (z <= -1.5) return { g: "––", c: "g-nn" };
  if (z <= -0.8) return { g: "–", c: "g-n" };
  return { g: "·", c: "g-z" };
}

function seatRecord(rec, ready) {
  if (!ready || !rec) return `<span class="street">–</span>`;
  return `<b>${rec.wins}–${rec.losses}${rec.ties ? `–${rec.ties}` : ""}</b>`;
}

/* Live carries the real Sleeper handle, where this chip is the only thing
   marking which seat is mine. The demo already names that seat "You" — so
   don't stamp it twice. */
function youChip(s) {
  return s.mine && s.owner.trim().toLowerCase() !== "you"
    ? ` <span class="seat-you">YOU</span>` : "";
}

async function loadLeague() {
  try {
    const [ov, rost] = await Promise.all([
      fetchJSON("/api/league/overview"),
      fetchJSON("/api/league/rosters"),
    ]);
    if (!changed("league", [ov, rost], !$("#league-body")?.childElementCount)) { wireOK(); return; }
    const depth = new Map(rost.rosters.map((r) => [r.roster_id, r.players]));
    const me = ov.seats.find((s) => s.mine);
    const myNeed = new Set(me ? me.need : []);
    const mySurplus = new Set(me ? me.surplus : []);

    const table = ov.seats.map((s) => {
      const rec = seatRecord(s.record, ov.records_ready);
      const pf = ov.records_ready && s.record
        ? s.record.fpts.toLocaleString(undefined, { maximumFractionDigits: 0 })
        : "–";
      const roster = depth.get(s.roster_id) || [];
      const chart = LEAGUE_POS.map((pos) => {
        const room = roster.filter((p) => posOf(p) === pos);
        if (!room.length) return "";
        return `<div class="depth-room"><span class="pos pos-${pos}">${pos}</span>
          ${room.slice(0, 5).map((p) =>
          `<span class="depth-p">${esc(p.name)} <i>${p.pts.toFixed(0)}</i></span>`).join("")}</div>`;
      }).join("");
      return `
        <tr class="seat-row${s.mine ? " is-mine" : ""}" data-seat="${s.roster_id}"
            tabindex="0" role="button" aria-expanded="false"
            aria-label="Open the depth chart — ${esc(s.owner)}">
          <td class="num seat-rank">${s.rank}</td>
          <td><span class="seat-owner">${esc(s.owner)}</span>${youChip(s)}</td>
          <td class="num">${rec}</td>
          <td class="num street">${pf}</td>
          <td class="num"><b>${s.proj.toFixed(1)}</b></td>
          <td class="seat-read">${esc(s.read)}</td>
        </tr>
        <tr class="seat-depth" data-depth="${s.roster_id}" hidden>
          <td colspan="6"><div class="depth-wrap">${chart}</div></td>
        </tr>`;
    }).join("");

    const grid = ov.seats.map((s) => {
      const cells = LEAGUE_POS.map((pos) => {
        const room = s.by_pos[pos];
        const cell = zCell(room.z);
        // A fit is where one seat's surplus meets the other's need. Only ever
        // marked on someone else's row — you cannot trade with yourself.
        const fit = !s.mine && (
          (s.surplus.includes(pos) && myNeed.has(pos)) ||
          (s.need.includes(pos) && mySurplus.has(pos)));
        return `<td class="gcell ${cell.c}${fit ? " is-fit" : ""}"
          title="${esc(s.owner)} ${pos}: ${room.pts} pts from ${room.depth} bodies${
          fit ? " — fits your room" : ""}">${cell.g}</td>`;
      }).join("");
      return `<tr class="${s.mine ? "is-mine" : ""}">
        <td class="gname">${esc(s.owner)}${youChip(s)}</td>
        ${cells}</tr>`;
    }).join("");

    const fits = ov.seats.filter((s) => !s.mine && LEAGUE_POS.some((pos) =>
      (s.surplus.includes(pos) && myNeed.has(pos)) ||
      (s.need.includes(pos) && mySurplus.has(pos)))).length;

    $("#league-body").innerHTML = `
      ${ov.note ? `<p class="muted">${esc(ov.note)}</p>` : ""}
      <table class="wtable league-table">
        <thead><tr>
          <th style="text-align:right">#</th><th>Seat</th>
          <th style="text-align:right">Rec</th><th style="text-align:right">PF</th>
          <th style="text-align:right">Can start</th><th>The read</th>
        </tr></thead>
        <tbody>${table}</tbody>
      </table>

      <h3 class="grid-title">The rooms, against the field</h3>
      <p class="room-note">${me
        ? `Highlighted cells are where this league's shape meets yours —
           ${fits ? `${fits} seat${fits === 1 ? "" : "s"} worth a knock.`
          : "nothing obvious right now."}`
        : "Your seat isn't on the board yet."}</p>
      <table class="wtable gridtable">
        <thead><tr><th>Seat</th>${LEAGUE_POS.map((p) =>
        `<th class="gcol">${p === "DEF" ? "D/ST" : p}</th>`).join("")}</tr></thead>
        <tbody>${grid}</tbody>
      </table>
      <div id="sos-block"></div>`;
    loadSos();

    $("#league-body").querySelectorAll(".seat-row").forEach((row) => {
      const toggle = () => {
        const body = $(`#league-body [data-depth="${row.dataset.seat}"]`);
        const open = body.hidden;
        body.hidden = !open;
        row.setAttribute("aria-expanded", String(open));
        row.classList.toggle("is-open", open);
      };
      row.addEventListener("click", toggle);
      row.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
      });
    });
    wireOK();
  } catch { wireFail(); }
}

/* Strength of schedule, as far as the market has priced it. Deliberately the
   last thing on The League and deliberately advisory: two weeks of betting
   lines is not a season, and a number shaped like one would be the more
   dangerous half of the truth. The coverage sits beside every row for the
   same reason. */
async function loadSos() {
  const el = $("#sos-block");
  if (!el) return;
  try {
    const d = await fetchJSON("/api/schedule/strength");
    if (!d.priced) {
      el.innerHTML = `<h3 class="grid-title">The road ahead</h3>
        <p class="room-note slate-none">No game this season carries a betting
        line yet — the schedule read opens as books post them.</p>`;
      return;
    }
    const rows = d.teams.filter((t) => t.weeks > 0);
    const cell = (t) => {
      const v = t.vs_league;
      const cls = v > 1 ? "g-p" : v < -1 ? "g-n" : "";
      return `<tr><td class="gname">${esc(t.team)}</td>
        <td class="num"><span class="${cls}">${v > 0 ? "+" : ""}${v.toFixed(1)}</span></td>
        <td class="num street">${t.mean_implied.toFixed(1)}</td>
        <td class="street">${esc(t.covered)}</td></tr>`;
    };
    el.innerHTML = `<h3 class="grid-title">The road ahead</h3>
      <p class="room-note">Each club's priced games against the league's
      ${d.league_mean} implied average. ${esc(d.advisory)}</p>
      <div class="scroller"><table class="wtable sos-table">
        <thead><tr><th>Club</th><th style="text-align:right">vs league</th>
        <th style="text-align:right">implied</th><th>priced</th></tr></thead>
        <tbody>${rows.map(cell).join("")}</tbody></table></div>
      <p class="optimal-note">${d.priced} of ${d.total} club-weeks priced.</p>`;
  } catch { /* the wire indicator owns the failure */ }
}

/* --------------------------------- parlor --------------------------------- */
function sideList(ps) {
  return ps.map((p) =>
    `<span class="pos pos-${posOf(p)}">${posOf(p)}</span> ${esc(p.name)}`).join(" · ");
}

async function loadParlor() {
  try {
    const data = await fetchJSON("/api/trades/suggest");
    if (!changed("parlor", data, !$("#parlor-body")?.childElementCount)) { wireOK(); return; }
    const considered = data.considered
      ? `<p class="room-note considered">${data.considered} packages found;
         ${data.trades.length} worth reading. The rest were the same deal with different
         men attached, or one seat's whole surplus.</p>` : "";
    $("#parlor-body").innerHTML = data.trades.length ? considered + data.trades.map((t) => `
      <div class="deal">
        <div class="deal-head">
          <span class="deal-partner">with ${esc(t.partner)}</span>
          <span class="deal-gains"><b>${t.my_gain > 0 ? "+" : ""}${t.my_gain}</b> you ·
            ${t.their_gain > 0 ? "+" : ""}${t.their_gain} them</span>
        </div>
        <div class="deal-sides">
          <div class="deal-side"><span class="lbl">send</span> ${sideList(t.give)}</div>
          <div class="deal-side"><span class="lbl">get</span> ${sideList(t.receive)}</div>
        </div>
        <p class="deal-verdict v-${esc(t.verdict?.level || "note")}">${esc(t.verdict?.line || "")}</p>
        <p class="deal-summary">${esc(t.summary)}</p>
      </div>`).join("")
      : `<p class="muted">${esc(data.note || "Nothing worth whispering this week.")}</p>`;
    initDealChecker();
    wireOK();
  } catch { wireFail(); }
}

/* ---- the back table: run your own deal by the room ----------------------
   Constraint-based input on purpose: both pools are the actual rosters, so
   an unknown-player error can't happen. Any change to the table hides the
   previous verdict — a read must never sit next to a deal it wasn't run on. */
const deal = { rosters: null, partner: null, give: new Set(), get: new Set(), running: false };

function dealRowBtn(p, side) {
  const on = (side === "give" ? deal.give : deal.get).has(p.id);
  return `<button type="button" class="deal-row ${on ? "is-in" : ""}" data-id="${esc(p.id)}"
    data-side="${side}" aria-pressed="${on}">
    <span class="pos pos-${posOf(p)}">${posOf(p)}</span>
    <span class="deal-row-name">${esc(p.name)}</span>
    <span class="deal-row-pts">${p.pts.toFixed(1)}</span></button>`;
}

function renderDealPools() {
  const mine = deal.rosters.find((r) => r.mine);
  const theirs = deal.rosters.find((r) => r.roster_id === deal.partner);
  $("#deal-mine").innerHTML = mine.players.map((p) => dealRowBtn(p, "give")).join("");
  $("#deal-theirs").innerHTML = (theirs?.players || []).map((p) => dealRowBtn(p, "get")).join("");
  $("#deal-run").disabled = deal.running || !deal.give.size || !deal.get.size;
}

async function initDealChecker() {
  if (deal.rosters) return;
  try {
    const data = await fetchJSON("/api/league/rosters");
    const mine = data.rosters.find((r) => r.mine);
    if (!mine || !mine.players.length) return;   // pre-draft: the table stays folded
    deal.rosters = data.rosters;
    const sel = $("#deal-partner");
    sel.innerHTML = data.rosters.filter((r) => !r.mine).map((r) =>
      `<option value="${r.roster_id}">${esc(r.owner)}</option>`).join("");
    deal.partner = Number(sel.value);
    $("#deal-checker").hidden = false;
    renderDealPools();
  } catch { /* the suggestions above still render; the table just stays folded */ }
}

$("#deal-partner").addEventListener("change", (e) => {
  deal.partner = Number(e.target.value);
  deal.get.clear();                 // their pool changed — old picks are meaningless
  $("#deal-verdict").textContent = "";
  renderDealPools();
});

for (const poolId of ["#deal-mine", "#deal-theirs"])
  $(poolId).addEventListener("click", (e) => {
    const btn = e.target.closest(".deal-row");
    if (!btn) return;
    const set = btn.dataset.side === "give" ? deal.give : deal.get;
    set.has(btn.dataset.id) ? set.delete(btn.dataset.id) : set.add(btn.dataset.id);
    $("#deal-verdict").textContent = "";
    renderDealPools();
  });

$("#deal-clear").addEventListener("click", () => {
  deal.give.clear(); deal.get.clear();
  $("#deal-verdict").textContent = "";
  renderDealPools();
});

function fmtDelta(n) { return n == null ? "–" : `${n > 0 ? "+" : ""}${n.toFixed(1)}`; }

$("#deal-run").addEventListener("click", async () => {
  const btn = $("#deal-run");
  deal.running = true;
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span>Running it…';
  try {
    const v = await fetchJSON("/api/trades/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ give: [...deal.give], receive: [...deal.get],
                             their_roster_id: deal.partner }),
    });
    const edges = [
      `value ${fmtDelta(v.vbd_edge)} VBD`,
      `market ${v.market_edge > 0 ? "+" : ""}${Math.round(v.market_edge)}`,
      v.consolidation_edge != null ? `package ${fmtDelta(v.consolidation_edge)}` : null,
    ].filter(Boolean).join(" · ");
    $("#deal-verdict").innerHTML = `
      <div class="deal deal-verdict-card">
        <div class="deal-head">
          <span class="deal-partner">the room's read</span>
          <span class="deal-gains"><b>${fmtDelta(v.lineup_impact?.starters_delta)}</b> you ·
            ${fmtDelta(v.their_lineup_impact?.starters_delta)} them</span>
        </div>
        <p class="deal-summary">${esc(v.summary)}</p>
        <p class="optimal-note">${edges} — the you/them figures are season-total
        optimal-lineup points, positive means that side's starters improve.</p>
      </div>`;
  } catch {
    $("#deal-verdict").innerHTML =
      `<p class="muted">The room couldn't get a read — the wire hiccuped. Run it again.</p>`;
  }
  deal.running = false;
  btn.innerHTML = "Run it by the room";
  renderDealPools();
});

/* --------------------------------- ledger --------------------------------- */
function ruleDetail(r) {
  if (r.threshold == null) return "";
  if (r.name.startsWith("questionable")) return `trips inside ${r.threshold}h of kickoff`;
  if (r.name.startsWith("source_disagreement")) return `trips over ${Math.round(r.threshold * 100)}% spread`;
  return "";
}

async function loadLedger() {
  try {
    const [rules, audit] = await Promise.all([
      fetchJSON("/api/rules"), fetchJSON("/api/audit")]);
    if (!changed("ledger", [rules, audit], !$("#rules-body")?.childElementCount)) { wireOK(); return; }
    $("#rules-body").innerHTML = rules.map((r) => `
      <div class="rule-row">
        <span class="rule-name">${esc(r.name.replaceAll("_", " "))}</span>
        ${ruleDetail(r) ? `<span class="rule-th">${esc(ruleDetail(r))}</span>` : ""}
        <input type="checkbox" class="toggle" data-rule="${esc(r.rule_id)}"
          ${r.enabled ? "checked" : ""} aria-label="Toggle ${esc(r.name)}">
      </div>`).join("");
    document.querySelectorAll(".toggle[data-rule]").forEach((t) =>
      t.addEventListener("change", async () => {
        try { await fetchJSON(`/api/rules/${t.dataset.rule}/toggle`, { method: "POST" }); }
        catch { t.checked = !t.checked; wireFail(); }
      }));
    $("#audit-body").innerHTML = audit.length ? audit.map((a) => {
      const cls = /ok|verified|done/.test(a.step) ? "ok"
        : /fail|mismatch|violation|expired|rule/.test(a.step) ? "bad" : "";
      return `<div class="ledger-line">
        <span class="ts">${esc((a.ts || "").replace("T", " ").slice(0, 19))}</span>
        <span>rec #${esc(a.rec_id ?? "–")}</span>
        <span class="step-name ${cls}">${esc(a.step)}</span>
        ${a.screenshot_path ? `<span>shot · ${esc(a.screenshot_path)}</span>` : ""}
      </div>`;
    }).join("") : `<div class="ledger-empty">Nothing on the books yet.</div>`;
    wireOK();
  } catch { wireFail(); }
}

/* ------------------------------ the report card ---------------------------
   Grades appear once, when the bound draft (real or scrimmage) completes —
   every seat on the league's own curve, my row opened up. */
// Keys mirror engines/grades.WEIGHTS — rename a metric server-side and this
// map must follow, or the UI shows the raw key.
const COMP_LABELS = { starters: "starting nine", vbd: "value", surplus: "discounts",
                      depth: "the shelf", risk: "sturdiness" };

function gradeCls(g) {
  return { A: "gA", B: "gB", C: "gC" }[g[0]] || "gD";
}

async function maybeLoadGrades() {
  const d = state.board?.draft;
  const el = $("#report-card");
  if (!d || d.status !== "complete") {
    el.hidden = true;
    state.gradesFor = null;
    return;
  }
  if (state.gradesFor === d.id) return;
  try {
    const g = await fetchJSON("/api/draft/grades");
    if (!g.ready) { el.hidden = true; return; }
    state.gradesFor = d.id;
    renderReportCard(g);
    el.hidden = false;
  } catch { /* the board stands without the card */ }
}

/* The rank is a weighted composite of five z-scores, so no single printed
   column falls monotonically down the table. Without the composite itself on
   screen the ordering looks like a broken sort — this draws it: a bar left of
   centre for below the room, right for above. */
function compositeBar(z) {
  const c = Math.max(-2, Math.min(2, Number(z) || 0));
  const half = Math.abs(c) / 2 * 50;
  const side = c >= 0 ? `left:50%;width:${half}%` : `right:50%;width:${half}%`;
  return `<span class="cbar" title="composite z-score ${c.toFixed(2)}">
    <span class="cbar-fill ${c >= 0 ? "is-up" : "is-down"}" style="${side}"></span></span>
    <span class="cbar-num">${c >= 0 ? "+" : ""}${c.toFixed(2)}</span>`;
}

function renderReportCard(g) {
  const rows = g.teams.map((t) => `
    <tr class="${t.mine ? "is-me" : ""}">
      <td class="num">${t.rank}</td>
      <td><span class="gradechip ${gradeCls(t.grade)}">${esc(t.grade)}</span></td>
      <td>${esc(t.owner)}${t.mine ? ' <span class="me-tag">YOU</span>' : ""}</td>
      <td class="num" title="season-total points of the optimal starting lineup">${t.starters.toFixed(1)}</td>
      <td class="num" title="picks of market value captured vs ADP — positive means discounts">${t.surplus > 0 ? "+" : ""}${Math.round(t.surplus)}</td>
      <td class="rc-comp-cell">${compositeBar(t.composite)}</td>
      <td class="rc-note">${esc(t.note)}</td>
    </tr>`).join("");
  const me = g.teams.find((t) => t.mine);
  const comps = me ? Object.entries(me.components).map(([k, c]) => `
    <span class="rc-comp"><span class="lbl">${esc(COMP_LABELS[k] || k)}</span>
      <span class="gradechip ${gradeCls(c.grade)}">${esc(c.grade)}</span></span>`).join("") : "";
  const detail = me ? `
    <div class="rc-mine">
      <div class="rc-comps">${comps}</div>
      ${me.best_pick ? `<p>Best call: <b>${esc(me.best_pick.name)}</b> at P${me.best_pick.pick_no}
        — the room had him ${Math.round(Math.max(0, me.best_pick.surplus))} picks earlier (ADP ${me.best_pick.adp}).</p>` : ""}
      ${me.reach && me.reach.surplus < -3 ? `<p>The reach: <b>${esc(me.reach.name)}</b> at
        P${me.reach.pick_no}, ${Math.round(-me.reach.surplus)} picks before his ADP of ${me.reach.adp}.</p>` : ""}
    </div>` : "";
  $("#report-card").innerHTML = `
    <h2 class="room-title">The Report Card${g.practice ? " — scrimmage" : ""}</h2>
    ${g.steal ? `<p class="room-note">Steal of the draft: <b>${esc(g.steal.name)}</b> to
      ${esc(g.steal.owner)} at P${g.steal.pick_no} — the room had him
      ${Math.round(g.steal.surplus)} picks earlier.</p>` : ""}
    <table class="wtable rc-table"><thead><tr>
      <th></th><th></th><th>Seat</th><th style="text-align:right">proj starters</th>
      <th style="text-align:right">value</th><th style="text-align:right">composite</th>
      <th>the read</th></tr></thead>
      <tbody>${rows}</tbody></table>
    <p class="optimal-note">Seats are ranked by the <b>composite</b> — starting nine (45%),
    value (20%), discounts (15%), depth (10%) and injury risk (10%), each as a z-score across
    this room. That is why proj starters does not fall straight down the column: a seat can
    out-project the field and still grade behind one that paid less for it.</p>
    ${detail}`;
}

/* ------------------------------ the slip + pilot --------------------------
   The Slip is the user's ordered pick list; the pilot (hands/draft_pilot.py,
   a separate opt-in worker) takes slip-first-else-The-Call. The UI only
   edits the plan and the armed flag — it never picks anything itself. */
state.slipIds = [];

async function loadSlip() {
  try {
    const data = await fetchJSON("/api/queue");
    state.slipIds = data.queue.map((p) => p.id);
    $("#slip-list").innerHTML = data.queue.length ? data.queue.map((p, i) => `
      <li class="slip-row ${p.picked ? "is-gone" : ""}" data-id="${esc(p.id)}">
        <span class="slip-n">${i + 1}</span>
        <span class="pos pos-${posOf(p)}">${posOf(p)}</span>
        <span class="slip-name">${esc(p.name)}</span>
        <span class="slip-acts">
          <button data-act="up" aria-label="Move ${esc(p.name)} up">↑</button>
          <button data-act="down" aria-label="Move ${esc(p.name)} down">↓</button>
          <button data-act="rm" aria-label="Remove ${esc(p.name)}">✕</button>
        </span>
      </li>`).join("")
      : `<li class="muted slip-empty">Empty slip — the pilot would fly The Call alone.</li>`;
    const armed = data.pilot_armed;
    $("#pilot-status").textContent = armed
      ? `ARMED${data.pilot_dry_run ? " · dry run — logs only" : " · LIVE"}`
      : `parked${data.pilot_ready ? "" : " · auth lives with the pilot host"}${data.pilot_dry_run ? " · dry run" : ""}`;
    $("#pilot-arm").textContent = armed ? "Disarm the pilot" : "Arm the pilot";
    $("#pilot-banner").hidden = !armed;
  } catch { /* rail panel only — the board stands */ }
}

async function saveSlip() {
  await practiceCall("/api/queue", { ids: state.slipIds });
  loadSlip();
}

$("#slip-list").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.closest(".slip-row").dataset.id;
  const i = state.slipIds.indexOf(id);
  if (i < 0) return;
  if (btn.dataset.act === "rm") state.slipIds.splice(i, 1);
  if (btn.dataset.act === "up" && i > 0)
    [state.slipIds[i - 1], state.slipIds[i]] = [state.slipIds[i], state.slipIds[i - 1]];
  if (btn.dataset.act === "down" && i < state.slipIds.length - 1)
    [state.slipIds[i + 1], state.slipIds[i]] = [state.slipIds[i], state.slipIds[i + 1]];
  saveSlip().catch(() => wireFail());
});

let _slipTimer = null;
$("#slip-search").addEventListener("input", (e) => {
  clearTimeout(_slipTimer);
  const q = e.target.value.trim();
  if (q.length < 2) { $("#slip-results").hidden = true; return; }
  _slipTimer = setTimeout(async () => {
    try {
      const rs = await fetchJSON(`/api/players/search?q=${encodeURIComponent(q)}`);
      $("#slip-results").innerHTML = rs.length ? rs.map((r) => `
        <button type="button" data-id="${esc(r.id)}">
          <span class="pos pos-${r.pos === "DST" ? "DEF" : esc(r.pos)}">${r.pos === "DST" ? "DEF" : esc(r.pos)}</span>
          ${esc(r.name)} <span class="team">${esc(r.team ?? "")}</span></button>`).join("")
        : `<p class="muted">nobody by that name</p>`;
      $("#slip-results").hidden = false;
    } catch { /* search is a convenience */ }
  }, 250);
});
$("#slip-results").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-id]");
  if (!b) return;
  if (!state.slipIds.includes(b.dataset.id)) state.slipIds.push(b.dataset.id);
  $("#slip-search").value = "";
  $("#slip-results").hidden = true;
  saveSlip().catch(() => wireFail());
});

$("#slip-fill").addEventListener("click", () => {
  for (const s of (state.board?.suggestions || []).slice(0, 5))
    if (!state.slipIds.includes(s.id)) state.slipIds.push(s.id);
  saveSlip().catch(() => wireFail());
});

async function setPilot(armed) {
  try { await practiceCall("/api/pilot/arm", { armed }); } catch { wireFail(); }
  loadSlip();
}
$("#pilot-arm").addEventListener("click", () => {
  const arming = $("#pilot-arm").textContent.startsWith("Arm");
  if (arming && !confirm(
    "Arm the pilot?\n\nWhen the pilot worker is running with dry-run OFF, " +
    "Bootlegger will DRAFT FOR YOU whenever you're on the clock — the slip " +
    "first, The Call when it runs dry. Disarm any time.")) return;
  setPilot(arming);
});
$("#pilot-disarm").addEventListener("click", () => setPilot(false));

/* -------------------------------- scrimmage -------------------------------
   Practice rooms are invisible on Sleeper's listing APIs, so the only way in
   is the room's URL. The server validates the id against Sleeper before
   binding — a typo fails loudly here, not silently on the wire. */
/* Not fetchJSON on purpose: fetchJSON throws away the response body on a
   non-2xx, and scrimmage errors carry their message in the server's JSON
   `detail` ("that's the league draft", "the league draft is LIVE", …). */
async function practiceCall(path, body) {
  const opts = { method: "POST", headers: { "Content-Type": "application/json" } };
  if (TOKEN) opts.headers["X-Bootlegger-Token"] = TOKEN;
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `the wire said ${r.status}`);
  return data;
}

$("#scrimmage-go").addEventListener("click", async () => {
  const input = $("#scrimmage-url"), msg = $("#scrimmage-msg"), btn = $("#scrimmage-go");
  if (!input.value.trim()) { msg.textContent = "Paste the practice room's link first."; return; }
  btn.disabled = true;
  msg.textContent = "Checking the room with Sleeper…";
  try {
    const res = await practiceCall("/api/practice", { url: input.value.trim() });
    msg.textContent = `Tracking it — the wire turns over in a couple seconds (room is ${res.status.replace("_", " ")}).`;
    input.value = "";
  } catch (e) { msg.textContent = e.message; }
  btn.disabled = false;
});
$("#scrimmage-url").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("#scrimmage-go").click();
});

$("#practice-clear").addEventListener("click", async () => {
  try {
    await practiceCall("/api/practice/clear");
    $("#scrimmage-msg").textContent = "Scrimmage over — rebinding to the league draft.";
    state.builtPickCount = -1;   // full rebuild: the practice picks must vanish
  } catch { wireFail(); }
});

/* ---------------------------------- boot ---------------------------------- */
$("#reset-mock").addEventListener("click", async () => {
  try { await fetchJSON("/api/draft/reset", { method: "POST" }); state.builtPickCount = -1; pollBoard(); }
  catch { wireFail(); }
});

async function boot() {
  // A hash in the URL beats the remembered room — a link you followed should
  // land where it points, not where you last were.
  const hashed = location.hash.replace(/^#/, "");
  roomPinned = ROOMS.includes(hashed);
  setTab(roomPinned ? hashed : state.tab);
  try {
    state.health = await fetchJSON("/health");
    if (state.health.mode === "demo") $("#reset-mock").hidden = false;
    // Source health in the colophon: a dead scrape must never be a secret.
    const h = state.health;
    if (h.sources_live != null) {
      const note = h.sources_missing.length
        ? ` · ${h.sources_live}/${h.sources_expected} projection sources on the wire — down: ${h.sources_missing.join(", ")}`
        : ` · all ${h.sources_expected} projection sources on the wire`;
      const el = document.querySelector(".colophon p");
      if (el) el.append(note);
      if (h.sources_missing.length) {
        state.sourcesShort = `${h.sources_live}/${h.sources_expected}`;
        $("#wire").title = `sources down: ${h.sources_missing.join(", ")}`;
      }
    }
    wireOK(); renderWire();
  } catch { wireFail(); }
  await pollBoard();   // runs applyPhase — a phase turn re-homes the tab here
  chipDefault();
  loadSlip();
  state.booted = true;
  await pollWeek();
  // Adaptive cadence: 1s while the draft is live (the server caches the board
  // per pick, so fast polling is nearly free), relaxed otherwise.
  (function boardLoop() {
    const drafting = state.board?.draft?.status === "drafting";
    setTimeout(async () => { await pollBoard(); boardLoop(); }, drafting ? 1000 : 2500);
  })();
  setInterval(() => {
    const fast = Date.now() < state.fastWeekUntil;
    if (state.tab === "week" || fast) pollWeek();
  }, 1500);
  setInterval(() => {
    if (state.tab === "waivers") loadWaivers();
    if (state.tab === "ledger") loadLedger();
    if (state.tab === "board") loadSlip();   // picked strikethroughs + pilot state
    // The beat used to refresh only as a side effect of This Week rebuilding
    // itself. Now that the room holds still on unchanged data, the wire needs
    // its own clock — and 15s is already far faster than the server polls it.
    if (state.tab === "week") loadBeat();
  }, 15000);
  setInterval(() => {
    if (state.tab === "parlor") loadParlor();  // full-league scan — slower cadence
    if (state.tab === "league") loadLeague();  // twelve rosters + z-scores; same
  }, 60000);
}
boot();
