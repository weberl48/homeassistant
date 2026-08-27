"""Wire alerts: deciding whose phone deserves to ring.

The wire carries every NFL player. Almost none of them are yours, and of the
ones that are, most items are noise ("participated in joint practice"). This
module is the filter between a feed and a notification, and it is deliberately
stingy — a wire that buzzes for everything gets muted, and a muted wire is
worse than no wire at all.

Three audiences, three treatments:

- **Yours, and it changes Sunday** (out / doubtful) — the DND-bypass emergency
  channel, and a lineup scan is kicked immediately so the swap is already
  proposed by the time the phone is picked up.
- **Yours, and it's worth knowing** (questionable / practice / role) — the
  normal channel, at most one digest per pass so five practice reports arrive
  as one line.
- **Somebody else's man, and he's gone for a while** — the job behind him just
  opened. That is a waiver window, not an emergency: normal channel, and only
  for a departure (IR, released, suspended, season-ending), never for a
  questionable tag.

Two marks, not one. `seen_at` says the filter has judged an item; `pushed_at`
says a phone actually rang for it. Collapsing them into one column made the
ledger claim a notification for every street item the filter deliberately
ignored — and, because the re-grade pass skips notified rows, froze those
items' grades against every later improvement to the classifier.

Every item notifies at most once: `pushed_at` is that ledger, and the poll is
idempotent because the feed's own guid is the primary key.
"""
from __future__ import annotations

import json
import sqlite3

from . import brain, db, push
from .engines import wire as wire_engine

# How far back a freshly-matched item may still be worth a push. An item that
# surfaces late (a name that only joined after tonight's player refresh) is
# still news; a three-day-old practice report is not.
MAX_AGE_H = 12.0
# One pass never sends more than this many notifications, however busy the
# wire is. The overflow still lands in the feed and the ledger.
MAX_PUSHES = 4


def _my_ids(conn: sqlite3.Connection) -> tuple[set[str], set[str]]:
    """(my players, my current starters). Empty sets pre-draft."""
    row = brain.my_roster_row(conn)
    if not row:
        return set(), set()
    try:
        return (set(json.loads(row["players_json"] or "[]")),
                set(json.loads(row["starters_json"] or "[]")))
    except (ValueError, TypeError):
        return set(), set()


def _league_ids(conn: sqlite3.Connection) -> set[str]:
    out: set[str] = set()
    for r in conn.execute("SELECT players_json FROM rosters"):
        try:
            out |= set(json.loads(r["players_json"] or "[]"))
        except (ValueError, TypeError):
            continue
    return out


def audience(player_id: str | None, mine: set[str], league: set[str]) -> str:
    """Whose story this is: mine | league | street."""
    if player_id and player_id in mine:
        return "mine"
    if player_id and player_id in league:
        return "league"
    return "street"


def pending(conn: sqlite3.Connection, max_age_h: float = MAX_AGE_H) -> list[sqlite3.Row]:
    """Matched wire items the filter has not judged yet, young enough to matter.

    Gated on `seen_at`, not `pushed_at`: most items are somebody else's man and
    are deliberately passed over, and stamping those as "pushed" would both
    make the ledger lie and freeze their grade against a later re-read.

    Two corrections, both of which made this quieter than it looks.

    THE LATCH. seen_at alone made this one-way. ingest.etl_news re-grades an
    item in place while `pushed_at IS NULL` — that is the whole point of the
    re-grade, since the classifier keeps improving — but a rival's man who
    arrived "Questionable" was stamped seen on the pass that passed him over,
    so when the same guid came back as "Placed on injured reserve" the row WAS
    corrected in the database and could never be considered again. The news
    that opens a job never reached the phone. An item is pending again when its
    grade has moved since it was last judged, which is exactly when it is news.

    THE CLOCK. published_at is written as ISO-8601 ('...T01:00:00+00:00') and
    SQLite's datetime() returns a space-separated string, so a plain string
    comparison diverges at byte 10 ('T' 0x54 against ' ' 0x20) and the window
    stopped being an hours window at all — measured: 13h, 18h and 24h all
    passed a 12-hour guard. Comparing on a normalized form makes the window the
    length it claims to be.
    """
    return conn.execute(
        "SELECT * FROM news WHERE player_id IS NOT NULL "
        "AND severity <> 'info' "
        "AND (seen_at IS NULL OR seen_severity IS NULL OR seen_severity <> severity) "
        "AND (published_at IS NULL "
        "     OR REPLACE(SUBSTR(published_at, 1, 19), 'T', ' ') >= datetime('now', ?)) "
        "ORDER BY published_at DESC",
        (f"-{max_age_h} hours",),
    ).fetchall()


