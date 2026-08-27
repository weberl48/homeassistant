"""The A++ gate — assertions a screenshot cannot make.

`driver.py check` proves the shell resolved and the board painted rows.
This proves the things you cannot see in a PNG: that a live region stays quiet
through an idle poll, that the tablist is actually a tablist, that focus goes
into the scout's file and comes back to the row that opened it, that nothing
scrolls sideways at any width a real device has, that a control you can tap is
big enough to tap, and that the contrast holds on the RENDERED page rather than
in the token file.

Every check here corresponds to a defect that shipped at least once. That is
the entry requirement — this file is not a wishlist, it is a record.

Run: driver.py audit          (exit 0 = clean, 1 = something regressed)
"""
from __future__ import annotations

import pathlib
import re

ROOMS = ["board", "week", "waivers", "league", "parlor", "ledger"]
# Widths people actually hold: iPhone, small tablet, small laptop, the modal
# laptop, a desktop. 1440 is the one that matters most — see _board_columns.
WIDTHS = [390, 768, 1024, 1440, 1920]
# Board widths worth checking: below the four-across threshold, either side
# of it, the modal laptop, either side of six-across, and a big monitor.
WIDTHS_BOARD = [1024, 1180, 1300, 1340, 1440, 1700, 1900, 1920, 2560]
MIN_CONTRAST = 4.5       # WCAG 2.1 AA, normal text
MIN_TARGET = 44          # CSS px, per PRODUCT.md's own commitment


class Results(list):
    def ok(self, name: str, detail: str = "") -> None:
        self.append((True, name, detail))

    def bad(self, name: str, detail: str) -> None:
        self.append((False, name, detail))

    def check(self, cond: bool, name: str, detail: str = "") -> None:
        (self.ok if cond else self.bad)(name, detail)


# ---------------------------------------------------------------------------
# semantics
# ---------------------------------------------------------------------------

def audit_tablist(pg, r: Results) -> None:
    shape = pg.evaluate("""() => {
      const tabs = [...document.querySelectorAll('[role=tab]')];
      const panels = [...document.querySelectorAll('[role=tabpanel]')];
      return {
        list: !!document.querySelector('[role=tablist]'),
        tabs: tabs.length,
        panels: panels.length,
        selected: tabs.filter(t => t.getAttribute('aria-selected') === 'true').length,
        controls: tabs.every(t => !!document.getElementById(t.getAttribute('aria-controls') || '')),
        labelled: panels.every(p => !!document.getElementById(p.getAttribute('aria-labelledby') || '')),
        roving: tabs.filter(t => t.tabIndex === 0).length,
        stale: document.querySelectorAll('[role=tab][aria-current]').length,
      };
    }""")
    good = (shape["list"] and shape["tabs"] == len(ROOMS)
            and shape["panels"] == len(ROOMS) and shape["selected"] == 1
            and shape["controls"] and shape["labelled"]
            and shape["roving"] == 1 and shape["stale"] == 0)
    r.check(good, "tablist is a real tablist", "" if good else str(shape))

    # A tablist without arrow keys is half a pattern. Expectation is computed
    # from where we actually are — the app has a phase router that picks the
    # opening room, so "it opens on the board" is not a safe assumption.
    here = pg.evaluate(
        "() => document.querySelector('[role=tab][aria-selected=true]').dataset.tab")
    want = ROOMS[(ROOMS.index(here) + 1) % len(ROOMS)]
    pg.locator("[role=tab][aria-selected=true]").focus()
    pg.keyboard.press("ArrowRight")
    pg.wait_for_timeout(500)
    moved = pg.evaluate(
        "() => document.querySelector('[role=tab][aria-selected=true]').dataset.tab")
    r.check(moved == want, "arrow keys move between rooms",
            "" if moved == want else f"from {here!r} expected {want!r}, landed on {moved!r}")
    pg.keyboard.press("Home")
    pg.wait_for_timeout(500)


