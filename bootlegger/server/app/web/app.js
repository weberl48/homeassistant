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
  if (!state.booted || status === "drafting") setTab(PHASE_ROOM[status] || "board");
}

/* ---------------------------------- icons -------------------------------- */
/* The ref: cartoon zebra with rotatable arms — TOUCHDOWN pose on your clock. */
const REF_SVG = (td) => `
<svg class="ref ${td ? "is-td" : ""}" viewBox="0 0 48 62" aria-hidden="true">
  <g fill="none" stroke="#20241f" stroke-width="2" stroke-linecap="round">
    <circle cx="24" cy="13" r="8" fill="#f0c9a0"/>
    <path d="M16 10a8 8 0 0 1 16 0z" fill="#20241f"/>
    <rect x="15" y="24" width="18" height="20" rx="5" fill="#f2f7ef"/>
    <path d="M15 29h18M15 34h18M15 39h18" stroke="#20241f" stroke-width="3.4"/>
    <g class="arm-l"><path d="M17 30 L7 42" stroke="#f2f7ef" stroke-width="4.6"/><circle cx="7" cy="42" r="2.6" fill="#f0c9a0" stroke="none"/></g>
    <g class="arm-r"><path d="M31 30 L41 42" stroke="#f2f7ef" stroke-width="4.6"/><circle cx="41" cy="42" r="2.6" fill="#f0c9a0" stroke="none"/></g>
    <path d="M20 44 L18 58 M28 44 L30 58" stroke="#20241f" stroke-width="4"/>
  </g>
</svg>`;

const CHALK_LIVE = `
<svg class="chalk-live" viewBox="0 0 150 100" aria-hidden="true" fill="none"
  stroke="#f2f7ef" stroke-width="2.2" stroke-linecap="round">
  <circle cx="22" cy="82" r="7" stroke-opacity=".55"/>
  <path class="route" d="M28 76 C 52 66, 60 48, 78 44 S 118 30, 138 12" stroke-dasharray="7 8"/>
  <path d="M138 12l-11 1M138 12l-4 10" stroke-opacity=".8"/>
</svg>`;

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
document.querySelectorAll(".tab").forEach((b) =>
  b.addEventListener("click", () => setTab(b.dataset.tab)));

