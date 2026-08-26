"""The League room: every seat scouted on one screen — season records carried
off the wire, and each roster's positional surplus read against the field."""
from __future__ import annotations

import pytest

from app import brain, ingest
from app.config import settings

POS = ("QB", "RB", "WR", "TE")


def test_demo_seeds_a_balanced_finished_season(conn):
    """Records only mean something if they could have come from real games:
    every seat plays the same slate, and each game hands out exactly one win
    and one loss."""
    rows = conn.execute(
        "SELECT roster_id,wins,losses,ties,fpts FROM rosters ORDER BY roster_id").fetchall()
    assert len(rows) == settings.teams

    played = {r["wins"] + r["losses"] + r["ties"] for r in rows}
    assert len(played) == 1, f"seats played different slates: {played}"
    assert played.pop() > 0, "a finished season means games were played"

    assert sum(r["wins"] for r in rows) == sum(r["losses"] for r in rows), \
        "every game hands out one win and one loss"
    assert all(r["fpts"] > 0 for r in rows), "a played season scores points"


def test_seeded_season_agrees_with_the_ranking_beside_it(conn):
    """Points-for and the roster ranking must come off the same book. Simulated
    from a different basis (week 1 vs the season) they contradict each other on
    screen: the seat ranked first reads last in scoring for no visible reason."""
    seats = brain.league_overview(conn)["seats"]
    proj = [s["proj"] for s in seats]
    pf = [s["record"]["fpts"] for s in seats]

    n = len(seats)
    mp, mf = sum(proj) / n, sum(pf) / n
    cov = sum((p - mp) * (f - mf) for p, f in zip(proj, pf))
    sp = sum((p - mp) ** 2 for p in proj) ** 0.5
    sf = sum((f - mf) ** 2 for f in pf) ** 0.5
    r = cov / (sp * sf)

    assert r > 0.5, (
        f"points-for barely tracks roster strength (r={r:.2f}) — the standings "
        "and the ranking are being computed off different projections")


def test_league_overview_ranks_every_seat(conn):
    seats = brain.league_overview(conn)["seats"]
    assert len(seats) == settings.teams
    assert sorted(s["rank"] for s in seats) == list(range(1, settings.teams + 1))
    assert sum(1 for s in seats if s["mine"]) == 1, "exactly one seat is mine"

    projs = [s["proj"] for s in seats]
    assert projs == sorted(projs, reverse=True), "seats are ranked by projection"
    assert all(s["proj"] > 0 for s in seats)
    assert all(s["owner"] for s in seats)


def test_overview_projection_is_a_starting_lineup_not_the_whole_roster(conn):
    """Ranking on total roster points rewards hoarding. The number has to be
    what the seat can actually start."""
    seats = brain.league_overview(conn)["seats"]
    for s in seats:
        bench_and_all = sum(p["pts"] for p in
                            brain.league_rosters(conn)["rosters"][s["roster_id"] - 1]["players"])
        assert s["proj"] < bench_and_all, \
            f"{s['owner']}: proj {s['proj']} counts the bench"


def test_positional_z_scores_are_centred_and_ordered(conn):
    """The grid's ++/-- glyphs are z-scores, so they must be a real
    distribution over the field: centred on zero, and the deepest room at a
    position is the one that reads highest."""
    seats = brain.league_overview(conn)["seats"]
    for pos in POS:
        zs = [s["by_pos"][pos]["z"] for s in seats]
        assert abs(sum(zs)) < 1e-6, f"{pos}: z-scores must centre on zero, got {sum(zs)}"
        best = max(seats, key=lambda s: s["by_pos"][pos]["pts"])
        assert best["by_pos"][pos]["z"] == pytest.approx(max(zs)), \
            f"{pos}: deepest room does not read highest"


def test_a_stacked_room_reads_as_surplus(conn):
    """Hand one seat the four best backs in the league and the page must say
    so — this is the read the Parlor turns into a deal."""
    import json

    rosters = brain.league_rosters(conn)["rosters"]
    mine = next(r for r in rosters if r["mine"])
    victim = next(r for r in rosters if not r["mine"])

    best_rbs = [p["id"] for r in rosters for p in r["players"] if p["pos"] == "RB"][:4]
    keep = [p["id"] for p in mine["players"] if p["id"] not in best_rbs]
    conn.execute("UPDATE rosters SET players_json=? WHERE roster_id=?",
                 (json.dumps(keep + best_rbs), mine["roster_id"]))
    conn.execute("UPDATE rosters SET players_json=? WHERE roster_id=?",
                 (json.dumps([p["id"] for p in victim["players"]
                              if p["id"] not in best_rbs]), victim["roster_id"]))
    conn.commit()

    seat = next(s for s in brain.league_overview(conn)["seats"] if s["mine"])
    assert "RB" in seat["surplus"], f"stacked RB room not read as surplus: {seat['surplus']}"
    assert "RB" in seat["read"], f"read does not mention the RB room: {seat['read']!r}"


def test_etl_rosters_captures_the_record_off_the_wire(conn):
    """Sleeper carries the record in roster.settings and splits the score into
    fpts + fpts_decimal; etl_rosters used to drop the whole block."""
    class FakeClient:
        def users(self, league_id):
            return [{"user_id": "u1", "display_name": "Ada"}]

        def rosters(self, league_id):
            return [{"roster_id": 1, "owner_id": "u1", "players": [], "starters": [],
                     "settings": {"wins": 9, "losses": 4, "ties": 1,
                                  "fpts": 1523, "fpts_decimal": 45}}]

    ingest.etl_rosters(FakeClient(), conn)

    row = conn.execute("SELECT owner,wins,losses,ties,fpts FROM rosters "
                       "WHERE roster_id=1").fetchone()
    assert row["owner"] == "Ada"
    assert (row["wins"], row["losses"], row["ties"]) == (9, 4, 1)
    assert row["fpts"] == pytest.approx(1523.45)


def test_etl_rosters_survives_a_roster_with_no_settings(conn):
    """A brand-new league returns rosters before any games — no settings block.
    That must read as 0-0, not crash the nightly."""
    class FakeClient:
        def users(self, league_id):
            return [{"user_id": "u2", "display_name": "Bo"}]

        def rosters(self, league_id):
            return [{"roster_id": 2, "owner_id": "u2", "players": [], "starters": []}]

    ingest.etl_rosters(FakeClient(), conn)

    row = conn.execute("SELECT wins,losses,ties,fpts FROM rosters WHERE roster_id=2").fetchone()
    assert (row["wins"], row["losses"], row["ties"], row["fpts"]) == (0, 0, 0, 0.0)
