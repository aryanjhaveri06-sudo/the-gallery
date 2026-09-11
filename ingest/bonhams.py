"""Ingest Bonhams "Modern & Contemporary South Asian Art" results.

Bonhams' site is a Next.js app over a Typesense index, reached through
`api01.bonhams.com/search-proxy`. The API key is the search-only key the site
ships to every browser in its `runtimeConfig` — the same thing an Algolia
search key is — and the proxy answers a plain client as long as the request
looks like the browser's (Origin + the `sec-fetch-*` headers; without them
Cloudflare returns 403). robots.txt allows everything but `/ldc/` and images.

* **Sales**: collection `auctions-search`, filtered on the department name.
  61 sales from 2006 on 2026-09-11, including the Dubai and "Middle Eastern &
  South Asian" years — every lot in those carries the IND department tag, so a
  handful of Arab and Iranian painters come along. They are real results in a
  real sale and are left in; nothing on the desk tracks them.
* **Lots**: collection `lots-search`, filtered on `auctionId`, `per_page=250`
  gets a whole sale in one request. `price.hammerPrice` is the HAMMER and
  `price.hammerPremium` the inclusive figure (both 0 for a bought-in lot,
  `status` "BI"); estimates are in the sale currency. `catalogDesc` carries the
  medium and dimensions, `styledDescription` the artist / dates / title as
  three lines.

Usage:  python3 ingest/bonhams.py [--limit N]
"""

import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

from common import (UA, connect, normalise_artist, upsert_artist, split_artist,
                    parse_description, year_from, fx_rate, to_inr)

HOUSE = "Bonhams"
BASE = "https://www.bonhams.com"
SEARCH = "https://api01.bonhams.com/search-proxy/multi_search?use_cache=true&enable_lazy_filter=true"
SEARCH_KEY = "7YZqOyG0twgst4ACc2VuCyZxpGAYzM0weFTLCC20FQY"   # public, from the page's runtimeConfig
DEPARTMENT = "Modern & Contemporary South Asian Art"
PAUSE = 1.0

_LINE = re.compile(r'<div class="(\w+)">(.*?)</div>', re.S)


