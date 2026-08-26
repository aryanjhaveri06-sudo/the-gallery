"""Collect forthcoming sales from all three houses into the `event` table.

The past-sale ingesters only see sales that have closed, so a diary needs its own
pass. Each house exposes its calendar differently:

  AstaGuru    /api/auctions/get-auctions-by-status?auctionType=UPCOMING
  Pundole's   /auctions/upcoming — the same inline JSON as the past list. Use
              this route, not /auctions, which returns a 500 when the house has
              nothing live. Empty is normal between sales, not a failure.
  Saffronart  no forthcoming feed we can read without a browser, so future-dated
              sales arrive through saffronart.py --discover instead
  Her own     data/manual_events.json — dates from invitations and trade word
              that no feed carries. Committed, so a lost database cache does
              not lose them.

AstaGuru's `endDateTime` on an unopened sale is stale (it can predate the start),
so it is ignored and the event is written as a single day.

Usage:  python3 ingest/upcoming.py
"""

import json
import re
import sys
from datetime import datetime, timezone

from common import ROOT, connect, get

UA_PAUSE = 1.0

EVENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS event (
  id          TEXT PRIMARY KEY,
  house       TEXT NOT NULL,
  title       TEXT,
  starts      TEXT,            -- ISO date
  ends        TEXT,
  kind        TEXT,            -- Live sale / Online sale / Preview / Fair
  city        TEXT,
  url         TEXT,
  lot_count   INTEGER,
  updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_starts ON event(starts);
"""


def _day(iso):
    return (iso or "")[:10] or None


def astaguru(con):
    url = ("https://www.astaguru.com/api/auctions/get-auctions-by-status"
           "?auctionType=UPCOMING&sortOrder=asc&limit=50")
    payload = get(url, pause=UA_PAUSE)
    rows = payload if isinstance(payload, list) else next(
        (v for v in payload.values() if isinstance(v, list)), [])
    n = 0
    for a in rows:
        aid = a.get("id")
        if not aid:
            continue
        # `tentativeDate` is the sale's own advertised moment; startDateTime is
        # the bidding window opening. Prefer the former when present.
        starts = _day(a.get("tentativeDate")) or _day(a.get("startDateTime"))
        if not starts:
            continue
        slug = a.get("slug") or ""
        con.execute("""
            INSERT OR REPLACE INTO event
              (id, house, title, starts, ends, kind, city, url, lot_count, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (f"astaguru:{aid}", "AstaGuru", a.get("name") or a.get("title"),
              starts, starts, "Online sale", "Mumbai",
              "https://www.astaguru.com" + slug if slug.startswith("/") else slug,
              None, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        n += 1
    return n


def pundoles(con):
    from pundoles import extract_inline, BASE
    # /auctions 500s when nothing is live; /auctions/upcoming is the stable route.
    page = get(BASE + "/auctions/upcoming", expect_json=False, pause=10)
    n = 0
    for a in extract_inline(page, "auctions"):
        rid = a.get("row_id")
        starts = _day(a.get("time_start"))
        if not (rid and starts):
            continue
        kind = "Live sale" if a.get("auction_type") == "live" else "Online sale"
        con.execute("""
            INSERT OR REPLACE INTO event
              (id, house, title, starts, ends, kind, city, url, lot_count, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (f"pundoles:{rid}", "Pundole's", a.get("title"), starts,
              _day(a.get("effective_end_time")) or starts, kind,
              a.get("location_name") or "Mumbai",
              BASE + (a.get("_detail_url") or ""), a.get("lot_count"),
              datetime.now(timezone.utc).isoformat(timespec="seconds")))
        n += 1
    return n


def from_discovered_sales(con):
    """Saffronart's forthcoming sales arrive as future-dated rows in `sale`."""
    today = datetime.now(timezone.utc).date().isoformat()
    n = 0
    for s in con.execute(
            "SELECT id, house, name, start_date, url FROM sale "
            "WHERE start_date >= ? ORDER BY start_date", (today,)):
        # 31 December is the houses' placeholder for "date not announced"
        if s["start_date"].endswith("-12-31"):
            continue
        con.execute("""
            INSERT OR REPLACE INTO event
              (id, house, title, starts, ends, kind, city, url, lot_count, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (f"sale:{s['id']}", s["house"], s["name"], s["start_date"],
              s["start_date"], "Online sale", None, s["url"], None,
              datetime.now(timezone.utc).isoformat(timespec="seconds")))
        n += 1
    return n


def manual(con):
    """Sales she knows about that no house feed publishes.

    Kept in data/manual_events.json and committed, deliberately: the database is
    a build cache in CI and can be thrown away, so anything that lives only in a
    table is one cache miss from gone. Re-read on every run, so correcting the
    file corrects the diary.
    """
    path = ROOT / "data" / "manual_events.json"
    if not path.exists():
        return 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n = 0
    for e in payload.get("events", []):
        if not (e.get("id") and e.get("starts")):
            continue
        con.execute("""
            INSERT OR REPLACE INTO event
              (id, house, title, starts, ends, kind, city, url, lot_count, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (e["id"], e.get("house"), e.get("title"), e["starts"],
              e.get("ends") or e["starts"], e.get("kind"), e.get("city"),
              e.get("url"), e.get("lot_count"), now))
        n += 1
    return n


def main():
    con = connect()
    con.executescript(EVENT_SCHEMA)

    total = 0
    for label, fn in (("AstaGuru", astaguru),
                      ("Pundole's", pundoles),
                      ("dated sales", from_discovered_sales),
                      ("her own diary", manual)):
        try:
            n = fn(con)
            total += n
            note = " (none scheduled — normal between sales)" if n == 0 else ""
            print(f"  {label:14} {n:>3} forthcoming{note}", flush=True)
        except Exception as e:
            print(f"  {label:14} FAILED {e}", flush=True)
    con.commit()

    today = datetime.now(timezone.utc).date().isoformat()
    stale = con.execute("DELETE FROM event WHERE starts < ?", (today,)).rowcount
    con.commit()
    if stale:
        print(f"  dropped {stale} past events")

    rows = con.execute("SELECT COUNT(*) c FROM event").fetchone()["c"]
    print(f"\n{rows} forthcoming events in the diary.")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
