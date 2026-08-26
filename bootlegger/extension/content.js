/* Bootlegger Draft Overlay — the back room pinned inside Sleeper's draft room.
   Read-only against the Pi's board API; the one page interaction (click a
   suggestion) fills Sleeper's own search box in YOUR logged-in tab — the pick
   itself stays human. */
(() => {
  "use strict";
  if (document.getElementById("bootlegger-overlay-host")) return;

  const POLL_MS = 2000;
  const STALE_MS = 10000;

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const POS_HUES = { QB: "#ff7a95", RB: "#52d98b", WR: "#59d3f2", TE: "#f0954a", K: "#c39bff", DEF: "#e4de6b" };
  const posOf = (p) => (p.pos === "DST" ? "DEF" : p.pos);

  const host = document.createElement("div");
  host.id = "bootlegger-overlay-host";
  const shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `
<style>
  :host {
    all: initial;
    /* ORCHARD PARK NIGHT tokens — mirrors bootlegger/server/app/web/styles.css
       :root so every hex here appears exactly once. */
    --ground: #06122e;
    --panel: #0b1c44;
    --panel-2: #112552;
    --line: rgba(236,242,255,.28);
    --line-soft: rgba(236,242,255,.13);
    --ink: #f2f6ff;
    --ink-dim: #c2cde6;
    --ink-faint: #8499c4;
    --brass: #5b8cff;
    --brass-bright: #93b4ff;
    --brass-deep: #2e5fd9;
    --lamp: #3ed98a;
    --oxblood: #ff5d66;
    --font-ledger: "Courier Prime", "Courier New", monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  .panel {
    position: fixed; right: 16px; bottom: 16px; width: 304px; z-index: 2147483000;
    background: linear-gradient(180deg, var(--panel-2), var(--panel));
    border: 1px solid var(--brass-deep); border-radius: 6px;
    box-shadow: 0 2px 6px rgba(0,0,0,.45), 0 8px 24px rgba(0,0,0,.35);
    color: var(--ink); font: 400 13px/1.4 "Segoe UI", system-ui, sans-serif;
  }
  .head {
    display: flex; align-items: center; gap: 8px; padding: 8px 10px;
    border-bottom: 1px solid var(--line-soft); cursor: pointer; user-select: none;
  }
  .mark { font: 400 13px Georgia, serif; letter-spacing: .18em; color: var(--brass); }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--lamp);
         box-shadow: 0 1px 5px rgba(62,217,138,.8); }
  .dot.bad { background: var(--oxblood); box-shadow: 0 1px 5px rgba(255,93,102,.8); }
  .fold { margin-left: auto; color: var(--ink-faint); font-size: 14px; }
  .body { padding: 9px 10px 10px; }
  .clock { font-family: var(--font-ledger); font-size: 12px; color: var(--ink-dim);
           letter-spacing: .04em; margin-bottom: 7px; }
  .clock.mine { color: var(--brass-bright); font-weight: 700; }
  .warn { color: var(--oxblood); font-size: 11px; margin-bottom: 6px; }
  .call { border: 1px solid var(--brass-deep); border-radius: 5px; padding: 8px 9px;
          background: linear-gradient(180deg, rgba(91,140,255,.10), rgba(91,140,255,.02));
          cursor: pointer; }
  .call:hover { background: linear-gradient(180deg, rgba(91,140,255,.16), rgba(91,140,255,.05)); }
  .cname { font-size: 16px; font-weight: 700; }
  .cmeta { display: flex; gap: 10px; font-family: var(--font-ledger); font-size: 11px;
           color: var(--ink-dim); margin-top: 2px; }
  .creason { color: var(--ink-dim); font-size: 11.5px; margin-top: 5px; }
  .sheet { color: var(--ink-faint); font-size: 11px; margin-top: 5px; padding-top: 5px;
           border-top: 1px dashed var(--line-soft); }
  .sheet b { color: var(--ink-dim); }
  .runners { list-style: none; margin-top: 7px; }
  .runners li { display: flex; align-items: baseline; gap: 7px; padding: 4px 2px;
                border-top: 1px solid var(--line-soft); cursor: pointer; }
  .runners li:hover { background: var(--panel-2); }
  .swatch { width: 7px; height: 7px; border-radius: 2px; flex: none; }
  .rpos { font-size: 10px; font-weight: 700; color: var(--ink-faint); width: 24px; }
  .rname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rnum { margin-left: auto; font-family: var(--font-ledger); font-size: 11px; color: var(--ink-dim); }
  .needs { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
  .need { font-size: 10px; font-weight: 600; border: 1px solid var(--line); border-radius: 3px;
          padding: 1px 5px; color: var(--ink-faint); }
  .need.open { border-color: var(--brass-deep); color: var(--brass); }
  .toast { position: absolute; left: 10px; right: 10px; bottom: calc(100% + 8px);
           background: var(--brass); color: var(--ground); font-weight: 600; font-size: 12px;
           padding: 7px 10px; border-radius: 4px; box-shadow: 0 2px 8px rgba(0,0,0,.5);
           opacity: 0; transition: opacity .15s ease; pointer-events: none; }
  .toast.show { opacity: 1; }
  .muted { color: var(--ink-faint); font-size: 12px; }
  .panel.folded .body { display: none; }
</style>
<div class="panel" id="panel">
  <div class="toast" id="toast"></div>
  <div class="head" id="head">
    <span class="dot" id="dot"></span>
    <span class="mark">BOOTLEGGER</span>
    <span class="fold" id="fold">–</span>
  </div>
  <div class="body" id="body"><p class="muted">Tapping the wire…</p></div>
</div>`;
  document.documentElement.appendChild(host);

  const $ = (id) => shadow.getElementById(id);
  const panel = $("panel");
  let folded = false;
  try { folded = localStorage.getItem("bootlegger.overlay.folded") === "1"; } catch { /* fine */ }
  panel.classList.toggle("folded", folded);
  $("head").addEventListener("click", () => {
    folded = !folded;
    panel.classList.toggle("folded", folded);
    $("fold").textContent = folded ? "+" : "–";
    try { localStorage.setItem("bootlegger.overlay.folded", folded ? "1" : "0"); } catch { /* fine */ }
  });

  let toastTimer = null;
  function toast(msg) {
    const t = $("toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
  }

  /* Fill Sleeper's own player search in the logged-in tab (React needs the
     native value setter + a bubbling input event). Clipboard fallback when
     the search box can't be found — Sleeper redesigns break selectors. */
  function assist(name) {
    const inp = [...document.querySelectorAll("input")].find(
      (i) => /search/i.test(i.placeholder || "") && i.offsetParent !== null);
    if (inp) {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      set.call(inp, name);
      inp.dispatchEvent(new Event("input", { bubbles: true }));
      inp.focus();
      toast(`searched "${name}" — tap him in Sleeper's list`);
    } else if (navigator.clipboard) {
      navigator.clipboard.writeText(name)
        .then(() => toast(`"${name}" copied — paste into the search`))
        .catch(() => toast(name));
    }
  }

  /* Sleeper is an SPA: entering the draft room from the league page changes
     the URL with no page load, so the script must inject on ALL sleeper pages
     and show/hide itself as the location moves. Recomputed live — never
     cached at inject time. */
  const draftPathId = () => (location.pathname.match(/\/draft\/nfl\/(\d+)/) || [])[1];

  function syncVisibility() {
    host.style.display = draftPathId() ? "" : "none";
  }
  syncVisibility();

  function render(b) {
    const d = b.draft;
    const s = b.suggestions || [];
    const top = s[0];
    const sheet = b.experts_call;
    const stale = d.synced_at ? Date.now() - Date.parse(d.synced_at) > STALE_MS : false;
    const pageDraftId = draftPathId();
    const wrongDraft = pageDraftId && d.id && String(d.id) !== pageDraftId;
    $("dot").classList.toggle("bad", stale);

    const clock = d.status === "complete"
      ? "DRAFT COMPLETE"
      : `PICK ${d.current_pick}/${d.total_picks} · R${d.round}` +
        (d.on_the_clock_me ? " — YOU'RE UP" : d.my_next_pick ? ` · you at #${d.my_next_pick}` : "");

    const needs = (b.roster_positions ? buildNeeds(b) : []).map((n) =>
      `<span class="need ${n.have < n.want ? "open" : ""}">${esc(n.pos)} ${n.have}/${n.want}</span>`).join("");

    $("body").innerHTML = `
      ${wrongDraft ? `<p class="warn">Board is tracking a different draft — paste this room's URL into the Scrimmage box on the board.</p>` : ""}
      ${stale ? `<p class="warn">PICK FEED STALE — poller heartbeat is old.</p>` : ""}
      <p class="clock ${d.on_the_clock_me ? "mine" : ""}">${esc(clock)}</p>
      ${d.status === "complete" ? `<p class="muted">The shelf is stocked — the room pours one out.</p>` : top ? `
        <div class="call" id="call" title="click: search him in Sleeper">
          <div class="cname">${esc(top.name)}</div>
          <div class="cmeta"><span>${esc(posOf(top))} · ${esc(top.team ?? "")}</span>
            <span>score ${top.score}</span><span>vbd ${top.vbd}</span>
            <span>${Math.round((top.survival ?? 0) * 100)}% lasts</span></div>
          <div class="creason">${esc(top.reason || "")}</div>
          ${sheet && top && sheet.id !== top.id
            ? `<div class="sheet">sheet says <b>${esc(sheet.name)}</b> · ECR ${sheet.ecr}</div>` : ""}
        </div>
        <ul class="runners">${s.slice(1, 4).map((r, i) => `
          <li data-i="${i + 1}" title="click: search him in Sleeper">
            <span class="swatch" style="background:${POS_HUES[posOf(r)] || "#8499c4"}"></span>
            <span class="rpos">${esc(posOf(r))}</span>
            <span class="rname">${esc(r.name)}</span>
            <span class="rnum">${r.score ?? ""}</span></li>`).join("")}
        </ul>` : `<p class="muted">Reading the room…</p>`}
      <div class="needs">${needs}</div>`;

    const call = $("call");
    if (call && top) call.addEventListener("click", () => assist(top.name));
    shadow.querySelectorAll(".runners li").forEach((li) =>
      li.addEventListener("click", () => assist(s[Number(li.dataset.i)].name)));
  }

  function buildNeeds(b) {
    const slots = {};
    for (const sl of b.roster_positions) {
      if (["BN", "IR", "TAXI"].includes(sl)) continue;
      const k = ["SUPER_FLEX", "SUPERFLEX", "WRRB_FLEX", "REC_FLEX"].includes(sl) ? "FLEX" : sl;
      slots[k] = (slots[k] || 0) + 1;
    }
    const counts = {};
    for (const p of b.my_roster || []) counts[p.pos] = (counts[p.pos] || 0) + 1;
    const out = [];
    let flexUsed = 0;
    for (const pos of ["QB", "RB", "WR", "TE", "K", "DEF"]) {
      const want = slots[pos] || 0;
      if (!want) continue;
      const have = counts[pos] || 0;
      if (["RB", "WR", "TE"].includes(pos) && have > want) flexUsed += have - want;
      out.push({ pos, have: Math.min(have, want), want });
    }
    if (slots.FLEX) out.push({ pos: "FLEX", have: Math.min(flexUsed, slots.FLEX), want: slots.FLEX });
    return out;
  }

  let drafting = false;

  async function tick() {
    syncVisibility();
    if (document.hidden || !draftPathId()) return;
    let res = null;
    try { res = await chrome.runtime.sendMessage({ type: "board" }); } catch { /* worker asleep */ }
    if (!res || !res.ok) {
      $("dot").classList.add("bad");
      return;
    }
    drafting = res.board.draft.status === "drafting";
    render(res.board);
  }

  (function loop() {
    // 1s while the draft is live (server caches per pick), relaxed otherwise
    setTimeout(async () => { await tick(); loop(); }, drafting ? 1000 : POLL_MS);
  })();
  tick();
})();
