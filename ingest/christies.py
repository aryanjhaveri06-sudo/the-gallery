"""Ingest Christie's South Asian Modern + Contemporary results.

This is where the record prices in this market are set (Gaitonde, Sher-Gil,
Husain), so the desk is not credible without it. Two public surfaces, both
verified 2026-09-11 with nothing more than a browser User-Agent:

* **Sale list** — `/en/results/{year}/{month}` is server-rendered and carries a
  JSON-LD `Event` per sale. One page lists roughly six to nine months forward
  from the month asked for, so three pages a year enumerate everything.
* **Lots** — `window.chrComponents.lots` on the auction page names the sale's
  `saleid` / `salenumber` / `saleroomcode`, and
  `/api/discoverywebsite/auctionpages/lotsearch` returns every lot as JSON with
  `pagesize=200` (their own page asks for 84). Estimates and `price_realised`
  are in the sale currency; the price is INCLUSIVE of buyer's premium (their
  "Price realised"). An unsold lot has an empty `price_realised`; a withdrawn
  one is flagged and skipped.

Christie's robots.txt disallows `*/search` and `*/searchresults.aspx`. Neither
the auction page nor `lotsearch` is under those; the results calendar is not
disallowed. `Crawl-delay` is not set; a courtesy pause is kept anyway.

Which sales count: the department has been called "South Asian Modern +
Contemporary Art" since the 2000s (New York, London, and Mumbai as "The India
Sale"). "Art of the Islamic and Indian Worlds" and "Indian, Himalayan and
Southeast Asian Art" are antiquities and miniatures — not her market — and are
excluded by name.

Usage:  python3 ingest/christies.py [--limit N] [--from-year 2000]
"""

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone

from common import (connect, get, normalise_artist, upsert_artist, split_artist,
                    parse_description, year_from, fx_rate, to_inr)

HOUSE = "Christie's"
BASE = "https://www.christies.com"
RESULTS = BASE + "/en/results/{year}/{month:02d}"
LOTS = (BASE + "/api/discoverywebsite/auctionpages/lotsearch?language=en&pagesize=200"
        "&saleid={saleid}&salenumber={salenumber}&saleroomcode={saleroomcode}"
        "&page={page}&saletype=Sale")
FIRST_YEAR = 2000
PAUSE = 1.0

_WANT = re.compile(r"south asian.*(modern|contemporary)|india sale|"
                   r"(modern|contemporary).*indian", re.I)
_NOT = re.compile(r"islamic|himalayan|southeast|textile|miniature|jewel|carpet|classical", re.I)
_CCY = re.compile(r"^\s*([A-Z]{3})\s")
# "HKD 1,000 - 2,000" first, then a saleroom fallback for a sale whose every
# estimate is on request.
_ROOM_CCY = {"NYR": "USD", "CKS": "GBP", "CSK": "GBP", "MUM": "INR", "HGK": "HKD",
             "PAR": "EUR", "DUB": "USD"}


def wanted(name):
    return bool(_WANT.search(name or "")) and not _NOT.search(name or "")


def fetch_sales(from_year):
    """Every South Asian sale from the results calendar, oldest first."""
    seen = {}
    today = date.today()
    for year in range(from_year, today.year + 1):
        for month in (1, 5, 9):
            if (year, month) > (today.year, today.month):
                break
            html = get(RESULTS.format(year=year, month=month), expect_json=False, pause=PAUSE)
            for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
                try:
                    d = json.loads(m.group(1))
                except json.JSONDecodeError:
                    continue
                items = d if isinstance(d, list) else [d]
                for it in items:
                    for g in (it.get("@graph") or [it]):
                        if g.get("@type") != "Event" or not wanted(g.get("name")):
                            continue
                        sid = g["url"].rstrip("/").rsplit("-", 1)[-1]
                        seen[sid] = {"id": sid, "title": g["name"], "url": g["url"],
                                     "start": g.get("startDate"), "end": g.get("endDate"),
                                     "place": (g.get("location") or {}).get("name")}
    return sorted(seen.values(), key=lambda s: s["start"] or "")


def lot_params(sale_url):
    html = get(sale_url, expect_json=False, pause=PAUSE)
    m = re.search(r"window\.chrComponents\.lots = (\{.*?\});\s*\n", html, re.S)
    if not m:
        raise RuntimeError("no chrComponents.lots on the auction page")
    return json.loads(m.group(1))["data"]["lot_search_api_endpoint"]["parameters"]


