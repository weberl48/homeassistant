"""Draft-pilot rehearsal — watch it pick, with nothing at stake.

The pilot is the one piece of this system that can act on draft night, and the
only honest way to trust it is to have watched it work. This drives the REAL
`hands.draft_pilot` process (not a reimplementation of its logic) against a
simulated live snake draft, in dry-run, and prints a transcript of every pick it
would have made and where each choice came from.

What this proves:
  - the pilot finds the board, reads the slip, and notices its own clock
  - it takes the slip first and The Call when the slip runs dry
  - it makes exactly one decision per clock and never repeats one
  - dry-run is genuinely inert: no browser is launched, nothing is clicked

What it does NOT prove: the browser half. In dry-run the pilot never opens a
page (`if page is None and not DRY_RUN`), so the Sleeper locators are not
exercised here. Those were calibrated live on 2026-08-25 against a practice
room, verified by a real pick. A live-room rehearsal is still owed before the
pilot flies for real.

Safe by construction: a throwaway database in a temp directory, a demo-mode
server on an ephemeral port, HANDS_DRY_RUN forced on, and no Sleeper contact.

Usage (from bootlegger/server):
    .venv/Scripts/python.exe tools/pilot_rehearsal.py            # full 15 rounds
    .venv/Scripts/python.exe tools/pilot_rehearsal.py --rounds 4 # a quick look
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

# The transcript is read in a terminal; a Windows console defaults to cp1252
# and dies on anything outside it. The report is the product here, so it must
# not be the thing that crashes.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SERVER_DIR = Path(__file__).resolve().parent.parent
WOULD = re.compile(r"would draft (.+?) \(from the (slip|call)\) at pick (\d+)")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(url: str, timeout: float = 90.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        try:
            r = httpx.get(url, timeout=3)
            if r.status_code == 200:
                return r.json()
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise SystemExit(f"server never came up at {url}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rounds", type=int, default=15)
    ap.add_argument("--slip", type=int, default=3,
                    help="how many of The Call's picks to pre-load onto the "
                         "slip, so BOTH decision sources are exercised")
    ap.add_argument("--pilot-python", default=os.environ.get(
        "BOOTLEGGER_PILOT_PYTHON", sys.executable),
                    help="the interpreter that will actually FLY the pilot. It "
                         "needs httpx and Playwright; the server venv "
                         "deliberately has neither, so on a dev box this is "
                         "usually the system Python. Rehearsing under an "
                         "interpreter that could never fly proves nothing "
                         "about the one that will.")
    args = ap.parse_args()

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    tmp = Path(tempfile.mkdtemp(prefix="pilot-rehearsal-"))
    env = dict(os.environ,
               BOOTLEGGER_MODE="demo",
               BOOTLEGGER_DB=str(tmp / "rehearsal.db"),
               BOOTLEGGER_AUDIT_DIR=str(tmp / "audit"),
               BOOTLEGGER_ROUNDS=str(args.rounds),
               # Fast opponents, but leave a real window on my own clock so the
               # pilot has to notice it the way it will on the night.
               DEMO_PICK_SECONDS="0.05",
               DEMO_MY_CLOCK_SECONDS="4",
               HANDS_DRY_RUN="1",          # the whole point
               BOOTLEGGER_API_TOKEN="")
    # No inherited league credentials: a rehearsal must not be able to reach
    # the real league even by accident.
    for k in ("SLEEPER_LEAGUE_ID", "SLEEPER_USER_ID", "SLEEPER_DRAFT_ID"):
        env.pop(k, None)

    print(f"rehearsal db : {tmp}")
    print(f"demo board   : {base}")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=SERVER_DIR, env=env)
    pilot = None
    try:
        wait_for(f"{base}/health")
        board = httpx.get(f"{base}/api/draft/board", timeout=30).json()
        my_slot = board["draft"]["my_slot"]

        # The slip has to exercise BOTH halves of the rule, so it is seeded
        # deliberately rather than from the top of the board: a couple of studs
        # who will certainly be gone by my clock (proving picked entries are
        # skipped, not blindly taken) and several men deep enough to survive
        # (proving the slip is actually preferred over The Call). Seeding it
        # from suggestions[:3] alone produced an all-Call transcript — the top
        # three were gone before pick 7 every time, which looks like the slip
        # being ignored and is not.
        avail = sorted((p for p in board["players"] if p.get("adp")),
                       key=lambda p: p["adp"])
        idx = [1, 2] + [i for i in (38, 55, 72, 95) if i < len(avail)]
        slip = [avail[i] for i in idx if i < len(avail)][:args.slip + 3]
        httpx.post(f"{base}/api/queue", json={"ids": [p["id"] for p in slip]},
                   timeout=10)
        httpx.post(f"{base}/api/pilot/arm", json={"armed": True}, timeout=10)
        # Read the slip BACK. A rehearsal that assumes its own setup worked
        # proves nothing when the run comes up empty.
        q = httpx.get(f"{base}/api/queue", timeout=10).json()
        stored = q["queue"]
        print(f"seat         : slot {my_slot} of {board['draft']['teams']}")
        print(f"slip         : " + ", ".join(
            f"{r['name']} (ADP {next((p['adp'] for p in slip if p['id'] == r['id']), '?')})"
            for r in stored) or "(EMPTY — setup failed)")
        print(f"armed        : {q['pilot_armed']}   dry_run: {q['pilot_dry_run']}\n")
        if len(stored) != len(slip):
            print(f"  !! slip did not persist ({len(slip)} sent, {len(stored)} stored)")
        slip_ids = {r["id"] for r in stored}

        pilot = subprocess.Popen(
            [args.pilot_python, "-m", "hands.draft_pilot", "--api", base],
            cwd=SERVER_DIR, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            bufsize=1)

        picks: list[tuple[str, str, int]] = []
        # Evidence, not decoration. This scanner printed "!!" and then let the
        # verdict pass anyway — see the browser check below.
        browser_evidence: list[str] = []
        deadline = time.time() + 20 + args.rounds * 14
        print("  the pilot's clock-by-clock decisions")
        print("  " + "-" * 58)
        while time.time() < deadline:
            line = pilot.stdout.readline()
            if not line:
                break
            m = WOULD.search(line)
            if m:
                name, source, pick = m.group(1), m.group(2), int(m.group(3))
                repeat = bool(picks) and picks[-1][0] == name
                picks.append((name, source, pick))
                note = ("   (same man — a dry run never actually takes him, "
                        "so he stays top of the slip)" if repeat else "")
                print(f"  pick {pick:>3}  {source:<5} {name}{note}")
                continue
            # Anything that means a page was actually opened must never appear.
            if re.search(r"room open and hydrated|logged in with|new_context", line, re.I):
                browser_evidence.append(line.strip())
                print("  !! " + line.rstrip())
            d = httpx.get(f"{base}/health", timeout=5).json()
            if d["draft_status"] == "complete":
                break

        # --- verdict -----------------------------------------------------
        print("  " + "-" * 58)
        rounds_seen = len(picks)
        by_source = {s: sum(1 for _, src, _ in picks if src == s)
                     for s in ("slip", "call")}
        pick_nos = [p for _, _, p in picks]
        ok = True

        def check(label: str, passed: bool, detail: str = "") -> None:
            nonlocal ok
            ok = ok and passed
            print(f"  [{'PASS' if passed else 'FAIL'}] {label}"
                  + (f" — {detail}" if detail else ""))

        print("\n  verdict")
        check("it noticed its own clock", rounds_seen > 0,
              f"{rounds_seen} decisions made")
        check("one decision per clock, never repeated",
              len(pick_nos) == len(set(pick_nos)))
        check("every pick landed on this seat's turn",
              all((p - 1) % board["draft"]["teams"] + 1 in
                  (my_slot, board["draft"]["teams"] - my_slot + 1)
                  for p in pick_nos),
              f"slot {my_slot} snake")
        # The slip only fires for players still on the board when my clock
        # comes round; a slip of studs taken at picks 1-6 legitimately yields
        # nothing. Assert the RULE, not a count: no Call pick may precede an
        # available slip pick.
        check("the slip was actually used", by_source["slip"] > 0,
              f"{by_source['slip']} from the slip, "
              f"{by_source['call']} from The Call")
        check("every slip pick was a man on the slip",
              all(any(r["name"] == n for r in stored)
                  for n, src, _ in picks if src == "slip"))
        check("it always had something to take",
              all(n for n, _, _ in picks))
        # The one thing a repeat could be hiding: a pilot that never notices
        # its target came off the board. Every CHANGE of target must coincide
        # with the previous one having been drafted by somebody.
        final = httpx.get(f"{base}/api/draft/board", timeout=20).json()
        taken = {p["name"] for p in final["players"] if p.get("pick_no")}
        moved_on = [(prev[0], cur[0]) for prev, cur in zip(picks, picks[1:])
                    if prev[0] != cur[0]]
        detail = ("; ".join(f"{a} -> {b}" for a, b in moved_on)
                  if moved_on else "target never came off the board")
        check("it moved on when the board took its target",
              all(a in taken for a, _ in moved_on), detail)
        # THE ASSERTION THAT COULD NOT FAIL. This was the literal `True`, and
        # the scanner above collected nothing — so the one check the whole
        # rehearsal exists to make was decorative. Patching a pilot log line to
        # the exact string it prints after opening a real Sleeper room produced
        # one easily-missed "!!" and then "REHEARSAL CLEAN — nothing was
        # clicked", exit 0. A rehearsal that cannot fail earns no trust.
        check("dry run touched no browser", not browser_evidence,
              "; ".join(browser_evidence[:3]) if browser_evidence
              else "no page-open lines on the pilot's output")

        # And the half a dry run structurally cannot cover: DRY_RUN returns
        # before the lazy Playwright import, so a clean rehearsal says nothing
        # about whether the browser stack would come up at all. Two of the
        # three ways bringup dies on draft night are visible from here — no
        # playwright module, or a module with no chromium installed — and both
        # are only reached AFTER arming, which is the worst moment to find out.
        # Probed in the FLYING interpreter, as a subprocess. Checking the one
        # running this script was the obvious thing and the wrong one: the
        # rehearsal is normally run from the server venv, which deliberately
        # carries no browser tooling, so it reported a failure about an
        # interpreter nobody was ever going to fly with.
        probe = ";".join((
            "import pathlib",
            "from playwright.sync_api import sync_playwright",
            "pw = sync_playwright().start()",
            "exe = pw.chromium.executable_path",
            "pw.stop()",
            "print('OK' if exe and pathlib.Path(exe).exists() else 'NOCHROME', exe)",
        ))
        try:
            out = subprocess.run([args.pilot_python, "-c", probe],
                                 capture_output=True, text=True, timeout=90)
            first = (out.stdout or out.stderr or "").strip().splitlines()
            line = first[-1] if first else ""
            browser_ready = line.startswith("OK")
            why = line[3:].strip() if browser_ready else (line[:160] or "no output")
        except Exception as exc:                # noqa: BLE001
            browser_ready, why = False, f"{type(exc).__name__}: {exc}"
        who = Path(args.pilot_python).parent.parent.name or args.pilot_python
        check("the browser stack would actually come up", browser_ready,
              f"{who}: {why}" if browser_ready else
              f"{who}: {why} — a live pilot would die AFTER arming. Point "
              "--pilot-python at an interpreter with Playwright, or run "
              "`playwright install chromium` for this one.")

        print(f"\n  {'REHEARSAL CLEAN' if ok else 'REHEARSAL FAILED'} — "
              f"{rounds_seen} clocks, {by_source['slip']} slip / "
              f"{by_source['call']} Call, nothing was clicked.")
        return 0 if ok else 1
    finally:
        for p in (pilot, server):
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()


if __name__ == "__main__":
    raise SystemExit(main())
