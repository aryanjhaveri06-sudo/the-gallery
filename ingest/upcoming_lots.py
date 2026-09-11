"""Every lot in every forthcoming catalogue the houses will show us.

This is what "a Gaitonde is coming up" is answered from. upcoming.py knows
the SALES; this pulls the LOTS inside them, nightly, into `upcoming_lot`, so
an artist's page can say "Christie's, 16 Sep, lot 502, est ₹1.6–2.2 cr" and a
watch can fire on it. What each house allows (verified 2026-09-11):

    Christie's   the same lotsearch API as results, live weeks before the sale
    Bonhams      the same lots-search collection, once a sale is scheduled
    Pundole's    the catalogue page's inline JSON, ?n=500, weeks before
    AstaGuru     /api/auctions/filter-lots, but only once the catalogue is
                 published — a sale in "Announcement" answers with no lots
    Saffronart   NOTHING: every forthcoming catalogue sits behind their login.
                 The sale itself is still in the diary; a watch on Saffronart
                 can only say "there is a sale", never "with two Razas".

Rows are keyed like results (`house:sale:lot`) so a lot that later sells can
be recognised as the one that was announced. `first_seen` is kept across
runs — it is what "new since last night" means to the alerts — and a row that
has left the catalogue (withdrawn, or the sale is over) is deleted.

Usage:  python3 ingest/upcoming_lots.py
"""

import json
import re
import sys
from datetime import date, datetime, timezone

from common import (connect, get, normalise_artist, split_artist, parse_description,
                    year_from, fx_rate, to_inr)
import bonhams
import christies
import pundoles

SCHEMA = """
CREATE TABLE IF NOT EXISTS upcoming_lot (
  id            TEXT PRIMARY KEY,   -- 'christies:31137:502'
  sale_id       TEXT NOT NULL,
  house         TEXT NOT NULL,
  sale_title    TEXT,
  sale_date     TEXT,
  sale_url      TEXT,
  lot_no        TEXT,
  artist_key    TEXT,
  artist_raw    TEXT,
  title         TEXT,
  medium        TEXT,
  size          TEXT,
  year          TEXT,
  est_low_inr   INTEGER,
  est_high_inr  INTEGER,
  currency      TEXT,
  est_low_native  INTEGER,
  est_high_native INTEGER,
  url           TEXT,
  image_url     TEXT,
  first_seen    TEXT NOT NULL,
  seen_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_upcoming_artist ON upcoming_lot(artist_key);
"""

_AG_LOTS = "https://www.astaguru.com/api/auctions/filter-lots?auctionId={id}&limit=1000&page=1"
_AG_UP = "https://www.astaguru.com/api/auctions/get-auctions-by-status?auctionType=UPCOMING&sortOrder=asc&limit=50"


def _amt(v):
    try:
        n = float(v)
        return int(round(n)) if n > 0 else None
    except (TypeError, ValueError):
        return None


def put(con, run_at, row):
    """row: dict with the upcoming_lot columns minus first_seen/seen_at."""
    cols = list(row) + ["first_seen", "seen_at"]
    vals = list(row.values()) + [run_at, run_at]
    con.execute(f"""
        INSERT INTO upcoming_lot ({",".join(cols)}) VALUES ({",".join("?" * len(cols))})
        ON CONFLICT(id) DO UPDATE SET {",".join(f"{c}=excluded.{c}" for c in row if c != "id")},
          seen_at=excluded.seen_at
    """, vals)


def christies_lots(con, run_at):
    """Forthcoming South Asian sales from the calendar, then their lots."""
    today = date.today()
    seen = {}
    for offset in (0, 3):
        y, m = today.year, today.month + offset
        if m > 12:
            y, m = y + 1, m - 12
        html = get(f"https://www.christies.com/en/calendar/{y}/{m:02d}", expect_json=False, pause=christies.PAUSE)
        for mm in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
            try:
                d = json.loads(mm.group(1))
            except json.JSONDecodeError:
                continue
            for it in (d if isinstance(d, list) else [d]):
                for g in (it.get("@graph") or [it]):
                    if g.get("@type") == "Event" and christies.wanted(g.get("name")) \
                            and (g.get("startDate") or "") >= today.isoformat():
                        seen[g["url"]] = g
    n = 0
    for url, g in seen.items():
        sid = url.rstrip("/").rsplit("-", 1)[-1]
        p = christies.lot_params(url)
        lots = christies.fetch_lots(p)
        day = (g.get("startDate") or "")[:10]
        ccy = next((mm.group(1) for l in lots for mm in [christies._CCY.match(l.get("estimate_txt") or "")] if mm), None) \
            or christies._ROOM_CCY.get(p["saleroomcode"], "USD")
        rate, _ = fx_rate(con, day, ccy) if day else (None, None)
        for l in lots:
            if l.get("lot_withdrawn"):
                continue
            name, _ = split_artist(l.get("title_primary_txt"))
            key, _ = normalise_artist(name)
            desc = l.get("description_txt") or ""
            medium, size = parse_description(desc)
            lo, hi = _amt(l.get("estimate_low")), _amt(l.get("estimate_high"))
            put(con, run_at, {
                "id": f"christies:{sid}:{l.get('lot_id_txt')}", "sale_id": f"christies:{sid}",
                "house": "Christie's", "sale_title": g.get("name"), "sale_date": day, "sale_url": url,
                "lot_no": str(l.get("lot_id_txt") or ""), "artist_key": key, "artist_raw": name,
                "title": (l.get("title_secondary_txt") or "").strip() or None,
                "medium": medium, "size": size, "year": year_from(desc),
                "est_low_inr": to_inr(lo, rate), "est_high_inr": to_inr(hi, rate),
                "currency": ccy, "est_low_native": lo, "est_high_native": hi,
                "url": l.get("url"), "image_url": (l.get("image") or {}).get("image_src"),
            })
            n += 1
        print(f"  Christie's {g.get('name')[:40]} {day}: {len(lots)} lots", flush=True)
    return n


