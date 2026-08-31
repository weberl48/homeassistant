"""Draft night, 2026-08-30 — the gaps a real draft found in a green suite.

Every proposal reviewed after that draft passed 277 tests, and six of them were
wrong. Three holes explain most of it:

1. **No test ever seeded `fp_ecr`.** It is a `source` value in the `adp` table,
   not a table, and nothing in the suite wrote one — so `ecr_rank` was empty
   everywhere, `blended_value` degenerated to the identity, and `experts_call`
   was permanently None. Half of every decision value the engine computes was
   unexercised by the whole suite.
2. **Nothing touched the endgame starvation guard.** No test mentioned `slack`,
   `urgency`, or `starvation`. A reviewer replaced that arithmetic in BOTH
   directions and the suite stayed green. It is the code that decides whether
   you finish the draft with a kicker.
3. **Nothing pinned the wire against the board.** The engine recommended a man
   at three times the runner-up score while nine items in its own `news` table
   said the league had placed him on the Commissioner's Exempt List.

These tests are the pins. Each names the live observation it comes from, so a
change that re-breaks one fails with the reason attached.
"""
from __future__ import annotations

from app import brain, db, demo
from app.engines import draft as draft_engine


# ---------------------------------------------------------------------------
# helpers


def _draft_to(conn, upto: int, my_slot: int, script: list[str]) -> None:
    """Run the ADP sim for the room, but hand MY seat a scripted shape: the
    best available at each named position.

    Continues from wherever the draft stands; it never restarts. Replaying
    from pick 1 does not reproduce the same draft (the sim skips players
    already taken), and a scrambled board silently defuses whatever the test
    was trying to pin — see the note on _run_draft in test_advisories.
    """
    players = brain._players_index(conn)
    start = conn.execute("SELECT COUNT(*) c FROM draft_picks").fetchone()["c"] + 1
    for pick_no in range(start, upto + 1):
        slot = demo.slot_for_pick(pick_no)
        if slot == my_slot and script:
            want = script.pop(0)
            taken = {r["player_id"] for r in conn.execute(
                "SELECT player_id FROM draft_picks")}
            pid = next((r["player_id"] for r in conn.execute(
                "SELECT player_id, adp FROM adp ORDER BY adp")
                if r["player_id"] not in taken
                and r["player_id"] in players
                and players[r["player_id"]]["pos"] == want), None)
        else:
            pid = demo._sim_pick_for_slot(conn, slot, pick_no)
        if pid is None:
            break
        demo.record_pick(conn, pick_no, pid)


def _seed_ecr(conn, ranks: dict[str, float] | None = None) -> dict[str, float]:
    """Give the board an expert sheet. Without one, half of blended_value is
    dead and experts_call never renders — which was true of every test in this
    project until now.

    Default: rank everyone by ADP, so the experts broadly agree with the market
    and the blend is exercised without being adversarial.
    """
    if ranks is None:
        rows = conn.execute("SELECT player_id FROM adp WHERE source='demo' "
                            "ORDER BY adp").fetchall()
        ranks = {r["player_id"]: float(i + 1) for i, r in enumerate(rows)}
    now = db.utcnow()
    for pid, rank in ranks.items():
        conn.execute(
            "INSERT OR REPLACE INTO adp(player_id,source,adp,stdev,updated_at) "
            "VALUES(?,?,?,?,?)", (pid, "fp_ecr", rank, 8.0, now))
    conn.commit()
    return ranks


def _news(conn, pid: str, headline: str, severity: str = "out") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO news(guid,seq,source,player_id,name_raw,headline,"
        "body,link,severity,ailment,departure,published_at,fetched_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
        (f"t-{pid}-{severity}", 1, "rotowire", pid, "x", headline, "", "",
         severity, None, 1))
    conn.commit()


# ---------------------------------------------------------------------------
# 1. The expert sheet exists at all


def test_the_expert_sheet_is_actually_exercised(conn):
    """The hole itself: prove a seeded fp_ecr reaches the board. If this fails,
    every other ECR assertion in the suite is vacuous."""
    _seed_ecr(conn)
    _draft_to(conn, 30, 7, [])
    board = brain.get_board(conn)
    assert board["experts_call"] is not None, "experts_call never renders"
    assert board["experts_call"]["ecr"] > 0


