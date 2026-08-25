"""One h2h sim, print positional counts of the Bootlegger roster."""
import sys, sqlite3, shutil
from collections import Counter
sys.path.insert(0, "/src")
import tools.h2h_mock as m
from app import brain

shutil.copy(m.SRC_DB, m.WORK_DB)
conn = sqlite3.connect(m.WORK_DB)
conn.row_factory = sqlite3.Row
world = m.load_world(conn)
players = world[0]
rp = brain.roster_positions(conn)
m.run_sim(0, 2, 11, conn, world, rp)
rows = conn.execute(
    "SELECT player_id FROM draft_picks WHERE draft_id='h2h-0' AND draft_slot=2 "
    "ORDER BY pick_no").fetchall()
poss = [players[r["player_id"]]["pos"] for r in rows]
print("BL roster positions:", poss)
print("counts:", dict(Counter(poss)))