def bonhams_lots(con, run_at):
    # The department query, filtered here: "&& flags.isAuctionEnded" in the
    # filter string trips their firewall (403), the plain query does not.
    res = bonhams.search("auctions-search", f"departments.name:=[`{bonhams.DEPARTMENT}`]",
                         "dates.start.timestamp:desc", "auctionTitle")
    n = 0
    for h in res["hits"]:
        a = h["document"]
        if a["flags"].get("isAuctionEnded") or a["flags"].get("isExhibition"):
            continue
        day, ccy = a["dates"]["start"]["datetime"][:10], a["currency"]["iso_code"]
        rate, _ = fx_rate(con, day, ccy)
        lr = bonhams.search("lots-search", f"auctionId:={a['id']}", "lotNo.number:asc", "title")
        lots = [x["document"] for x in lr["hits"] if (x["document"].get("department") or {}).get("code") == "IND"]
        for l in lots:
            artist_raw, title = bonhams.split_styled(l.get("styledDescription"))
            name, _ = split_artist(artist_raw)
            key, _ = normalise_artist(name)
            desc = l.get("catalogDesc") or ""
            medium, size = parse_description(desc)
            pr = l.get("price") or {}
            lo, hi = _amt(pr.get("estimateLow")), _amt(pr.get("estimateHigh"))
            lot_no = (l.get("lotNo") or {}).get("full") or str(l.get("lotId") or "")
            put(con, run_at, {
                "id": f"bonhams:{a['id']}:{lot_no}", "sale_id": f"bonhams:{a['id']}",
                "house": "Bonhams", "sale_title": a["auctionTitle"], "sale_date": day,
                "sale_url": f"{bonhams.BASE}/auction/{a['id']}/",
                "lot_no": lot_no, "artist_key": key, "artist_raw": name, "title": title,
                "medium": medium, "size": size, "year": year_from(desc),
                "est_low_inr": to_inr(lo, rate), "est_high_inr": to_inr(hi, rate),
                "currency": ccy, "est_low_native": lo, "est_high_native": hi,
                "url": f"{bonhams.BASE}/auction/{a['id']}/lot/{lot_no}/{l.get('slug') or ''}/",
                "image_url": (l.get("image") or {}).get("url"),
            })
            n += 1
        print(f"  Bonhams {a['auctionTitle'][:40]} {day}: {len(lots)} lots", flush=True)
    return n


def pundoles_lots(con, run_at):
    page = get(pundoles.BASE + "/auctions/upcoming", expect_json=False, pause=pundoles.CRAWL_DELAY)
    n = 0
    for a in pundoles.extract_inline(page, "auctions"):
        rid, path = a.get("row_id"), a.get("_detail_url")
        if not rid or not path:
            continue
        day = (a.get("time_start") or "")[:10] or None
        cat = get(pundoles.CATALOG.format(path=path), expect_json=False, pause=pundoles.CRAWL_DELAY)
        lots = pundoles.extract_inline(cat, "lots")
        for lot in lots:
            name, _ = split_artist(lot.get("artist"))
            if not name:
                continue
            key, _ = normalise_artist(name)
            medium, size = parse_description(lot.get("truncated_description"))
            put(con, run_at, {
                "id": f"pundoles:{rid}:{lot.get('lot_number')}", "sale_id": f"pundoles:{rid}",
                "house": "Pundole's", "sale_title": a.get("title"), "sale_date": day,
                "sale_url": pundoles.BASE + path,
                "lot_no": str(lot.get("lot_number") or ""), "artist_key": key, "artist_raw": name,
                "title": (lot.get("title") or "").strip() or None,
                "medium": medium, "size": size, "year": None,
                "est_low_inr": _amt(lot.get("estimate_low")), "est_high_inr": _amt(lot.get("estimate_high")),
                "currency": "INR", "est_low_native": None, "est_high_native": None,
                "url": pundoles.BASE + (lot.get("_detail_url") or ""), "image_url": lot.get("cover_thumbnail"),
            })
            n += 1
        print(f"  Pundole's {str(a.get('title'))[:40]} {day}: {len(lots)} lots", flush=True)
    return n