def test_the_blend_moves_a_value(conn):
    """blended_value is 50% expert rank. With no fp_ecr rows it is the identity
    — which is how it behaved in all 277 tests before this one. Rank a mid
    board player first overall and his standing must improve."""
    _draft_to(conn, 30, 7, [])
    plain = {s["id"] for s in brain.get_board(conn)["suggestions"]}
    avail = [r for r in brain.get_board(conn)["players"] if "pick_no" not in r]
    dark_horse = avail[12]["id"]
    _seed_ecr(conn, {dark_horse: 1.0})
    blended = {s["id"] for s in brain.get_board(conn)["suggestions"]}
    assert blended != plain or dark_horse in blended, (
        "seeding the experts changed nothing — the blend is inert")


# ---------------------------------------------------------------------------
# 2. The endgame guard — zero committed coverage before this


def test_a_man_who_cannot_start_never_tops_the_board(conn):
    """The guard's whole purpose. With the roster full at a position and the
    draft closing, a luxury body there must not outrank a man who fills a slot
    still standing open."""
    _seed_ecr(conn)
    # Fill every skill slot, leave K and DEF open, and run to the last rounds.
    _draft_to(conn, 150, 7, ["RB", "WR", "RB", "WR", "TE", "QB", "WR", "RB",
                             "WR", "TE", "RB", "QB"])
    board = brain.get_board(conn)
    if board["draft"]["status"] == "complete":
        return
    mine = {}
    for p in board["my_roster"]:
        mine[p["pos"]] = mine.get(p["pos"], 0) + 1
    open_slots = {p for p in ("K", "DEF") if not mine.get(p)}
    if not open_slots:
        return
    top = board["suggestions"][0]
    assert top["pos"] in open_slots or mine.get(top["pos"], 0) < 2, (
        f"The Call named {top['name']} ({top['pos']}) with {open_slots} "
        f"still empty and {board['draft']['rounds']} rounds gone")


def test_the_luxury_markdown_does_not_promote_negatives():
    """`scores *= 0.05` moves a NEGATIVE score toward zero. A luxury body at
    -30 became -1.5 and outranked a starving-position candidate at -2 — the
    markdown promoting exactly what it exists to bury.

    Calls brain.luxury_markdown, which is why that expression is a named
    function: the first version of this test re-typed the formula into the
    assertion and pinned nothing at all.
    """
    assert brain.luxury_markdown(30.0) == 1.5
    assert brain.luxury_markdown(0.0) == 0.0
    # The whole point: a marked-down luxury body must stay below a starving
    # candidate, not float up past him.
    assert brain.luxury_markdown(-30.0) < -2.0


def test_a_second_kicker_is_dead_weight(conn):
    """The clamp checked starters-open BEFORE it checked whether you already
    had a kicker, so a second one scored 0.25 for nearly the whole draft and
    brain's `mult <= 0.05` skip never fired."""
    rp = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"] + ["BN"] * 6
    have_one = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1}
    assert draft_engine.roster_need_multiplier("K", have_one, rp) == 0.05
    # ...and the first kicker is still worth having while slots are open.
    assert draft_engine.roster_need_multiplier("K", {"QB": 1}, rp) == 0.25


def test_flex_spellings_agree(conn):
    """roster_need_multiplier counted two spellings while the endgame guard
    counted five, so the two disagreed about how many slots were open."""
    # Dedicated WR slots full, flex still open: the next receiver is a flex
    # body and must price as one under every spelling of the slot.
    counts = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
    for spelling in ("FLEX", "REC_FLEX", "WRRB_FLEX"):
        rp = ["QB", "RB", "RB", "WR", "WR", "TE", spelling, "K", "DEF"]
        assert draft_engine.roster_need_multiplier("WR", counts, rp) == 0.85, spelling
    # And with no flex slot at all he is bench depth, whatever it is called.
    bare = ["QB", "RB", "RB", "WR", "WR", "TE", "K", "DEF"]
    assert draft_engine.roster_need_multiplier("WR", counts, bare) == 0.55


