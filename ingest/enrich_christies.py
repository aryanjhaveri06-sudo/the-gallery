"""Provenance, exhibition history and literature for Christie's lots.

The lotsearch API that results come from carries the catalogue entry but not
the sections beneath it. Those sit on each lot's own page as an accordion —
Provenance, Exhibited, Literature, and a Lot Essay — and the page is public.
Condition reports are behind their login and are not attempted.

One request per lot, so this chips away nightly: `provenance IS NULL` means
never visited; a page with no provenance section is stored as '' so it is not
visited again. Newest sales first, since those are the ones on the desk.

Usage:  python3 ingest/enrich_christies.py [--limit 250] [--artist KEY]
"""

import argparse
import html
import re
import sys

from common import connect, get

_ITEM = re.compile(r'<div slot="header">\s*([^<]+?)\s*</div>\s*<div slot="content"[^>]*>(.*?)</div>\s*</chr-accordion-item>', re.S)


def sections(page):
    """{'Provenance': 'A · B · C', 'Exhibited': ..., 'Literature': ...}"""
    out = {}
    for head, body in _ITEM.findall(page):
        text = re.sub(r"<br\s*/?>", " · ", body)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", html.unescape(text)).strip(" ·")
        out[head.strip()] = text
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=250)
    ap.add_argument("--artist")
    args = ap.parse_args()

    con = connect()
    where = ["house = \"Christie's\"", "provenance IS NULL", "url IS NOT NULL"]
    params = []
    if args.artist:
        where.append("artist_key = ?")
        params.append(args.artist)
    sql = f"SELECT id, url FROM lot WHERE {' AND '.join(where)} ORDER BY sale_date DESC LIMIT {args.limit}"
    rows = con.execute(sql, params).fetchall()
    print(f"{len(rows)} Christie's lots to visit", flush=True)

    filled = none = failed = 0
    for i, r in enumerate(rows, 1):
        try:
            page = get(r["url"], expect_json=False, pause=1.0)
        except Exception:
            failed += 1
            continue
        s = sections(page)
        prov = s.get("Provenance", "")[:600]
        extra = " | ".join(f"{k}: {v[:400]}" for k in ("Exhibited", "Literature") if s.get(k) for v in [s[k]])
        con.execute("UPDATE lot SET provenance=?, notes=COALESCE(NULLIF(?, ''), notes) WHERE id=?",
                    (prov, extra, r["id"]))
        filled += bool(prov)
        none += not prov
        if i % 25 == 0:
            con.commit()
            print(f"  [{i:>4}/{len(rows)}] provenance {filled}, none {none}, failed {failed}", flush=True)
    con.commit()
    print(f"\nDone. {filled} with provenance, {none} without, {failed} pages failed.")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