def _ag_row(lot):
    """The fields astaguru.py reads, for a lot that has not sold yet."""
    if (lot.get("category") or "").lower() != "art":
        return None
    key, _ = normalise_artist(lot.get("creatorValue"))
    if not key:
        return None
    slug = lot.get("slug") or ""
    media = lot.get("mediaCollection") or []
    return {
        "lot_no": str(lot.get("lotID") or ""), "artist_key": key,
        "artist_raw": (lot.get("creatorValue") or "").strip() or None,
        "title": (lot.get("title") or "").strip() or None,
        "medium": (lot.get("mediumValue") or "").strip() or None,
        "size": (lot.get("size") or "").strip() or None,
        "year": (lot.get("creationYearValue") or "").strip() or None,
        "est_low_inr": _amt(lot.get("priceMinINR")), "est_high_inr": _amt(lot.get("priceMaxINR")),
        "url": ("https://www.astaguru.com" + slug) if slug.startswith("/") else (slug or None),
        "image_url": media[0].get("url") if media else None,
    }


def astaguru_lots(con, run_at):
    payload = get(_AG_UP, pause=1.0)
    rows = payload if isinstance(payload, list) else next((v for v in payload.values() if isinstance(v, list)), [])
    n = 0
    for a in rows:
        aid = a.get("id")
        if not aid:
            continue
        d = get(_AG_LOTS.format(id=aid), pause=1.0)
        lots = d.get("lots") or []
        day = (a.get("tentativeDate") or a.get("startDateTime") or "")[:10] or None
        slug = a.get("slug") or ""
        for l in lots:
            r = _ag_row(l)
            if not r:
                continue
            put(con, run_at, {
                "id": f"astaguru:{aid}:{r['lot_no']}", "sale_id": f"astaguru:{aid}",
                "house": "AstaGuru", "sale_title": a.get("name") or a.get("title"), "sale_date": day,
                "sale_url": "https://www.astaguru.com" + slug if slug.startswith("/") else slug,
                "lot_no": r["lot_no"], "artist_key": r["artist_key"], "artist_raw": r["artist_raw"],
                "title": r.get("title"), "medium": r.get("medium"), "size": r.get("size"), "year": r.get("year"),
                "est_low_inr": r.get("est_low_inr"), "est_high_inr": r.get("est_high_inr"),
                "currency": "INR", "est_low_native": None, "est_high_native": None,
                "url": r.get("url"), "image_url": r.get("image_url"),
            })
            n += 1
        print(f"  AstaGuru {str(a.get('name'))[:40]} {day} ({a.get('status')}): {len(lots)} lots", flush=True)
    return n


def main():
    con = connect()
    con.executescript(SCHEMA)
    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total = 0
    for name, fn in (("Christie's", christies_lots), ("Bonhams", bonhams_lots),
                     ("Pundole's", pundoles_lots), ("AstaGuru", astaguru_lots)):
        try:
            total += fn(con, run_at)
            con.commit()
        except Exception as e:                    # one house down must not empty the others
            print(f"  {name} FAILED: {e}", flush=True)
    # The diary (event table) only knew the Indian houses; the overseas sales
    # arrive here first. Write them in, and stamp every diary entry with the
    # number of lots the desk can actually see.
    con.executescript("""
        CREATE TABLE IF NOT EXISTS event (id TEXT PRIMARY KEY, house TEXT NOT NULL, title TEXT,
          starts TEXT, ends TEXT, kind TEXT, city TEXT, url TEXT, lot_count INTEGER, updated_at TEXT);
    """)
    for r in con.execute("""SELECT sale_id, house, sale_title, sale_date, sale_url, COUNT(*) n
                            FROM upcoming_lot WHERE seen_at = ? GROUP BY sale_id""", (run_at,)):
        # upcoming.py runs first and knows kind and city; on conflict only the
        # lot count moves, so its richer row survives.
        con.execute("""INSERT INTO event (id, house, title, starts, ends, kind, city, url, lot_count, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET lot_count=excluded.lot_count, updated_at=excluded.updated_at""",
                    (r["sale_id"], r["house"], r["sale_title"], r["sale_date"], r["sale_date"],
                     "Live sale" if r["house"] in ("Christie's", "Bonhams", "Pundole's") else "Online sale",
                     {"Christie's": "New York", "Bonhams": "London", "Pundole's": "Mumbai", "AstaGuru": "Mumbai"}.get(r["house"]),
                     r["sale_url"], r["n"], run_at))
    # Gone from the catalogue, or the sale has happened: not forthcoming any more.
    gone = con.execute("DELETE FROM upcoming_lot WHERE seen_at < ? OR sale_date < ?",
                       (run_at, date.today().isoformat())).rowcount
    con.commit()
    kept = con.execute("SELECT COUNT(*) FROM upcoming_lot").fetchone()[0]
    print(f"\n{total} forthcoming lots seen, {kept} held, {gone} removed.")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
