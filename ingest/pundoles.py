"""Ingest Pundole's auction results.

Pundole's runs on the Auction Mobility platform. Their backend API
(production2-server.auctionmobility.com) requires a client credential, so this
does not touch it — the public catalogue pages carry the same data inline as
JSON, and robots.txt allows them outright. `Crawl-delay: 10` is honoured.

Three things make this house different from the other two:

* **Unsold lots are included** (`status` is "expired" rather than "sold"), so
  Pundole's is the only source here from which sell-through can actually be
  computed. AstaGuru and Saffronart both hide the unsold set.
* **`sold_price` is the hammer, before premium** — their own UI says "Before
  buyer's premium". AstaGuru and Saffronart both publish inclusive figures, so
  comparing raw would understate Pundole's by about a sixth. Their conditions of
  sale set a flat 15% premium, so the inclusive figure is computed and the rate
  stored alongside it. GST (18% on the premium) is excluded, which matches how
  AstaGuru reports its own "inclusive of margin" price.
* **`?n=500` returns every lot in one request**, so a sale costs one fetch.

Usage:  python3 ingest/pundoles.py [--limit N] [--art-only]
"""

import argparse
import html as htmllib
import json
import re
import sys
import time
from datetime import datetime, timezone

from common import connect, get, normalise_artist, upsert_artist

BASE = "https://auctions.pundoles.com"
HOUSE = "Pundole's"
PAST = BASE + "/auctions/past?n=200"
# The canonical slug matters: any other slug 301s to it and the redirect DROPS
# the query string, silently capping the page at the default 36 lots.
CATALOG = BASE + "{path}?n=500"

CRAWL_DELAY = 10          # from their robots.txt; not negotiable
PREMIUM_PCT = 15.0        # flat, per their conditions of sale

# "JAMINI ROY (1887-1972)" / "SUBODH GUPTA (B. 1964)"
_ARTIST = re.compile(r"^(.*?)\s*\((?:b\.?\s*)?(\d{4})\s*[-–]?\s*(\d{4})?\)\s*$", re.I)
# Dimensions read "56 1/2 x 20 1/2 in. (144.2 x 52.1 cm.)". The fractional inches
# are a trap: a naive \d+ matches the "2" of "1/2" and yields "2 x 58 in". The cm
# pair is always plain decimal, so parse that and convert.
_DIM_CM = re.compile(r"\((\d+(?:\.\d+)?)\s*[x\u00d7]\s*(\d+(?:\.\d+)?)\s*cm")
_FRAC = r"\d+(?:\s+\d+/\d+)?(?:\.\d+)?"
_DIM_IN = re.compile("(%s)\\s*[x\\u00d7]\\s*(%s)\\s*in\\b" % (_FRAC, _FRAC))


def _num(t):
    """'7 7/8' -> 7.875, '24' -> 24.0"""
    t = t.strip()
    m = re.match(r"^(\d+)\s+(\d+)/(\d+)$", t)
    if m:
        return float(m.group(1)) + float(m.group(2)) / float(m.group(3))
    try:
        return float(t)
    except ValueError:
        return None


_MEDIUM_HINT = re.compile(
    r"\b(oil|acrylic|watercolour|watercolor|gouache|tempera|ink|pencil|charcoal|pastel|"
    r"mixed media|serigraph|lithograph|etching|photograph|bronze|terracotta|marble|"
    r"wood|steel|gelatin|silver|collage)\b", re.I)


def _money(v):
    try:
        n = float(v)
        return int(round(n)) if n > 0 else None
    except (TypeError, ValueError):
        return None


def split_artist(raw):
    """'JAMINI ROY (1887-1972)' -> ('Jamini Roy', '1887–1972')."""
    if not raw:
        return None, None
    s = re.sub(r"\s+", " ", htmllib.unescape(str(raw))).strip()
    m = _ARTIST.match(s)
    if not m:
        return s.title() if s.isupper() else s, None
    name = m.group(1).strip()
    name = name.title() if name.isupper() else name
    years = f"{m.group(2)}–{m.group(3)}" if m.group(3) else f"b. {m.group(2)}"
    return name, years


def parse_description(desc):
    """Pull medium and inch dimensions out of the catalogue blurb."""
    if not desc:
        return None, None
    text = re.sub(r"<br\s*/?>", "\n", htmllib.unescape(desc))
    text = re.sub(r"<[^>]+>", " ", text)
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.split("\n")]
    lines = [l for l in lines if l]

    medium = next((l for l in lines if _MEDIUM_HINT.search(l) and len(l) < 90), None)
    size = None
    cm = _DIM_CM.search(text)
    if cm:
        a, b = float(cm.group(1)) / 2.54, float(cm.group(2)) / 2.54
        size = "%.1f x %.1f in" % (a, b)
    else:
        d = _DIM_IN.search(text)
        if d:
            a, b = _num(d.group(1)), _num(d.group(2))
            if a and b:
                size = "%.1f x %.1f in" % (a, b)
    return medium, size