def _mark(conn: sqlite3.Connection, guids: list[str], pushed: bool = False) -> None:
    """Record that the filter has judged these items — and, separately, whether
    a notification actually went out for them.

    seen_severity records WHAT was judged, not merely that something was. The
    grade is re-derived on every poll while an item sits unpushed (the
    classifier keeps improving), so without it a passed-over item that later
    turns into real news is invisible to pending() forever.
    """
    if not guids:
        return
    now = db.utcnow()
    if pushed:
        conn.executemany(
            "UPDATE news SET pushed_at=?, seen_at=?, seen_severity=severity "
            "WHERE guid=?", [(now, now, g) for g in guids])
    else:
        conn.executemany(
            "UPDATE news SET seen_at=?, seen_severity=severity WHERE guid=?",
            [(now, g) for g in guids])
    conn.commit()


def scan(conn: sqlite3.Connection, week: int | None = None) -> dict:
    """One notification pass over the wire. Returns what it did, so the season
    loop can log it and /health can show that the wire is awake."""
    mine, starters = _my_ids(conn)
    league = _league_ids(conn)
    rows = pending(conn)
    if not rows:
        return {"considered": 0, "alarm": 0, "notice": 0, "window": 0}

    alarm: list[sqlite3.Row] = []
    notice: list[sqlite3.Row] = []
    window: list[sqlite3.Row] = []
    seen: list[str] = []
    # ONE STORY, ONE PUSH. Five newsrooms carry the same ruling, so five rows
    # with five distinct guids reach here for one fact — and every existing
    # dedupe (pushed_at, seen_at) passes each of them, because each really is
    # a different item. Measured before this guard: a starter ruled out
    # produced FOUR identical DND pushes and burned MAX_PUSHES, so the pass's
    # genuinely different news was stamped seen and never sent at all.
    #
    # The display path has collapsed these since the wire went multi-source
    # (corroborate, below); the push path had not, which is the more expensive
    # of the two places to get it wrong. Same key: same player, same grade,
    # same day. Every duplicate is still marked seen, so nothing lingers.
    pushed_story: set[tuple] = set()

    def first_of_its_story(r: sqlite3.Row) -> bool:
        key = (r["player_id"], r["severity"], (r["published_at"] or "")[:10])
        if key in pushed_story:
            return False
        pushed_story.add(key)
        return True

    for r in rows:
        who = audience(r["player_id"], mine, league)
        seen.append(r["guid"])
        if not first_of_its_story(r):
            continue                    # a second desk on a story already queued
        if who == "mine":
            # A man on the bench going Out changes nothing about Sunday; the
            # alarm is for someone the optimizer is currently starting.
            if r["severity"] in wire_engine.ALARM and r["player_id"] in starters:
                alarm.append(r)
            elif r["severity"] in wire_engine.NOTIFY:
                notice.append(r)
        elif who == "league" and r["departure"]:
            window.append(r)
        # 'street' items reach the feed and the waiver surface, never a push.

    sent = 0
    for r in alarm[:MAX_PUSHES]:
        tail = f" ({r['ailment']})" if r["ailment"] else ""
        push.send(conn, "The wire — your starter",
                  f"{r['name_raw']}{tail}: {r['headline']}.",
                  push.CHANNEL_EMERGENCY,
                  data={"deep_link": "bootlegger://week", "guid": r["guid"]})
        sent += 1

    if notice and sent < MAX_PUSHES:
        head = notice[0]
        more = f" (+{len(notice) - 1} more on the wire)" if len(notice) > 1 else ""
        push.send(conn, "The wire",
                  f"{head['name_raw']}: {head['headline']}.{more}",
                  push.CHANNEL_NORMAL,
                  data={"deep_link": "bootlegger://week", "guid": head["guid"]})
        sent += 1

    if window and sent < MAX_PUSHES:
        head = window[0]
        more = f" (+{len(window) - 1} more)" if len(window) > 1 else ""
        push.send(conn, "The wire — a job just opened",
                  f"{head['name_raw']}: {head['headline']}. "
                  f"Check the street for the man behind him.{more}",
                  push.CHANNEL_NORMAL,
                  data={"deep_link": "bootlegger://waivers", "guid": head["guid"]})
        sent += 1

    # Everything the filter looked at is now judged; only the items that
    # actually reached a phone are recorded as pushed.
    notified = [r["guid"] for r in alarm[:MAX_PUSHES]]
    if notice and len(notified) < MAX_PUSHES:
        notified.append(notice[0]["guid"])
    if window and len(notified) < MAX_PUSHES:
        notified.append(window[0]["guid"])
    _mark(conn, [g for g in seen if g not in set(notified)])
    _mark(conn, notified, pushed=True)

    # An alarm means the lineup the owner has set is now wrong. Propose the fix
    # before they open the app rather than waiting for the next 5-minute scan.
    if alarm and week:
        try:
            from . import recs
            recs.scan_lineup(conn, week=week)
        except Exception:  # a scan failure must not swallow the alert itself
            pass

    return {"considered": len(rows), "alarm": len(alarm),
            "notice": len(notice), "window": len(window), "pushes": sent}


