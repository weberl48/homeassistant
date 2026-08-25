import sys
from pathlib import Path
sys.path.insert(0, "/src")
from app.sources import fetch_draftsharks, DS_ROWS_URL, FP_UA
import httpx, re

ck = Path("/data/.ds_cookie").read_text().strip()
print("cookie len:", len(ck))
r = httpx.get(DS_ROWS_URL, timeout=40, follow_redirects=True,
              headers={"Cookie": ck, "User-Agent": FP_UA,
                       "X-Requested-With": "XMLHttpRequest"})
print("status:", r.status_code, "| size:", len(r.text))
trs = re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S)
big = [t for t in trs if len(re.findall(r"<td", t)) >= 12]
print("tr:", len(trs), "| rows>=12 cells:", len(big))
if big:
    nm = re.search(r'first-name="([^"]*)"\s+last-name="([^"]*)"', big[0])
    print("name match:", nm.groups() if nm else None)
    pm = re.search(r">\s*(QB|RB|WR|TE|K|DST|DEF)\s*<", big[0])
    print("pos match:", pm.group(1) if pm else None)
rows = fetch_draftsharks(ck)
print("fetcher rows:", len(rows))
if rows: print(rows[0])