def audit_live_regions(pg, r: Results) -> None:
    """A live region may only announce when its MEANING changes.

    renderClock used to assign textContent unconditionally on a 1 Hz poll, so a
    screen reader read the pick number aloud once a second for the whole draft.
    Assigning an identical string is still a mutation of the accessibility
    tree, so the strings alone cannot prove anything — the mutations have to be
    watched.

    The assertion is NOT "zero mutations". That only holds on an idle board,
    and it would therefore fail on draft night, when picks land every couple of
    seconds and the clock legitimately has something new to say — a gate that
    cries wolf exactly when it matters is worse than no gate. The invariant is
    one announcement per MEANING change: every mutation must carry text that
    differs from the text before it. Measured live mid-draft: five distinct
    clock states, four mutations, zero repeats.
    """
    pg.evaluate("""() => {
      window.__live = {repeats: [], changes: 0};
      for (const el of document.querySelectorAll('[aria-live], [role=alert], [role=status]')) {
        if (el.getAttribute('aria-live') === 'off') continue;
        let last = (el.textContent || '').trim();
        new MutationObserver(() => {
          const now = (el.textContent || '').trim();
          const who = el.id || el.className || el.tagName;
          if (now === last) window.__live.repeats.push(who + ' -> ' + now.slice(0, 44));
          else { window.__live.changes++; last = now; }
        }).observe(el, {childList: true, characterData: true, subtree: true});
      }
    }""")
    pg.wait_for_timeout(10000)          # ten board polls at the draft cadence
    live = pg.evaluate("() => window.__live")
    repeats = live["repeats"]
    r.check(not repeats, "live regions announce only on a real change",
            f"{len(repeats)} redundant announcement(s), e.g. {repeats[0]!r}" if repeats
            else f"{live['changes']} real change(s), 0 repeats")


def audit_deep_link(pg, base: str, r: Results) -> None:
    pg.goto(f"{base}#waivers", wait_until="networkidle")
    pg.wait_for_timeout(1500)
    landed = pg.evaluate(
        "() => document.querySelector('[role=tab][aria-selected=true]').dataset.tab")
    r.check(landed == "waivers", "a room is linkable (#waivers)",
            "" if landed == "waivers" else f"landed on {landed!r}")


def audit_dialog_focus(pg, base: str, r: Results) -> None:
    """Focus must go into the scout's file, stay there, and come back to the
    row that opened it. The old hand-rolled modal did none of the three."""
    pg.goto(f"{base}#board", wait_until="networkidle")
    pg.wait_for_timeout(2000)
    row = pg.locator(".prow").first
    row.focus()
    opener = pg.evaluate("() => document.activeElement.dataset.id")
    row.press("Enter")
    pg.wait_for_timeout(1400)
    inside = pg.evaluate("""() => {
      const d = document.getElementById('dossier');
      return {open: !!d.open, holds: d.contains(document.activeElement)};
    }""")
    if not inside["open"]:
        r.bad("scout's file opens as a modal dialog", str(inside))
        return
    r.check(inside["holds"], "focus enters the dialog",
            "" if inside["holds"] else str(inside))

    pg.keyboard.press("Escape")
    pg.wait_for_timeout(900)
    back = pg.evaluate("""() => ({
      open: !!document.getElementById('dossier').open,
      id: document.activeElement.dataset ? document.activeElement.dataset.id : null,
    })""")
    good = (not back["open"]) and back["id"] == opener
    r.check(good, "Escape closes and hands focus back to the row",
            "" if good else f"opener={opener!r} restored={back['id']!r}")


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------