# ---------------------------------------------------------------------------
# 3. The wire reaches the board — the Jacobs case


def test_a_flagged_man_is_never_recommended(conn):
    """THE draft-night failure, in one test.

    A man with a fat, stale projection — still the best number on the board —
    whom the wire says is not playing. He may keep his score and his place on
    the board. He may not be named by The Call.
    """
    _seed_ecr(conn)
    _draft_to(conn, 30, 7, [])
    board = brain.get_board(conn)
    best = board["suggestions"][0]
    _news(conn, best["id"], "Placed on the commissioner's exempt list")
    after = brain.get_board(conn)
    named = [s["id"] for s in after["suggestions"]]
    assert best["id"] not in named, (
        f"{best['name']} is on the exempt list and still tops The Call")
    # ...but he is still ON the board, carrying his flag.
    row = next(r for r in after["players"] if r["id"] == best["id"])
    assert row.get("news", {}).get("severity") == "out"
    assert row.get("score") is not None, "suppressed from advice, not deleted"


def test_the_experts_do_not_launder_a_flagged_man(conn):
    """A second opinion that repeats the first opinion's blind spot is worse
    than no second opinion."""
    _draft_to(conn, 30, 7, [])
    avail = [r for r in brain.get_board(conn)["players"] if "pick_no" not in r]
    top = avail[0]["id"]
    _seed_ecr(conn, {top: 1.0})
    _news(conn, top, "NFL places him on paid leave")
    board = brain.get_board(conn)
    assert (board["experts_call"] or {}).get("id") != top


def test_a_knock_is_not_a_blackout(conn):
    """Questionable men play most weeks. A board that refuses to name anybody
    carrying a knock names nobody in November."""
    _seed_ecr(conn)
    _draft_to(conn, 30, 7, [])
    best = brain.get_board(conn)["suggestions"][0]
    _news(conn, best["id"], "Questionable for Sunday", severity="questionable")
    after = brain.get_board(conn)
    assert best["id"] in [s["id"] for s in after["suggestions"]]


def test_the_shortlist_never_goes_empty(conn):
    """Late in a draft the pool thins enough that everyone left can be
    flagged. A flagged man with his flag showing beats no advice at all."""
    _draft_to(conn, 30, 7, [])
    board = brain.get_board(conn)
    for r in board["players"]:
        if "pick_no" not in r:
            _news(conn, r["id"], "Placed on injured reserve")
    after = brain.get_board(conn)
    assert after["suggestions"], "every man flagged and The Call went silent"


def test_the_board_says_how_old_its_sheet_is(conn):
    """The pick feed's heartbeat was two seconds old next to an eleven-hour-old
    sheet, and nothing on screen distinguished them."""
    board = brain.get_board(conn)
    assert board["draft"]["sheet_as_of"], "no sheet_as_of on the payload"


# ---------------------------------------------------------------------------
# 4. Freshness and the men who leave the sheet


def test_a_released_player_does_not_break_the_board(conn):
    """etl_players keeps only positions we care about ON A TEAM, so the day a
    rostered man is cut he vanishes from the index while his pick row stays
    forever. Every lookup in `recent` and `my_roster` was a bare subscript —
    a 500 on the main board the afternoon somebody gets released. Forty-three
    such picks already existed in this league's past drafts."""
    _draft_to(conn, 30, 7, [])
    # Somebody inside the last twelve picks, so he is on the feed the board
    # actually renders (recent_picks is picks[-12:]).
    gone = conn.execute(
        "SELECT player_id FROM draft_picks ORDER BY pick_no DESC LIMIT 1").fetchone()[0]
    conn.execute("DELETE FROM players WHERE sleeper_id=?", (gone,))
    conn.commit()
    board = brain.get_board(conn)          # must not raise
    assert board["recent_picks"], "the feed went empty rather than degrading"
    names = [r["player"] for r in board["recent_picks"]] + \
            [r["player"] for r in board["my_roster"]]
    assert "(no longer rostered)" in names, (
        "the released man vanished silently instead of being rendered as a "
        "pick whose player is gone")
