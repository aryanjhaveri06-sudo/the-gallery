"""Emit a compact dataset the iPad/iPhone app can carry inline.

`desk.json` is the full derived set (~3.5 MB, 215 artists) and is what a server
would serve. The app bundle wants something much smaller, because a published
Artifact cannot fetch across origins — the data has to travel inside the page.

So this trims to the artists the desk actually shows, keeps a bounded number of
records each, and drops every field the UI never reads.

Usage:  python3 ingest/export_app.py [--artists N] [--records N]
"""

import argparse
import json
import sys

from decimal import Decimal, ROUND_HALF_UP


def _q(n, divisor, places):
    """Divide and round HALF-UP, as a string.

    Python rounds half to even and JavaScript's toFixed rounds half away from
    zero, so ₹22,50,000 printed "₹22 lakh" from the pipeline and "₹23 lakh" from
    inrShort() in the browser — the same number, two prices, depending on which
    side rendered it. Exact decimal arithmetic on the integer paise-free rupee
    value keeps both in step.
    """
    v = Decimal(n) / Decimal(divisor)
    return str(v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


from common import ROOT, connect


def inr(n):
    """The trade's own units — crore above 1 cr, lakh below.

    Lakh carries a decimal under 10, for the same reason crore does: rounding to
    whole lakh printed 1.08, 1.32 and 1.92 lakh all as "1 lakh" or "2 lakh", so
    three different results read as one price.
    """
    if not n:
        return None
    if n >= 10_000_000 or int(_q(n, 100_000, 0)) >= 100:
        # 99,99,999 is "₹1 cr", never "₹100 lakh" — nobody in the trade says that.
        v = n / 10_000_000
        return f"₹{_q(n, 10_000_000, 2 if v < 10 else 1)} cr"
    if n >= 100_000:
        v = n / 100_000
        return f"₹{_q(n, 100_000, 1 if v < 10 else 0)} lakh"
    return f"₹{n:,.0f}"


def _unit(n):
    return "cr" if n >= 10_000_000 else "lakh" if n >= 100_000 else "rupees"


def band(lo, hi):
    """An estimate band. Each end is named in its OWN unit when they straddle a
    boundary: the trade writes "₹80 lakh–1.2 cr", never "₹0.8–1.2 cr", and a
    price that starts with a zero reads like a bug.

    This used to divide by a lakh unconditionally and round to whole, so a real
    ₹30,000–50,000 estimate printed "₹0–1 lakh" and ₹20,000–40,000 printed
    "₹0–0 lakh" — an estimate of nothing to nothing.
    """
    if not (lo and hi):
        return None
    if lo > hi:
        # Two lots in 21,942 carry an inverted estimate (AstaGuru, 2021: 2,00,000
        # low against 30,000 high). It is wrong at the house, not in the parse.
        # Publishing nothing is honest; swapping would assert a range nobody set.
        return None
    if _unit(lo) != _unit(hi):
        return f"{inr(lo)}–{inr(hi)[1:]}"          # drop the second ₹
    if _unit(hi) == "cr":
        return f"₹{_q(lo, 10_000_000, 1)}–{_q(hi, 10_000_000, 1)} cr"
    if _unit(hi) == "lakh":
        places = 1 if lo < 1_000_000 else 0
        fmt = lambda v: _q(v, 100_000, places)
        return f"₹{fmt(lo)}–{fmt(hi)} lakh"
    return f"₹{lo:,.0f}–{hi:,.0f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artists", type=int, default=40)
    ap.add_argument("--records", type=int, default=24)
    ap.add_argument("--out", default=str(ROOT / "data" / "app_data.json"))
    args = ap.parse_args()

    desk = json.loads((ROOT / "data" / "desk.json").read_text())
    artists = desk["artists"]

    # Rank by market weight, not by mover: the desk is a reference tool first.
    ranked = sorted(artists.values(),
                    key=lambda a: (a["stats"]["high_inr"] or 0), reverse=True)
    keep = {a["key"] for a in ranked[:args.artists]}
    keep |= set(desk.get("trending", [])[:15])

    out_artists = {}
    for key in keep:
        a = artists.get(key)
        if not a:
            continue
        s = a["stats"]
        recs = [r for r in a["records"] if r["sold"]][:args.records]
        out_artists[key] = {
            "key": key,
            "name": a["name"],
            "stats": {
                "high": inr(s["high_inr"]),
                "high_lot": s["high_lot"],
                "median_12mo": inr(s["median_12mo_inr"]),
                "median_all": inr(s["median_all_inr"]),
                "lots_12mo": s["lots_12mo"],
                "sold_12mo": s["sold_12mo"],
                "lots_total": s["lots_recorded_total"],
                "delta_12mo": s["delta_12mo_pct"],
                "delta_basis": s["delta_basis"],
                "above_high_rate": s["above_high_est_rate"],
                "sell_through": s["sell_through"],
                "sell_through_basis": s["sell_through_basis"],
                "non_exportable": s["non_exportable_count"],
                "index_houses": s.get("index_houses") or [],
            },
            "index": s["index"],
            # The picture goes with the record. Roughly half of every house's
            # catalogue is genuinely untitled, so an auction record without
            # images is a column of the word "Untitled" — the picture is the
            # only thing that tells one work from another. build_desk.py has
            # always carried it; this used to drop it on the way out.
            # Costs ~110 KB raw across 48 artists, ~21 KB gzipped: these URLs
            # share long prefixes and compress about five to one.
            "records": [{
                "date": r["date"], "house": r["house"], "lot": r["lot"],
                "title": r["title"], "medium": r["medium"], "size": r["size"],
                "est": band(r["est_low"], r["est_high"]),
                "price": inr(r["price"]),
                "above": r["above_high"],
                "nat": r["non_exportable"],
                "url": r["url"],
                "image": r.get("image"),
            } for r in recs],
        }

    def vs_estimate(price, lo, hi):
        """How far the hammer landed from the published estimate.

        Measured against the nearer end of the band: above the high, or below
        the low. Inside the band is not a result worth a number, so it returns
        None and the card says nothing rather than inventing precision.
        """
        if not price or not (lo and hi) or lo > hi:
            return None
        if price > hi:
            return {"dir": "above", "pct": round((price - hi) / hi * 100)}
        if price < lo:
            return {"dir": "below", "pct": round((lo - price) / lo * 100)}
        return None

    feed = [{
        "date": f["date"], "house": f["house"], "artist": f["artist"],
        "artist_key": f["artist_key"] if f["artist_key"] in out_artists else None,
        "title": f["title"], "price": inr(f["price"]),
        "est": band(f["est_low"], f["est_high"]), "above": f["above_high"],
        "vs_est": vs_estimate(f["price"], f["est_low"], f["est_high"]),
        "image": f.get("image"), "medium": f.get("medium"), "size": f.get("size"),
        "url": f.get("url"),
    } for f in desk["feed"][:40]]

    trending = [k for k in desk.get("trending", []) if k in out_artists][:20]

    # Forthcoming sales, for the Calendar tab and the "next up" strip.
    con = connect()
    try:
        events = [dict(r) for r in con.execute(
            "SELECT house, title, starts, ends, kind, city, url, lot_count "
            "FROM event ORDER BY starts")]
    except Exception:
        events = []                      # diary not built yet
    con.close()

    # Real headlines, written by publishers. See ingest/news.py — this replaced
    # four invented ones that read as though they had been reported.
    try:
        with open(ROOT / "data" / "news.json") as f:
            news = json.load(f)
    except (OSError, ValueError):
        news = {"on_market": [], "wider": []}

    app = {
        "generated_at": desk["generated_at"],
        "news": news,
        "coverage": desk["coverage"],
        "caveats": desk["caveats"],
        "fx": desk["fx"],
        "artists": out_artists,
        "trending": trending,
        "feed": feed,
        "recent_sales": desk["recent_sales"][:16],
        "events": events,
    }

    blob = json.dumps(app, ensure_ascii=False, separators=(",", ":"))
    with open(args.out, "w") as f:
        f.write(blob)
    print(f"{len(out_artists)} artists, {len(feed)} feed rows, "
          f"{len(events)} forthcoming events, "
          f"{len(news.get('on_market', []))} headlines -> {args.out}")
    print(f"{len(blob) / 1024:.0f} KB")


if __name__ == "__main__":
    sys.exit(main())