def audit_board_columns(pg, base: str, r: Results) -> None:
    """The board's whole argument is the deciding positions side by side. The
    column ladder used to reach six-across only at 1700px, so the modal laptop
    drafted with three columns and half the board below the fold."""
    bad = []
    for w in (1340, 1440, 1920):
        pg.set_viewport_size({"width": w, "height": 1000})
        pg.goto(f"{base}#board", wait_until="networkidle")
        pg.wait_for_timeout(1200)
        rows = pg.evaluate("""() => {
          // Only the four that decide. K/DST deliberately fold to a paired
          // strip beneath them below 1700 — see the comment on .columns.
          const deciding = ['QB', 'RB', 'WR', 'TE'];
          const tops = [...document.querySelectorAll('section.col')]
            .filter(c => c.offsetParent && !c.classList.contains('pos-hidden')
                         && deciding.includes(c.dataset.pos))
            .map(c => Math.round(c.getBoundingClientRect().top));
          return {rows: new Set(tops).size, n: tops.length};
        }""")
        if rows["rows"] > 1 or rows["n"] != 4:
            bad.append(f"{w}px: {rows['n']} deciding cols on {rows['rows']} rows")
    pg.set_viewport_size({"width": 1440, "height": 1000})
    r.check(not bad, "the four deciding columns hold one row from 1340 up",
            ", ".join(bad) if bad else "1340, 1440, 1920 all single-row")


def audit_rail_contained(pg, base: str, r: Results) -> None:
    """Nothing in the rail may cross into the board.

    `overflow-x: clip` on the body means an overflowing rail never shows up as
    page-level sideways scroll — it just quietly paints over the first column.
    The Call did exactly that on the live board while every other check passed,
    so the containment gets its own assertion.
    """
    bad = []
    for w in (1340, 1440, 1920):
        pg.set_viewport_size({"width": w, "height": 1000})
        pg.goto(f"{base}#board", wait_until="networkidle")
        pg.wait_for_timeout(1200)
        over = pg.evaluate("""() => {
          const rail = document.querySelector('.rail');
          const board = document.querySelector('.board-main');
          if (!rail || !board) return [];
          // The boundary that matters is where the BOARD starts, not where the
          // rail's border box ends: the 24px gutter between them is fair game,
          // and the buffalo's dust puffs land in it by design. What must never
          // happen is a plate painting over a player row.
          const edge = board.getBoundingClientRect().left;
          const out = [];
          for (const el of rail.querySelectorAll('*')) {
            const cs = getComputedStyle(el);
            if (cs.visibility === 'hidden' || !el.getClientRects().length) continue;
            const right = el.getBoundingClientRect().right;
            if (right > edge + 1)
              out.push((el.className.baseVal ?? el.className ?? el.tagName)
                       + ' +' + Math.round(right - edge) + 'px');
          }
          return [...new Set(out)];
        }""")
        if over:
            bad.append(f"{w}px: {over[:3]}")
    pg.set_viewport_size({"width": 1440, "height": 1000})
    r.check(not bad, "nothing in the rail crosses into the board",
            "; ".join(bad) if bad else "1340, 1440, 1920 clear")