def fetch_lots(p):
    out, page = [], 1
    while True:
        d = get(LOTS.format(saleid=p["saleid"], salenumber=p["salenumber"],
                            saleroomcode=p["saleroomcode"], page=page), pause=PAUSE)
        out.extend(d.get("lots") or [])
        if len(out) >= int(d.get("total_hits_filtered") or 0) or not d.get("lots"):
            return out
        page += 1


def _amt(v):
    try:
        n = float(v)
        return int(round(n)) if n > 0 else None
    except (TypeError, ValueError):
        return None


def ingest_sale(con, sale):
    p = lot_params(sale["url"])
    lots = fetch_lots(p)
    sale_id = f"christies:{sale['id']}"
    day = (sale["start"] or "")[:10] or None

    ccy = next((m.group(1) for l in lots
                for m in [_CCY.match(l.get("estimate_txt") or l.get("price_realised_txt") or "")]
                if m), None) or _ROOM_CCY.get(p["saleroomcode"], "USD")
    rate_inr, rate_usd = fx_rate(con, day, ccy) if day else (None, None)

    kept = sold_n = 0
    for l in lots:
        if l.get("lot_withdrawn"):
            continue
        name, _years = split_artist(l.get("title_primary_txt"))
        key, display = normalise_artist(name)
        if key:
            upsert_artist(con, key, display, "christies")

        desc = l.get("description_txt") or ""
        medium, size = parse_description(desc)
        lo, hi, price = _amt(l.get("estimate_low")), _amt(l.get("estimate_high")), _amt(l.get("price_realised"))
        sold = 1 if price else 0
        sold_n += sold
        img = (l.get("image") or {}).get("image_src") or None

        con.execute("""
            INSERT OR REPLACE INTO lot (
              id, sale_id, house, lot_no, sale_date,
              artist_key, artist_raw, title, medium, size, year, category,
              est_low_inr, est_high_inr, hammer_inr, price_inr, price_usd,
              currency, est_low_native, est_high_native, price_native,
              sold, bid_count, non_exportable, premium_pct,
              provenance, notes, image_url, url
            ) VALUES (?,?,?,?,?, ?,?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?,?)
        """, (
            f"{sale_id}:{l.get('lot_id_txt')}", sale_id, HOUSE,
            str(l.get("lot_id_txt") or ""), day,
            key, name, (l.get("title_secondary_txt") or "").strip() or None,
            medium, size, year_from(desc), "Art",
            to_inr(lo, rate_inr), to_inr(hi, rate_inr), None, to_inr(price, rate_inr),
            to_inr(price, rate_usd) if ccy != "USD" else price,
            ccy, lo, hi, price,
            sold, None, 0, None,
            None, None, img, l.get("url"),
        ))
        kept += 1

    con.execute("""
        INSERT OR REPLACE INTO sale (id, house, name, start_date, end_date, url,
                                     lot_count, sold_count, fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (sale_id, HOUSE, f"{sale['title']}, {sale['place']}" if sale.get("place") else sale["title"],
          day, (sale["end"] or "")[:10] or day, sale["url"], kept, sold_n,
          datetime.now(timezone.utc).isoformat(timespec="seconds")))
    con.commit()
    return kept, sold_n, ccy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="newest N sales only")
    ap.add_argument("--from-year", type=int, default=FIRST_YEAR)
    args = ap.parse_args()

    con = connect()
    # An incremental run only needs the current calendar page.
    sales = fetch_sales(date.today().year if args.limit else args.from_year)
    if args.limit:
        sales = sales[-args.limit:]
    print(f"Christie's: {len(sales)} South Asian sales", flush=True)

    total = 0
    for i, s in enumerate(sales, 1):
        try:
            kept, sold_n, ccy = ingest_sale(con, s)
        except Exception as e:
            print(f"  [{i:>3}/{len(sales)}] {s['id']:<8} FAILED {e}", flush=True)
            continue
        total += kept
        st = f"{sold_n / kept * 100:.0f}%" if kept else "n/a"
        print(f"  [{i:>3}/{len(sales)}] {s['id']:<8} {s['title'][:34]:34} {s['start']}  "
              f"{kept:>4} lots  sold {st}  {ccy}", flush=True)
    print(f"\nDone. {total} Christie's lots.")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
