"""Ingest the full AstaGuru result history.

Two public JSON endpoints carry everything, no auth and no headers required:

  /api/auctions/get-auctions-by-status?auctionType=PAST&sortOrder=desc&limit=300
  /api/auctions/filter-lots?auctionId={id}&limit=1000&page=1

The money fields are easy to misread. `lotAmountWithMargin` and `outbidHammer`
are zeroed in this response and must be ignored. The real figures are:

  currentHammerINR      fall of the hammer
  hammerWithMarginINR   what the house publishes as "Sold for" (incl. premium)
  isBoughtIn            true when the lot did not sell

Usage:  python3 ingest/astaguru.py [--limit N] [--art-only]
"""

import argparse
import json
import sys
from datetime import datetime, timezone

from common import connect, get, normalise_artist, upsert_artist

BASE = "https://www.astaguru.com"
HOUSE = "AstaGuru"

LIST_URL = BASE + "/api/auctions/get-auctions-by-status?auctionType=PAST&sortOrder=desc&limit=300"
LOTS_URL = BASE + "/api/auctions/filter-lots?auctionId={id}&limit=1000&page=1"


def _int(v):
    try:
        n = int(float(v))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def fetch_sales():
    payload = get(LIST_URL)
    rows = payload if isinstance(payload, list) else next(
        v for v in payload.values() if isinstance(v, list))
    out = []
    for a in rows:
        aid = a.get("id")
        if aid is None:
            continue
        out.append({
            "id": str(aid),
            "name": a.get("name") or a.get("title"),
            "start": (a.get("startDateTime") or "")[:10] or None,
            "end": (a.get("endDateTime") or "")[:10] or None,
            "slug": a.get("slug") or "",
        })
    return out


def ingest_sale(con, sale, art_only=True):
    payload = get(LOTS_URL.format(id=sale["id"]))
    lots = payload.get("lots") or []
    sale_id = f"astaguru:{sale['id']}"
    kept = 0

    for lot in lots:
        category = lot.get("category") or ""
        if art_only and category.lower() != "art":
            continue

        key, display = normalise_artist(lot.get("creatorValue"))
        if key:
            upsert_artist(con, key, display, "astaguru", lot.get("creatorID") or None)

        st = lot.get("auctionState") or {}
        bought_in = bool(st.get("isBoughtIn"))
        price_inr = _int(st.get("hammerWithMarginINR"))
        hammer_inr = _int(st.get("currentHammerINR"))
        sold = 0 if bought_in else (1 if price_inr else 0)

        charges = lot.get("charges") or {}
        slug = lot.get("slug") or ""
        media = lot.get("mediaCollection") or []

        con.execute("""
            INSERT OR REPLACE INTO lot (
              id, sale_id, house, lot_no, sale_date,
              artist_key, artist_raw, title, medium, size, year, category,
              est_low_inr, est_high_inr, hammer_inr, price_inr, price_usd,
              sold, bid_count, non_exportable, premium_pct,
              provenance, notes, image_url, url
            ) VALUES (?,?,?,?,?, ?,?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?,?)
        """, (
            f"{sale_id}:{lot.get('lotID')}", sale_id, HOUSE,
            str(lot.get("lotID") or ""), sale["start"],
            key, (lot.get("creatorValue") or "").strip() or None,
            (lot.get("title") or "").strip() or None,
            (lot.get("mediumValue") or "").strip() or None,
            (lot.get("size") or "").strip() or None,
            (lot.get("creationYearValue") or "").strip() or None,
            category,
            _int(lot.get("priceMinINR")), _int(lot.get("priceMaxINR")),
            hammer_inr, price_inr, _int(st.get("hammerWithMarginUSD")),
            sold, _int(st.get("totalBidCount")) or 0,
            1 if str(lot.get("isNonExportable")) == "1" else 0,
            charges.get("premiumPercentage"),
            (lot.get("provenance") or "").strip() or None,
            (lot.get("description") or "").strip() or None,
            (media[0].get("url") if media else None),
            (BASE + slug) if slug.startswith("/") else (slug or None),
        ))
        kept += 1

    con.execute("""
        INSERT OR REPLACE INTO sale (id, house, name, start_date, end_date, url, lot_count, fetched_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        sale_id, HOUSE, sale["name"], sale["start"], sale["end"],
        BASE + sale["slug"] if sale["slug"].startswith("/") else sale["slug"],
        kept, datetime.now(timezone.utc).isoformat(timespec="seconds"),
    ))
    con.commit()
    return kept, len(lots)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only the N most recent sales")
    ap.add_argument("--all-categories", action="store_true",
                    help="keep jewellery, watches, memorabilia too (default: art only)")
    args = ap.parse_args()

    con = connect()
    sales = fetch_sales()
    if args.limit:
        sales = sales[:args.limit]

    print(f"AstaGuru: {len(sales)} past sales to pull", flush=True)
    total_art = total_all = 0
    for i, s in enumerate(sales, 1):
        try:
            kept, seen = ingest_sale(con, s, art_only=not args.all_categories)
        except Exception as e:                      # one bad sale must not kill the run
            print(f"  [{i:>3}/{len(sales)}] {s['id']:>4} {str(s['name'])[:32]:32} FAILED {e}", flush=True)
            continue
        total_art += kept
        total_all += seen
        print(f"  [{i:>3}/{len(sales)}] {s['id']:>4} {str(s['name'])[:32]:32} "
              f"{s['start']}  {kept:>4} art / {seen:>4} lots", flush=True)

    artists = con.execute("SELECT COUNT(*) c FROM artist").fetchone()["c"]
    print(f"\nDone. {total_art} art lots kept of {total_all} seen; {artists} distinct artists.")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
