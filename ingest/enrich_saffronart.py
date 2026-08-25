"""Fill in medium and size for Saffronart lots.

Saffronart's results listing carries price and estimate but no medium or
dimensions, which means those lots cannot enter the per-square-inch index — so
without this pass the index silently covers AstaGuru only.

The detail is one request per lot (`PostWork.aspx?l={id}`, public, no auth), so
a full backfill of ~12k lots is hours. It is therefore scoped by default to the
artists that actually get a dossier, and it is resumable: lots already carrying
a medium are skipped, so re-running picks up where it stopped.

Usage:
  python3 ingest/enrich_saffronart.py --min-lots 12      # tracked artists only
  python3 ingest/enrich_saffronart.py --artist "s h raza"
  python3 ingest/enrich_saffronart.py --all --limit 500  # chip away at the rest
"""

import argparse
import html
import re
import sys

from common import connect, get

# "Oil on canvas<br>40 x 25 in (101.6 x 63.5 cm)" — medium then dimensions, with
# the odd lot using Height/Width/Depth instead (sculpture and antiquities).
_DIM = re.compile(r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*in\b")
_HWD = re.compile(r"Height:\s*(\d+(?:\.\d+)?)\s*in.*?Width:\s*(\d+(?:\.\d+)?)\s*in", re.S)
_MEDIUM = re.compile(
    r"\b((?:oil|acrylic|watercolour|watercolor|gouache|tempera|ink|pencil|charcoal|"
    r"pastel|mixed media|serigraph|lithograph|etching|photograph|bronze|terracotta|"
    r"marble|wood|steel|fibreglass)[^<>\n]{0,44}?)\s*(?:<br|\||$)", re.I)


def parse_detail(page):
    body = html.unescape(html.unescape(page))
    flat = re.sub(r"<[^>]+>", "\n", body)
    flat = re.sub(r"[ \t]+", " ", flat)

    medium = None
    m = _MEDIUM.search(body)
    if m:
        medium = re.sub(r"\s+", " ", m.group(1)).strip(" .,;")

    # Height/Width first: sculpture entries often also mention a mounting base,
    # and the inline "10 x 2.5 x 4.75 in" of the base would otherwise win.
    size = None
    h = _HWD.search(flat)
    if h:
        size = f"{h.group(1)} x {h.group(2)} in"
    else:
        d = _DIM.search(flat)
        if d:
            size = f"{d.group(1)} x {d.group(2)} in"

    non_export = 1 if re.search(r"NON[- ]EXPORTABLE", flat, re.I) else 0
    return medium, size, non_export


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-lots", type=int, default=12,
                    help="only artists with at least this many sold lots")
    ap.add_argument("--artist", help="a single canonical artist key")
    ap.add_argument("--keys", help="comma-separated canonical artist keys")
    ap.add_argument("--all", action="store_true", help="every Saffronart lot")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    con = connect()

    where = ["house = 'Saffronart'",
             "(medium IS NULL OR medium = '')",
             "url LIKE '%PostWork%'"]
    params = []
    if args.artist:
        where.append("artist_key = ?")
        params.append(args.artist)
    elif args.keys:
        ks = [k.strip() for k in args.keys.split(",") if k.strip()]
        where.append("artist_key IN (%s)" % ",".join("?" * len(ks)))
        params.extend(ks)
    elif not args.all:
        where.append("""artist_key IN (
            SELECT artist_key FROM lot
            WHERE sold = 1 AND price_inr IS NOT NULL AND artist_key IS NOT NULL
            GROUP BY artist_key HAVING COUNT(*) >= ?)""")
        params.append(args.min_lots)

    sql = f"SELECT id, url, artist_raw FROM lot WHERE {' AND '.join(where)} ORDER BY sale_date DESC"
    if args.limit:
        sql += f" LIMIT {args.limit}"
    rows = con.execute(sql, params).fetchall()

    print(f"{len(rows)} Saffronart lots to enrich "
          f"(~{len(rows) * 1.2 / 60:.0f} min at one request each)", flush=True)

    filled = skipped = 0
    for i, r in enumerate(rows, 1):
        try:
            page = get(r["url"], expect_json=False, pause=0.8)
        except Exception as e:
            skipped += 1
            continue
        medium, size, non_export = parse_detail(page)
        if not (medium or size):
            skipped += 1
            continue
        con.execute("UPDATE lot SET medium=?, size=?, non_exportable=? WHERE id=?",
                    (medium, size, non_export, r["id"]))
        filled += 1
        if i % 25 == 0:
            con.commit()
            print(f"  [{i:>5}/{len(rows)}] filled {filled}, no data {skipped}", flush=True)

    con.commit()
    print(f"\nDone. {filled} enriched, {skipped} without usable detail.")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