def extract_inline(page, key):
    """Lift one inline JSON object (`"key":{...}`) out of the server-rendered page."""
    i = page.find(f'"{key}":{{"result_page"')
    if i < 0:
        return []
    start = page.index("{", i + len(key) + 3)
    depth = 0
    for j in range(start, len(page)):
        if page[j] == "{":
            depth += 1
        elif page[j] == "}":
            depth -= 1
            if depth == 0:
                break
    else:
        return []
    try:
        return json.loads(page[start:j + 1].replace("\\/", "/")).get("result_page", [])
    except json.JSONDecodeError:
        return []


def fetch_sales():
    page = get(PAST, expect_json=False, pause=CRAWL_DELAY)
    out = []
    for a in extract_inline(page, "auctions"):
        rid = a.get("row_id")
        if not rid:
            continue
        out.append({
            "id": rid,
            "path": a.get("_detail_url") or f"/auctions/{rid}/catalog",
            "title": a.get("title"),
            "start": (a.get("time_start") or "")[:10] or None,
            "lot_count": a.get("lot_count"),
            "sold_count": a.get("sold_lot_count"),
        })
    return out


def ingest_sale(con, sale, art_only=True):
    page = get(CATALOG.format(path=sale["path"]), expect_json=False, pause=CRAWL_DELAY)
    lots = extract_inline(page, "lots")
    sale_id = f"pundoles:{sale['id']}"
    kept = sold_n = 0

    for lot in lots:
        name, _years = split_artist(lot.get("artist"))
        if art_only and not name:
            continue

        key, display = normalise_artist(name)
        if key:
            recs = lot.get("artist_records") or []
            upsert_artist(con, key, display, "pundoles",
                          (recs[0].get("row_id") if recs else None))

        medium, size = parse_description(lot.get("truncated_description"))
        hammer = _money(lot.get("sold_price"))
        sold = 1 if (lot.get("status") == "sold" and hammer) else 0
        # their figure is the hammer; the others publish inclusive, so lift it
        inclusive = int(round(hammer * (1 + PREMIUM_PCT / 100))) if hammer else None
        if sold:
            sold_n += 1

        con.execute("""
            INSERT OR REPLACE INTO lot (
              id, sale_id, house, lot_no, sale_date,
              artist_key, artist_raw, title, medium, size, year, category,
              est_low_inr, est_high_inr, hammer_inr, price_inr, price_usd,
              sold, bid_count, non_exportable, premium_pct,
              provenance, notes, image_url, url
            ) VALUES (?,?,?,?,?, ?,?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?,?)
        """, (
            f"{sale_id}:{lot.get('lot_number')}", sale_id, HOUSE,
            str(lot.get("lot_number") or ""), sale["start"],
            key, name, (lot.get("title") or "").strip() or None,
            medium, size, None, "Art",
            _money(lot.get("estimate_low")), _money(lot.get("estimate_high")),
            hammer, inclusive, None,
            sold, None, 0, PREMIUM_PCT,
            None, (lot.get("condition") or "").strip() or None,
            lot.get("cover_thumbnail"),
            BASE + (lot.get("_detail_url") or ""),
        ))
        kept += 1

    con.execute("""
        INSERT OR REPLACE INTO sale (id, house, name, start_date, end_date, url,
                                     lot_count, sold_count, fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (sale_id, HOUSE, sale["title"], sale["start"], sale["start"],
          CATALOG.format(path=sale["path"]), kept, sold_n,
          datetime.now(timezone.utc).isoformat(timespec="seconds")))
    con.commit()
    return kept, sold_n, len(lots)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all-categories", action="store_true",
                    help="keep lots with no artist (memorabilia, books)")
    args = ap.parse_args()

    con = connect()
    sales = fetch_sales()
    if args.limit:
        sales = sales[:args.limit]

    print(f"Pundole's: {len(sales)} past sales "
          f"(~{len(sales) * CRAWL_DELAY / 60:.0f} min at the required 10s crawl-delay)",
          flush=True)

    total = 0
    for i, s in enumerate(sales, 1):
        try:
            kept, sold_n, seen = ingest_sale(con, s, art_only=not args.all_categories)
        except Exception as e:
            print(f"  [{i:>3}/{len(sales)}] {s['id']:<10} FAILED {e}", flush=True)
            continue
        total += kept
        st = f"{sold_n / kept * 100:.0f}%" if kept else "n/a"
        print(f"  [{i:>3}/{len(sales)}] {s['id']:<10} {str(s['title'])[:38]:38} "
              f"{s['start']}  {kept:>4} art / {seen:>4} lots  sold {st}", flush=True)

    print(f"\nDone. {total} Pundole's lots.")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