def search(collection, filter_by, sort_by, query_by, per_page=250, page=1):
    body = json.dumps({"searches": [{
        "collection": collection, "q": "*", "query_by": query_by,
        "filter_by": filter_by, "sort_by": sort_by, "per_page": per_page, "page": page}]})
    req = urllib.request.Request(SEARCH, data=body.encode(), method="POST", headers={
        "User-Agent": UA, "content-type": "text/plain", "x-typesense-api-key": SEARCH_KEY,
        "Origin": BASE, "Referer": BASE + "/", "Accept": "*/*",
        "sec-fetch-site": "same-site", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty"})
    with urllib.request.urlopen(req, timeout=45) as r:
        d = json.loads(r.read())
    time.sleep(PAUSE)
    res = d["results"][0]
    if "error" in res:
        raise RuntimeError(res["error"])
    return res


def fetch_sales():
    """Every closed sale in the department, oldest first."""
    out, page = [], 1
    while True:
        res = search("auctions-search", f"departments.name:=[`{DEPARTMENT}`]",
                     "dates.start.timestamp:asc", "auctionTitle", page=page)
        for h in res["hits"]:
            a = h["document"]
            if a.get("flags", {}).get("isExhibition") or not a.get("flags", {}).get("isAuctionEnded"):
                continue
            out.append({
                "id": a["id"], "title": a["auctionTitle"],
                "start": a["dates"]["start"]["datetime"][:10],
                "end": (a["dates"].get("end") or a["dates"]["start"])["datetime"][:10],
                "venue": (a.get("venue") or {}).get("name") if isinstance(a.get("venue"), dict) else a.get("venue"),
                "currency": a["currency"]["iso_code"],
                "url": f"{BASE}/auction/{a['id']}/{a.get('slug') or ''}",
            })
        if len(res["hits"]) < 250:
            return out
        page += 1


def split_styled(styled):
    """'<div class="firstLine">Nandalal Bose</div><div class="secondLine">(1882-1966)</div>
    <div class="otherLine"><i>Untitled</i></div>' -> (artist_raw, title)."""
    parts = {}
    for cls, text in _LINE.findall(styled or ""):
        text = re.sub(r"<[^>]+>", "", text).strip()
        parts.setdefault(cls, []).append(text)
    first = " ".join(parts.get("firstLine", [])[:1])
    second = " ".join(parts.get("secondLine", [])[:1])
    artist = f"{first} {second}".strip() if second.startswith("(") else first
    title = ", ".join(parts.get("otherLine", [])) or None
    return artist, title


def _amt(v):
    try:
        n = float(v)
        return int(round(n)) if n > 0 else None
    except (TypeError, ValueError):
        return None


def ingest_sale(con, sale):
    res = search("lots-search", f"auctionId:={sale['id']}", "lotNo.number:asc", "title")
    # A mixed "Discovery Sale" carries the department at sale level and clocks
    # at lot level; the lot's own tag is the one that counts.
    lots = [h["document"] for h in res["hits"]
            if (h["document"].get("department") or {}).get("code") == "IND"]
    sale_id = f"bonhams:{sale['id']}"
    day, ccy = sale["start"], sale["currency"]
    rate_inr, rate_usd = fx_rate(con, day, ccy)

    kept = sold_n = 0
    for l in lots:
        artist_raw, title = split_styled(l.get("styledDescription"))
        name, _years = split_artist(artist_raw)
        key, display = normalise_artist(name)
        if key:
            upsert_artist(con, key, display, "bonhams")

        desc = l.get("catalogDesc") or ""
        medium, size = parse_description(desc)
        pr = l.get("price") or {}
        lo, hi = _amt(pr.get("estimateLow")), _amt(pr.get("estimateHigh"))
        hammer, price = _amt(pr.get("hammerPrice")), _amt(pr.get("hammerPremium"))
        sold = 1 if (l.get("status") == "SOLD" and price) else 0
        sold_n += sold
        prem = round((price / hammer - 1) * 100, 1) if (sold and hammer and price >= hammer) else None
        lot_no = (l.get("lotNo") or {}).get("full") or str(l.get("lotId") or "")

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
            f"{sale_id}:{lot_no}", sale_id, HOUSE, lot_no, day,
            key, name, title,
            medium, size, year_from(desc), "Art",
            to_inr(lo, rate_inr), to_inr(hi, rate_inr),
            to_inr(hammer, rate_inr) if sold else None, to_inr(price, rate_inr) if sold else None,
            (to_inr(price, rate_usd) if ccy != "USD" else price) if sold else None,
            ccy, lo, hi, price if sold else None,
            sold, None, 0, prem,
            None, None, (l.get("image") or {}).get("url"),
            f"{BASE}/auction/{sale['id']}/lot/{lot_no}/{l.get('slug') or ''}/",
        ))
        kept += 1

    con.execute("""
        INSERT OR REPLACE INTO sale (id, house, name, start_date, end_date, url,
                                     lot_count, sold_count, fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (sale_id, HOUSE, f"{sale['title']}, {sale['venue']}" if sale.get("venue") else sale["title"],
          day, sale["end"], sale["url"], kept, sold_n,
          datetime.now(timezone.utc).isoformat(timespec="seconds")))
    con.commit()
    return kept, sold_n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="newest N sales only")
    args = ap.parse_args()

    con = connect()
    sales = fetch_sales()
    if args.limit:
        sales = sales[-args.limit:]
    print(f"Bonhams: {len(sales)} closed sales in '{DEPARTMENT}'", flush=True)

    total = 0
    for i, s in enumerate(sales, 1):
        try:
            kept, sold_n = ingest_sale(con, s)
        except Exception as e:
            print(f"  [{i:>3}/{len(sales)}] {s['id']:<6} FAILED {e}", flush=True)
            continue
        total += kept
        st = f"{sold_n / kept * 100:.0f}%" if kept else "n/a"
        print(f"  [{i:>3}/{len(sales)}] {s['id']:<6} {s['title'][:40]:40} {s['start']}  "
              f"{kept:>4} lots  sold {st}  {s['currency']}", flush=True)
    print(f"\nDone. {total} Bonhams lots.")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