def audit_rows_never_clip(pg, base: str, r: Results) -> None:
    """No player row may lose a figure to its own column.

    This one exists because the gate had a blind spot rather than a bug:
    headless Chromium draws OVERLAY scrollbars, which take no layout width, so
    the harness measured a board that fit while the owner's browser — with a
    classic 8px scrollbar reserving real width — was cutting the ADP off every
    row. Reserving the gutter in CSS fixes the page; measuring the invariant
    instead of the rendered scrollbar fixes the check.

    The invariant is browser-independent: a row's natural width must fit inside
    its column's content box, gutter already deducted.
    """
    bad = []
    for w in WIDTHS_BOARD:
        pg.set_viewport_size({"width": w, "height": 1000})
        pg.goto(f"{base}#board", wait_until="networkidle")
        pg.wait_for_timeout(1200)
        # Test the layout's CAPACITY, not the fixture's luck. The demo pool is
        # shallow enough that its worst VBD is two digits; the live board
        # carries -166.3, and that row was the one clipping. Writing the widest
        # figures a real board can produce into every row makes the check about
        # the design rather than about which players happen to be seeded.
        pg.evaluate("""() => {
          for (const row of document.querySelectorAll('.prow')) {
            const v = row.querySelector('[data-vbd]');
            const a = row.querySelector('[data-adp]');
            if (v) v.textContent = '-299';
            if (a) a.textContent = '300.5';
          }
        }""")
        pg.wait_for_timeout(150)
        worst = pg.evaluate("""() => {
          let worst = null;
          for (const col of document.querySelectorAll('section.col')) {
            if (!col.offsetParent || col.classList.contains('pos-hidden')) continue;
            const body = col.querySelector('.col-body');
            if (!body) continue;
            // offsetWidth-clientWidth reads 0 under overlay scrollbars, so
            // assume the classic 8px the owner's browser actually reserves.
            const gutter = (body.offsetWidth - body.clientWidth) || 8;
            const edge = body.getBoundingClientRect().right - gutter;
            for (const row of col.querySelectorAll('.prow')) {
              // The NAME is meant to ellipsize — that is the design. What must
              // never be cut is the figure block: vbd, adp, and the survival
              // percentage. Those are the Typewriter Figures, and half an ADP
              // is worse than no ADP.
              for (const nums of row.querySelectorAll('.nums, .surv')) {
                const b = nums.getBoundingClientRect();
                const over = Math.round(Math.max(b.right - edge, nums.scrollWidth - nums.clientWidth));
                if (over > 0 && (!worst || over > worst.over))
                  worst = {pos: col.dataset.pos, over,
                           txt: (nums.textContent || '').trim().slice(0, 22)};
              }
            }
          }
          return worst;
        }""")
        if worst:
            bad.append(f"{w}px {worst['pos']} over by {worst['over']}px ({worst['txt']!r})")
    pg.set_viewport_size({"width": 1440, "height": 1000})
    r.check(not bad, "no player row clips its own figures",
            "; ".join(bad) if bad else f"{len(WIDTHS_BOARD)} widths clear")


def audit_no_sideways(pg, base: str, r: Results) -> None:
    bad = []
    for w in WIDTHS:
        pg.set_viewport_size({"width": w, "height": 900})
        for room in ROOMS:
            pg.goto(f"{base}#{room}", wait_until="domcontentloaded")
            pg.wait_for_timeout(800)
            over = pg.evaluate("() => document.documentElement.scrollWidth "
                               "- document.documentElement.clientWidth")
            if over > 1:
                bad.append(f"{room}@{w} +{over}px")
    pg.set_viewport_size({"width": 1440, "height": 1000})
    r.check(not bad, "no sideways page at any width",
            ", ".join(bad) if bad else f"{len(WIDTHS)} widths x {len(ROOMS)} rooms")


def audit_targets(pg, base: str, r: Results) -> None:
    """Every control the thumb can reach clears 44px. PRODUCT.md commits to it
    for approval actions; nothing justifies the rest being smaller."""
    small: list[str] = []
    for room in ROOMS:
        pg.goto(f"{base}#{room}", wait_until="domcontentloaded")
        pg.wait_for_timeout(800)
        small += pg.evaluate("""(min) => {
          const out = [];
          const sel = 'button, [role=button], input[type=checkbox], select, a[href]';
          for (const el of document.querySelectorAll(sel)) {
            if (!el.offsetParent) continue;
            // Visually-hidden-until-focused controls (the skip link) measure
            // 1px by design; the pattern IS the clip. Measuring them unfocused
            // is the checker's error, not the page's.
            if (getComputedStyle(el).clipPath !== 'none') continue;
            const rect = el.getBoundingClientRect();
            if (rect.width < 1 || rect.height < 1) continue;
            if (rect.height < min - 0.5)
              out.push((el.id || el.className || el.tagName) + ' ' + Math.round(rect.height) + 'px');
          }
          return out;
        }""", MIN_TARGET)
    small = sorted(set(small))
    r.check(not small, f"every control clears {MIN_TARGET}px",
            f"{len(small)} under: {small[:5]}" if small else "all clear")


