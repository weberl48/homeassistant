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

  const POS_HUES = { QB: "#b8455f", RB: "#3aa96b", WR: "#4f8fd8", TE: "#cb7c2c", K: "#8768cf", DEF: "#9c8f38" };
  const posOf = (p) => (p.pos === "DST" ? "DEF" : p.pos);

  const host = document.createElement("div");
  host.id = "bootlegger-overlay-host";
  const shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `
<style>
  :host { all: initial; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  .panel {
    position: fixed; right: 16px; bottom: 16px; width: 304px; z-index: 2147483000;
    background: linear-gradient(180deg, #251c14, #1e1712);
    border: 1px solid #8a6d33; border-radius: 6px;
    box-shadow: 0 2px 6px rgba(0,0,0,.45), 0 8px 24px rgba(0,0,0,.35);
    color: #ecdfc6; font: 400 13px/1.4 "Segoe UI", system-ui, sans-serif;
  }
  .head {
    display: flex; align-items: center; gap: 8px; padding: 8px 10px;
    border-bottom: 1px solid #3b2f21; cursor: pointer; user-select: none;
  }
  .mark { font: 400 13px Georgia, serif; letter-spacing: .18em; color: #e6c684; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #58a06c;
         box-shadow: 0 1px 5px rgba(88,160,108,.8); }
  .dot.bad { background: #cf6152; box-shadow: 0 1px 5px rgba(207,97,82,.8); }
  .fold { margin-left: auto; color: #91805f; font-size: 14px; }
  .body { padding: 9px 10px 10px; }
  .clock { font-family: Consolas, monospace; font-size: 12px; color: #b5a483;
           letter-spacing: .04em; margin-bottom: 7px; }
  .clock.mine { color: #e6c684; font-weight: 700; }
  .warn { color: #cf6152; font-size: 11px; margin-bottom: 6px; }
  .call { border: 1px solid #8a6d33; border-radius: 5px; padding: 8px 9px;
          background: linear-gradient(180deg, rgba(201,162,92,.10), rgba(201,162,92,.02));
          cursor: pointer; }
  .call:hover { background: linear-gradient(180deg, rgba(201,162,92,.16), rgba(201,162,92,.05)); }
  .cname { font-size: 16px; font-weight: 700; }
  .cmeta { display: flex; gap: 10px; font-family: Consolas, monospace; font-size: 11px;
           color: #b5a483; margin-top: 2px; }
  .creason { color: #b5a483; font-size: 11.5px; margin-top: 5px; }
  .sheet { color: #91805f; font-size: 11px; margin-top: 5px; padding-top: 5px;
           border-top: 1px dashed #2a2118; }
  .sheet b { color: #b5a483; }
  .runners { list-style: none; margin-top: 7px; }
  .runners li { display: flex; align-items: baseline; gap: 7px; padding: 4px 2px;
                border-top: 1px solid #2a2118; cursor: pointer; }
  .runners li:hover { background: #251c14; }
  .swatch { width: 7px; height: 7px; border-radius: 2px; flex: none; }
  .rpos { font-size: 10px; font-weight: 700; color: #91805f; width: 24px; }
  .rname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rnum { margin-left: auto; font-family: Consolas, monospace; font-size: 11px; color: #b5a483; }
  .needs { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
  .need { font-size: 10px; font-weight: 600; border: 1px solid #3b2f21; border-radius: 3px;
          padding: 1px 5px; color: #91805f; }
  .need.open { border-color: #8a6d33; color: #c9a25c; }
  .toast { position: absolute; left: 10px; right: 10px; bottom: calc(100% + 8px);
           background: #c9a25c; color: #17120e; font-weight: 600; font-size: 12px;
           padding: 7px 10px; border-radius: 4px; box-shadow: 0 2px 8px rgba(0,0,0,.5);
           opacity: 0; transition: opacity .15s ease; pointer-events: none; }
  .toast.show { opacity: 1; }
  .muted { color: #91805f; font-size: 12px; }
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

  const pageDraftId = (location.pathname.match(/\/draft\/nfl\/(\d+)/) || [])[1];

  function render(b) {
    const d = b.draft;
    const s = b.suggestions || [];
    const top = s[0];
    const sheet = b.experts_call;
    const stale = d.synced_at ? Date.now() - Date.parse(d.synced_at) > STALE_MS : false;
    const wrongDraft = pageDraftId && d.id && String(d.id) !== pageDraftId;
    $("dot").classList.toggle("bad", stale);

    const clock = d.status === "complete"
      ? "DRAFT COMPLETE"
      : `PICK ${d.current_pick}/${d.total_picks} · R${d.round}` +
        (d.on_the_clock_me ? " — YOU'RE UP" : d.my_next_pick ? ` · you at #${d.my_next_pick}` : "");

    const needs = (b.roster_positions ? buildNeeds(b) : []).map((n) =>
      `<span class="need ${n.have < n.want ? "open" : ""}">${esc(n.pos)} ${n.have}/${n.want}</span>`).join("");

    $("body").innerHTML = `
      ${wrongDraft ? `<p class="warn">Board is tracking a different draft — repoint the poller.</p>` : ""}
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
            <span class="swatch" style="background:${POS_HUES[posOf(r)] || "#91805f"}"></span>
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
    if (document.hidden) return;
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