def feed(conn: sqlite3.Connection, limit: int = 40) -> dict:
    """The wire as the board reads it: newest first, tagged by audience, with
    the poll's own health attached so a stalled or skipping feed is visible
    rather than looking like a quiet news day."""
    mine, _ = _my_ids(conn)
    league = _league_ids(conn)
    rows = conn.execute(
        "SELECT n.*, p.pos, p.team FROM news n "
        "LEFT JOIN players p ON p.sleeper_id = n.player_id "
        "ORDER BY COALESCE(n.published_at, n.fetched_at) DESC LIMIT ?",
        (limit,)).fetchall()
    items = [{
        "guid": r["guid"], "player_id": r["player_id"], "name": r["name_raw"],
        "pos": r["pos"], "team": r["team"], "headline": r["headline"],
        "body": r["body"], "link": r["link"], "severity": r["severity"],
        "ailment": r["ailment"], "departure": bool(r["departure"]),
        "published_at": r["published_at"], "source": r["source"],
        "audience": audience(r["player_id"], mine, league),
    } for r in rows]
    items = corroborate(items)
    try:
        last_gap = json.loads(db.meta_get(conn, "wire_last_gap") or "null")
    except ValueError:
        last_gap = None
    try:
        health = json.loads(db.meta_get(conn, "wire_sources") or "{}")
    except ValueError:
        health = {}
    live = sorted(k for k, v in health.items() if v.get("ok"))
    down = sorted(k for k, v in health.items() if not v.get("ok"))
    return {
        "items": items,
        "last_ok": db.meta_get(conn, "wire_last_ok"),
        "missed_total": int(db.meta_get(conn, "wire_gap_total") or 0),
        "last_gap": last_gap,
        # Named, not counted. "4 of 5 feeds" tells you something is wrong;
        # "CBS is down" tells you what, and this house prefers the second.
        "sources": health,
        "live": live,
        "down": down,
        "source": ", ".join(s.upper() for s in live) or "RotoWire",
        "in_season": db.meta_get(conn, "wire_in_season") != "0",
    }


# ---------------------------------------------------------------------------
# One story, five newsrooms
# ---------------------------------------------------------------------------
# Five feeds carry roughly 146 items a poll and they cover the same league, so
# the same news arrives repeatedly — "Mike Evans expects to play Week 1" from
# Yahoo, CBS and ESPN is one fact, not three. A feed that prints it three times
# is worse than one that prints it once: it buries the other two stories that
# scrolled off, and it reads as three separate developments.
#
# Corroboration rather than deletion. The duplicates are EVIDENCE — three desks
# reporting the same thing is a firmer fact than one — so they collapse into a
# single row that names who else has it.

def corroborate(items: list[dict]) -> list[dict]:
    """Collapse the same story from several desks into one row.

    Two items are the same story when they are about the same MATCHED player
    and carry the same grade on the same day. Unmatched items are never merged:
    without a player id the only thing they share is prose, and merging on
    prose alone would silently hide genuinely different news.
    """
    out: list[dict] = []
    seen: dict[tuple, dict] = {}
    for it in items:
        pid = it.get("player_id")
        if not pid:
            out.append(it)
            continue
        day = (it.get("published_at") or "")[:10]
        key = (pid, it["severity"], day)
        first = seen.get(key)
        if first is None:
            it["also"] = []
            seen[key] = it
            out.append(it)
            continue
        src = it.get("source")
        if src and src != first.get("source") and src not in first["also"]:
            first["also"].append(src)
    return out


def for_players(conn: sqlite3.Connection, player_ids: list[str],
                per_player: int = 1, max_age_days: int = 10) -> dict[str, dict]:
    """The freshest wire item per player, for hanging on lineup and waiver
    rows. Only non-info grades — a chip that says 'participated in practice'
    is clutter on a row that already shows a projection."""
    if not player_ids:
        return {}
    marks = ",".join("?" * len(player_ids))
    rows = conn.execute(
        f"SELECT * FROM news WHERE player_id IN ({marks}) AND severity <> 'info' "
        f"AND (published_at IS NULL OR published_at >= datetime('now', '-{max_age_days} days')) "
        "ORDER BY COALESCE(published_at, fetched_at) DESC",
        player_ids).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        bucket = out.setdefault(r["player_id"], {"items": []})
        if len(bucket["items"]) >= per_player:
            continue
        bucket["items"].append({
            "headline": r["headline"], "severity": r["severity"],
            "ailment": r["ailment"], "published_at": r["published_at"],
            "body": r["body"], "link": r["link"],
        })
    for pid, bucket in out.items():
        top = bucket["items"][0]
        bucket["severity"] = top["severity"]
        bucket["headline"] = top["headline"]
        bucket["ailment"] = top["ailment"]
        bucket["published_at"] = top["published_at"]
    return out