def audit_contrast(pg, base: str, r: Results) -> None:
    """Measured on the rendered page, not the token file. A token that passes
    in DESIGN.md still fails if a rule paints it on the wrong surface."""
    bad: list[str] = []
    for room in ROOMS:
        pg.goto(f"{base}#{room}", wait_until="domcontentloaded")
        pg.wait_for_timeout(800)
        bad += pg.evaluate(r"""(min) => {
          const lin = (c) => { c /= 255; return c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); };
          const lum = (v) => 0.2126*lin(v[0]) + 0.7152*lin(v[1]) + 0.0722*lin(v[2]);
          const nums = (s) => (s.match(/[\d.]+/g) || []).map(Number);
          // A gradient is a background-IMAGE, so backgroundColor reads as
          // transparent and a naive walker sails straight past a white paper
          // sheet to the navy body behind it — reporting 1.19:1 on text that
          // actually renders near 6:1. Every gradient surface in this
          // stylesheet now paints a colour beneath it, so the walker can stop
          // there; if one ever does not, we report UNKNOWN rather than
          // inventing a ratio. A checker that lies is worse than no checker.
          const groundOf = (el) => {
            for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
              const cs = getComputedStyle(n);
              const c = nums(cs.backgroundColor);
              const opaque = c.length >= 3 && (c[3] === undefined || c[3] > 0.85);
              if (opaque) return c;
              const img = cs.backgroundImage;
              if (img && img !== 'none') {
                // A translucent overlay (rgba stops, or a fade to transparent)
                // lets the real ground through — keep walking, the surface
                // beneath is the honest answer. Only a gradient of solid stops
                // actually hides what is behind it, and every one of those in
                // this stylesheet now paints a colour underneath, so reaching
                // this branch means a new surface skipped that rule.
                const translucent = /transparent/.test(img)
                  || /rgba\([^)]*,\s*0?\.\d+\s*\)/.test(img);
                if (!translucent) return null;
              }
            }
            return [6, 18, 46];
          };
          const out = [];
          for (const el of document.querySelectorAll('body *')) {
            if (!el.offsetParent || el.children.length) continue;
            const text = (el.textContent || '').trim();
            if (text.length < 2) continue;
            const cs = getComputedStyle(el);
            if (parseFloat(cs.opacity) < 0.95) continue;
            const fg = nums(cs.color);
            if (fg.length < 3 || (fg[3] !== undefined && fg[3] < 0.9)) continue;
            const ground = groundOf(el);
            if (!ground) { out.push('UNKNOWN GROUND: ' + text.slice(0, 20)); continue; }
            const a = lum(fg), b = lum(ground);
            const ratio = (Math.max(a,b) + 0.05) / (Math.min(a,b) + 0.05);
            const size = parseFloat(cs.fontSize);
            const large = size >= 24 || (size >= 18.66 && parseInt(cs.fontWeight) >= 700);
            if (ratio < (large ? 3 : min))
              out.push(text.slice(0, 22) + ' @' + ratio.toFixed(2));
          }
          return out;
        }""", MIN_CONTRAST)
    bad = sorted(set(bad))
    r.check(not bad, f"rendered text clears {MIN_CONTRAST}:1",
            f"{len(bad)} under: {bad[:5]}" if bad else "all clear")


# ---------------------------------------------------------------------------
# system integrity (no browser needed)
# ---------------------------------------------------------------------------