function setTab(tab) {
  state.tab = tab;
  localStorage.setItem("bootlegger.tab", tab);
  document.querySelectorAll(".tab").forEach((b) => {
    const active = b.dataset.tab === tab;
    b.classList.toggle("is-active", active);
    if (active) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  for (const room of ["board", "week", "waivers", "parlor", "ledger"])
    $(`#room-${room}`).hidden = room !== tab;
  if (tab === "waivers") loadWaivers();
  if (tab === "parlor") loadParlor();
  if (tab === "ledger") loadLedger();
  if (tab === "week") pollWeek();
}

/* ------------------------------- draft board ------------------------------ */
function posOf(p) { return p.pos === "DST" ? "DEF" : p.pos; }

function fmtSurv(s) { return `${Math.round(s * 100)}%`; }

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
        <span title="value over the last starter at his position"><span class="lbl">vbd</span><b data-vbd>${p.vbd}</b></span>
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
    stamp.textContent = p.mine ? `MINE · P${p.pick_no}` : `P${p.pick_no}`;
    surv.hidden = true;
    if (justPicked) {
      el.classList.add("just-picked");
      setTimeout(() => el.classList.remove("just-picked"), 1400);
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
    rule.innerHTML = `The deep shelf <span class="shelf-n">${n} more</span>`;
    rule.addEventListener("click", () => col.classList.toggle("shelf-open"));
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
    $("#clock-line").textContent = "AWAITING KICKOFF";
    $("#clock-sub").textContent = "the draft hasn't started — the board warms up";
    return;
  }
  if (d.status === "complete") {
    $("#clock-line").textContent = "DRAFT COMPLETE";
    $("#clock-sub").textContent = `${d.rounds} rounds in the books`;
  } else if (d.on_the_clock_me) {
    $("#clock-line").textContent = `YOU'RE ON THE CLOCK — PICK ${d.current_pick}`;
    const pick = state.board?.suggestions?.[0];
    $("#clock-sub").textContent = pick
      ? `the room likes ${pick.name}` : "the room is thinking";
  } else {
    $("#clock-line").textContent = `PICK ${d.current_pick} OF ${d.total_picks} · ROUND ${d.round}`;
    $("#clock-sub").textContent = d.my_next_pick
      ? `slot ${d.on_clock_slot} on the clock · you pick at #${d.my_next_pick}`
      : "no picks left for you";
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

function renderTicker(picks) {
  $("#ticker").innerHTML = picks.map((p) => `
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
  $("#dossier").hidden = false;
}

$("#dossier-close").addEventListener("click", () => { $("#dossier").hidden = true; });
$("#dossier").addEventListener("click", (e) => {
  if (e.target === $("#dossier")) $("#dossier").hidden = true;
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("#dossier").hidden = true;
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
    // Still clickable — inside, each explains when it opens. The season rooms
    // follow the REAL league, so a finished scrimmage never lights them.
    document.querySelectorAll(".tab").forEach((b) => {
      if (["week", "waivers", "parlor"].includes(b.dataset.tab))
        b.classList.toggle("is-dormant", status !== "complete" || practice);
    });
    wireOK();
  } catch { wireFail(); }
}

/* -------------------------------- this week ------------------------------- */
const STEPS = ["proposed", "notified", "approved", "executed", "verified"];

function stepper(recState) {
  const failed = recState === "failed";
  // a snoozed rec still sits at "notified" on the chain — never an unlit stepper
  const shown = recState === "snoozed" ? "notified" : recState;
  const idx = failed ? STEPS.length : STEPS.indexOf(shown);
  return `<div class="stepper" aria-label="Recommendation state">` + STEPS.map((s, i) => {
    let cls = "step";
    if (failed && i >= 2) cls += " is-failed";
    else if (s === shown) cls += s === "verified" ? " is-verified" : " is-now";
    else if (i < idx) cls += " is-done";
    return `${i ? '<span class="step-link"></span>' : ""}<span class="${cls}">${failed && s === "verified" ? "failed" : s}</span>`;
  }).join("") + `</div>`;
}

function lineupTable(title, rows, total, marks) {
  const tr = rows.map((r) => {
    const mark = marks.get(r.id) || "";
    const hurt = r.injury ? `<span class="hurt">${icon.cross}${esc(r.injury.toUpperCase())}</span>`
      : r.bye ? `<span class="hurt">${icon.cross}BYE</span>` : "";
    return `<tr class="${mark}"><td class="slot">${esc(r.slot)}</td>
      <td><span class="pname">${esc(r.name)}</span>
        <span class="team">${esc(r.team ?? "")}</span> ${hurt}
        <div><span class="pos pos-${posOf(r)}">${posOf(r)}</span></div></td>
      <td class="proj">${r.proj.toFixed(1)}</td></tr>`;
  }).join("");
  return `<div class="lineup"><h3>${title}<span class="total">${total.toFixed(1)}</span></h3>
    <table><tbody>${tr}</tbody></table></div>`;
}

function renderWeek(card) {
  const wrap = $("#week-layout");
  if (!card.ready) {
    wrap.innerHTML = `<p class="muted">${esc(card.note || "No roster on file yet.")}</p>`;
    return;
  }
  const rec = card.rec;
  const recState = rec ? rec.state : null;

  if (!card.material && (!rec || ["verified", "ignored", "failed", null].includes(recState))) {
    const verifiedLine = recState === "verified"
      ? `The last swap was <strong>verified against the API</strong>. ` : "";
    wrap.innerHTML = `
      <div class="allgood"><span class="lampdot"></span>
        <div><p><strong>Lineup optimal.</strong> ${verifiedLine}Projected
        ${card.actual_total.toFixed(1)} for week ${card.week} — the room is satisfied.</p></div>
      </div>
      ${lineupBlock(card)}`;
    return;
  }

  const swapsLines = card.swaps.map((s) =>
    `${esc(s.in.name)} in for ${esc(s.out.name)} (${esc(s.slot)}, ${s.gain > 0 ? "+" : ""}${s.gain})`).join(" · ");
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
    actions = `<p class="holds">${icon.hold} The swap failed or expired — every failure
      degrades to this notice, never to silence. <a href="https://sleeper.com" rel="noopener">Set it in Sleeper</a>.</p>`;
  }

  wrap.innerHTML = `
    <div class="verdict ${recState === "verified" ? "is-good" : ""}">
      <div class="verdict-head">
        <span class="verdict-title">${card.injury_flag ? "You have trouble in the lineup." : "The room found points."}</span>
        <span class="delta">${card.delta > 0 ? "+" : ""}${card.delta.toFixed(1)}</span>
      </div>
      <p class="rationale">${rec ? esc(rec.rationale) : swapsLines}</p>
      ${holdNote}
      ${rec ? stepper(recState) : ""}
      ${actions}
    </div>
    ${lineupBlock(card)}`;

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

function lineupBlock(card) {
  const inIds = new Set(card.swaps.map((s) => s.in_id));
  const outIds = new Set(card.swaps.map((s) => s.out_id));
  const actualMarks = new Map(card.actual.map((r) => [r.id, outIds.has(r.id) ? "is-out" : ""]));
  const optimalMarks = new Map(card.optimal.map((r) => [r.id, inIds.has(r.id) ? "is-in" : ""]));
  return `<div class="lineup-tables">
    ${lineupTable(`Actual — week ${card.week}`, card.actual, card.actual_total, actualMarks)}
    ${lineupTable("Optimal", card.optimal, card.optimal_total, optimalMarks)}
  </div>
  <p class="optimal-note">Optimal is the Hungarian assignment over your roster's
  projections; actual is what the API says is started right now.</p>`;
}

async function pollWeek() {
  try {
    const card = await fetchJSON("/api/week/1");
    if (!state.approving) renderWeek(card);
    state.week = card;
    wireOK();
  } catch { wireFail(); }
}

/* --------------------------------- waivers -------------------------------- */
async function loadWaivers() {
  try {
    const data = await fetchJSON("/api/waivers");
    const rows = data.targets.map((t) => `
      <tr><td><span class="pname">${esc(t.name)}</span> <span class="team">${esc(t.team ?? "")}</span>
        <div><span class="pos pos-${posOf(t)}">${posOf(t)}</span></div></td>
      <td class="num">${t.fa_score}</td>
      <td class="num"><span class="bid">$${t.bid}</span></td>
      <td class="num street">${t.heat ? `${t.heat.toLocaleString()} adds` : "–"}</td>
      <td class="num">${t.lineup_gain == null ? "–"
        : t.lineup_gain > 0 ? `<b>starts · +${t.lineup_gain}</b>` : `<span class="street">depth</span>`}</td>
      <td>${t.hard_confirm ? `<span class="confirm-flag">${icon.flag}BIG SWING — CONFIRM TWICE</span>` : ""}</td></tr>`).join("");
    $("#waivers-body").innerHTML = data.targets.length ? `
      <table class="wtable">
        <thead><tr><th>Player</th><th style="text-align:right">FA score</th>
        <th style="text-align:right">Bid</th><th style="text-align:right">The street</th>
        <th style="text-align:right">Your lineup</th>
        <th></th></tr></thead>
        <tbody>${rows}</tbody></table>
      <p class="optimal-note">Sized at the P70 of the league's bids for each value tier (${data.history_n} on the books), +$1 over round numbers.</p>`
      : `<p class="muted">${esc(data.note || "Nobody on the street worth a dollar this week.")}</p>`;
    wireOK();
  } catch { wireFail(); }
}

/* --------------------------------- parlor --------------------------------- */
function sideList(ps) {
  return ps.map((p) =>
    `<span class="pos pos-${posOf(p)}">${posOf(p)}</span> ${esc(p.name)}`).join(" · ");
}

async function loadParlor() {
  try {
    const data = await fetchJSON("/api/trades/suggest");
    $("#parlor-body").innerHTML = data.trades.length ? data.trades.map((t) => `
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

function renderReportCard(g) {
  const rows = g.teams.map((t) => `
    <tr class="${t.mine ? "is-me" : ""}">
      <td class="num">${t.rank}</td>
      <td><span class="gradechip ${gradeCls(t.grade)}">${esc(t.grade)}</span></td>
      <td>${esc(t.owner)}${t.mine ? ' <span class="me-tag">YOU</span>' : ""}</td>
      <td class="num" title="season-total points of the optimal starting lineup">${t.starters.toFixed(1)}</td>
      <td class="num" title="picks of market value captured vs ADP — positive means discounts">${t.surplus > 0 ? "+" : ""}${Math.round(t.surplus)}</td>
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
      <th style="text-align:right">value</th><th>the read</th></tr></thead>
      <tbody>${rows}</tbody></table>
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
  setTab(state.tab);
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
  }, 15000);
  setInterval(() => {
    if (state.tab === "parlor") loadParlor();  // full-league scan — slower cadence
  }, 60000);
}
boot();
