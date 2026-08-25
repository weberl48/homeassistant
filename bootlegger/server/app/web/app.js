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
};

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
  $("#wire-text").textContent = down ? "wire down" : stale ? "wire stale" : "wire live";
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
  el.addEventListener("click", () => openDossier(p.id));
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
        <span><span class="lbl">vbd</span><b data-vbd>${p.vbd}</b></span>
        <span><span class="lbl">adp</span><span data-adp>${p.adp ?? "–"}</span></span>
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
        <span class="stat"><span>score</span>${top.score}</span>
        <span class="stat"><span>vbd</span>${top.vbd}</span>
        <span class="stat"><span>survives</span>${fmtSurv(top.survival ?? 0)}</span>
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
  activePos = chip.dataset.pos;
  document.querySelectorAll("#pos-filters .chip").forEach((c) => {
    c.classList.toggle("is-active", c === chip);
    c.setAttribute("aria-pressed", String(c === chip));
  });
  applyPosFilter();
});
function applyPosFilter() {
  document.querySelectorAll(".col").forEach((col) =>
    col.classList.toggle("pos-hidden", activePos !== "ALL" && col.dataset.pos !== activePos));
}

async function pollBoard() {
  try {
    renderBoard(await fetchJSON("/api/draft/board"));
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
    wrap.innerHTML = `<p class="muted">No roster on file yet.</p>`;
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
      : `<p class="muted">Nobody on the street worth a dollar this week.</p>`;
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
    wireOK();
  } catch { wireFail(); }
}

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
    }
    wireOK(); renderWire();
  } catch { wireFail(); }
  await pollBoard();
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
  }, 15000);
  setInterval(() => {
    if (state.tab === "parlor") loadParlor();  // full-league scan — slower cadence
  }, 60000);
}
boot();