def audit_token_drift(r: Results) -> None:
    """The extension mirrors the palette inline because there is deliberately no
    build step. Its own comment claimed each hex 'appears exactly once' while it
    appeared twice, and nothing enforced it. This is that enforcement."""
    root = pathlib.Path(__file__).resolve().parents[3]
    css = (root / "server/app/web/styles.css").read_text(encoding="utf-8")
    pattern = re.compile(r"(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))")

    def norm(v: str) -> str:
        """Same colour, same string. CSS writes rgba(...,.28) and TypeScript
        writes 0.28; comparing spellings would report a drift that isn't one,
        and a gate that cries wolf gets switched off."""
        v = v.replace(" ", "").lower()
        return re.sub(r"(?<![0-9])\.(?=[0-9])", "0.", v)

    def tokens(text: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for m in pattern.finditer(text):
            out.setdefault(m.group(1), norm(m.group(2)))
        return out

    # The Expo app cannot use CSS variables, so theme.ts names the same colours
    # in camelCase. Mapped by hand because the mapping IS the fact worth
    # checking — a token added to one and not the other is exactly the drift.
    ts_alias = {"ground": "--ground", "panel": "--panel", "panel2": "--panel-2",
                "panel3": "--panel-3", "line": "--line", "ink": "--ink",
                "inkDim": "--ink-dim", "inkFaint": "--ink-faint",
                "brass": "--brass", "brassBright": "--brass-bright",
                "brassDeep": "--brass-deep", "lamp": "--lamp",
                "lampBright": "--lamp-bright", "marigold": "--marigold",
                "oxblood": "--oxblood"}
    ts_pattern = re.compile(r"\b([A-Za-z0-9]+):\s*\"(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))\"")

    def ts_tokens(text: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for m in ts_pattern.finditer(text):
            css_name = ts_alias.get(m.group(1))
            if css_name:
                out.setdefault(css_name, norm(m.group(2)))
        return out

    web = tokens(css)
    mirrors = {
        "extension": tokens((root / "extension/content.js").read_text(encoding="utf-8")),
        "mobile": ts_tokens((root / "mobile/src/theme.ts").read_text(encoding="utf-8")),
    }
    for name, mirror in mirrors.items():
        shared = sorted(set(web) & set(mirror))
        drift = [k for k in shared if web[k] != mirror[k]]
        label = f"{name} palette matches the board"
        if not shared:
            r.bad(label, "no shared tokens found")
        elif drift:
            r.bad(label, "; ".join(f"{k}: board {web[k]} vs {name} {mirror[k]}"
                                   for k in drift[:4]))
        else:
            r.ok(label, f"{len(shared)} tokens agree")


def audit_width_system(r: Results) -> None:
    """The measures live in :root and rooms opt in. A raw px cap creeping back
    into a room is how This Week ended up hanging left in the first place."""
    root = pathlib.Path(__file__).resolve().parents[3]
    css = (root / "server/app/web/styles.css").read_text(encoding="utf-8")
    # Strip @media preludes first — a breakpoint is not a room cap, and
    # counting one as a stray is how a gate teaches you to ignore it.
    declarations = re.sub(r"@media[^{]*\{", "{", css)
    strays = sorted(set(re.findall(r"max-width:\s*(\d{3,4})px", declarations)))
    room_scale = [px for px in strays if int(px) >= 800]
    r.check(not room_scale, "room widths come from the measures, not literals",
            f"stray room-scale caps: {room_scale}" if room_scale else "no literals >=800px")


# ---------------------------------------------------------------------------

def run(pg, base: str) -> Results:
    r = Results()
    audit_token_drift(r)
    audit_width_system(r)
    pg.goto(base, wait_until="networkidle")
    pg.wait_for_timeout(2000)
    audit_tablist(pg, r)
    audit_live_regions(pg, r)
    audit_deep_link(pg, base, r)
    audit_dialog_focus(pg, base, r)
    audit_board_columns(pg, base, r)
    audit_rail_contained(pg, base, r)
    audit_rows_never_clip(pg, base, r)
    audit_no_sideways(pg, base, r)
    audit_targets(pg, base, r)
    audit_contrast(pg, base, r)
    return r


def report(r: Results) -> int:
    width = max(len(name) for _, name, _ in r)
    for good, name, detail in r:
        print(f"  {'PASS' if good else 'FAIL'}  {name.ljust(width)}  {detail}")
    failed = [name for good, name, _ in r if not good]
    print(f"\nOK: all {len(r)} audit checks passed" if not failed
          else f"\nFAIL: {len(failed)} of {len(r)} failed: {failed}")
    return 0 if not failed else 1
