"""Turn the raw lot table into the figures the desk shows, and emit desk.json.

Everything here is *derived from tracked results* rather than imported from a
vendor index. That is deliberate: Artprice and MutualArt licences generally
forbid caching or redistributing their result rows, whereas a index we compute
ourselves from public house results is ours to publish.

Definitions, so the desk and the numbers never drift apart:

  sold          a lot with a realised price and not bought in
  sell-through  Computed ONLY over houses that publish their unsold lots, which
                today means Pundole's alone — its catalogue carries "expired"
                lots alongside "sold" ones, and the counts reconcile exactly
                against the house's own lot_count / sold_lot_count. Saffronart
                lists sold lots only; AstaGuru marks ~1.4% bought in, which no
                real auction achieves. A figure spanning all three would read
                ~100% and be false, and that is exactly the sort of number that
                gets repeated to a client. So the output names its basis, and
                stays null when the sample is under MIN_ST_LOTS.
  price index   base 100 at the first qualifying year, tracking the median
                *price per square inch within one medium class*. Plain median
                price is NOT used: it measures which works came to market, not
                what the market did. A year where big oils were consigned and
                the year before was works on paper reads as a 500% "rise" on
                raw medians — mix shift, not movement. Normalising by area and
                holding medium constant removes most of that.
  12-mo delta   same measure, last 12 months vs the 12 before, and only when
                both windows clear MIN_COMPARABLES *and* are of comparable
                size (MIN_BALANCE). Otherwise it is None and the desk says
                "not enough comparables" rather than guess.

                What this still does NOT control for is quality. Per-square-inch
                holds size and medium constant; it cannot tell a major canvas
                from a studio piece of the same dimensions. Akbar Padamsee read
                +452% on a window whose prior year was five modest heads around
                ~₹40 lakh and whose current year included a ₹10.8 crore
                Metascape — correct data, real consignment shift, useless as a
                price signal. So both medians and both sample sizes are always
                published alongside the percentage, and the desk labels it
                consignment-weighted rather than quality-adjusted.

Usage:  python3 ingest/build_desk.py [--min-lots N] [--out FILE]
"""

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from common import ROOT, connect, get

MIN_YEAR_LOTS = 4        # a year needs this many comparable lots to anchor an index point
TRACKED_MIN_LOTS = 12    # an artist needs this many sold lots to get a dossier
MIN_COMPARABLES = 10     # per window, before a % move may be quoted at all
# Only Pundole's publishes its unsold lots (status "expired"), so it is the one
# house whose sell-through can be computed rather than guessed. AstaGuru and
# Saffronart show sold lots only, which is why a blanket figure would read ~100%.
SELL_THROUGH_HOUSES = {"Pundole's"}
MIN_ST_LOTS = 8          # below this the rate is noise, not a rate
MIN_BALANCE = 0.45       # smaller window must be at least this share of the larger

_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*[x\u00d7]\s*(\d+(?:\.\d+)?)\s*in")
# AstaGuru writes a bare "40 x 25" on roughly half its lots. Those are inches:
# the bare distribution (median 30.0, p10 11.5, p90 63.0) sits on top of the
# explicit-inch one (28.0 / 9.0 / 60.0); centimetres would run ~2.5x higher.
# Anchored, so a three-part "40 x 25 x 8" sculpture dimension does not match.
_BARE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[x\u00d7]\s*(\d+(?:\.\d+)?)\s*$")


def sq_inches(size):
    s = size or ""
    m = _SIZE.search(s) or _BARE.match(s)
    if not m:
        return None
    a = float(m.group(1)) * float(m.group(2))
    return a if 4 <= a <= 40000 else None      # reject typos and installation sizes


def medium_class(medium):
    """Canvas, paper and sculpture trade as different markets; never mix them."""
    m = (medium or "").lower()
    if not m:
        return None
    if any(w in m for w in ("bronze", "sculpt", "steel", "marble", "wood", "fibreglass", "terracotta", "granite")):
        return "sculpture"
    if "paper" in m:
        return "paper"
    if any(w in m for w in ("canvas", "board", "masonite", "panel")):
        return "canvas"
    return None


def ppsi(rows):
    """Median rupees per square inch, or None if nothing usable."""
    vals = [r["price_inr"] / r["_sqin"] for r in rows if r.get("_sqin")]
    return statistics.median(vals) if vals else None

FX_URL = "https://api.frankfurter.dev/v1/latest?base=INR&symbols=USD,GBP,SGD,EUR"


# --------------------------------------------------------------------------

def inr(n):
    """Format in the trade's own units: crore above 1cr, else lakh."""
    if n is None:
        return None
    if n >= 10_000_000:
        v = n / 10_000_000
        return f"₹{v:.2f} cr" if v < 10 else f"₹{v:.1f} cr"
    if n >= 100_000:
        return f"₹{n / 100_000:.0f} lakh"
    return f"₹{n:,.0f}"


def median(xs):
    return int(statistics.median(xs)) if xs else None


def pct(new, old):
    if not old or not new:
        return None
    return (new - old) / old * 100.0


# --------------------------------------------------------------------------

def fetch_fx():
    """Live INR crosses. Frankfurter is free, keyless and ECB-backed."""
    try:
        d = get(FX_URL, pause=0)
        r = d["rates"]
        return {
            "as_of": d["date"],
            "pairs": [
                {"pair": "USD / INR", "rate": round(1 / r["USD"], 2)},
                {"pair": "GBP / INR", "rate": round(1 / r["GBP"], 2)},
                {"pair": "SGD / INR", "rate": round(1 / r["SGD"], 2)},
                {"pair": "EUR / INR", "rate": round(1 / r["EUR"], 2)},
            ],
        }
    except Exception as e:
        print(f"  FX unavailable ({e}) — desk will show the last stored rates")
        return None


def artist_stats(rows, today):
    """rows: every lot for one artist, newest first (sqlite3.Row -> dict copies)."""
    rows = [dict(r) for r in rows]
    for r in rows:
        r["_sqin"] = sq_inches(r.get("size"))
        r["_class"] = medium_class(r.get("medium"))

    sold = [r for r in rows if r["sold"] and r["price_inr"]]
    offered = len(rows)

    cut12 = (today - timedelta(days=365)).isoformat()
    cut24 = (today - timedelta(days=730)).isoformat()
    last12 = [r for r in rows if r["sale_date"] and r["sale_date"] >= cut12]
    sold12 = [r for r in last12 if r["sold"] and r["price_inr"]]
    sold24 = [r for r in rows if r["sale_date"] and cut24 <= r["sale_date"] < cut12
              and r["sold"] and r["price_inr"]]

    # Work in whichever medium class this artist actually trades in most, so the
    # comparison holds one market constant instead of averaging three.
    #
    # Count over the two windows the delta actually measures, not the whole
    # history: picking the basis from all-time made the headline flip between
    # nightly runs as the Saffronart backfill added old lots — Husain read +51%
    # on canvas one night and +108% on paper the next, off the same market.
    counts = defaultdict(int)
    for r in sold12 + sold24:
        if r["_class"] and r["_sqin"]:
            counts[r["_class"]] += 1
    if not counts:                      # nothing recent: fall back to all-time
        for r in sold:
            if r["_class"] and r["_sqin"]:
                counts[r["_class"]] += 1
    basis = max(counts, key=counts.get) if counts else None
    comparable = [r for r in sold if r["_class"] == basis and r["_sqin"]] if basis else []

    # index: median price per square inch per year, rebased to the first good year
    by_year = defaultdict(list)
    for r in comparable:
        by_year[r["sale_date"][:4]].append(r["price_inr"] / r["_sqin"])

    index, base = [], None
    for y in sorted(by_year):
        if len(by_year[y]) < MIN_YEAR_LOTS:
            continue
        med = statistics.median(by_year[y])
        if base is None:
            base = med
        index.append({"year": int(y), "value": round(med / base * 100),
                      "n": len(by_year[y])})

    # 12-month move, quoted only when both windows have enough comparables
    c12 = [r for r in sold12 if r["_class"] == basis and r["_sqin"]]
    c24 = [r for r in sold24 if r["_class"] == basis and r["_sqin"]]
    now_ppsi, prior_ppsi = ppsi(c12), ppsi(c24)
    balanced = (min(len(c12), len(c24)) >= MIN_BALANCE * max(len(c12), len(c24), 1))
    deep = len(c12) >= MIN_COMPARABLES and len(c24) >= MIN_COMPARABLES
    delta_basis = {
        "n_now": len(c12), "n_prior": len(c24), "medium": basis,
        "ppsi_now": round(now_ppsi) if now_ppsi else None,
        "ppsi_prior": round(prior_ppsi) if prior_ppsi else None,
        "quality_adjusted": False,
    }
    if deep and balanced:
        delta = pct(now_ppsi, prior_ppsi)
    else:
        delta = None
        delta_basis["reason"] = ("not enough comparable lots" if not deep
                                 else "the two years are not comparable in size")

    # sell-through, over the houses that actually disclose the offered set
    st_pool = [r for r in rows if r["house"] in SELL_THROUGH_HOUSES]
    st_sold = sum(1 for r in st_pool if r["sold"])
    if len(st_pool) >= MIN_ST_LOTS:
        st_rate = round(st_sold / len(st_pool) * 100)
        st_basis = {"houses": sorted(SELL_THROUGH_HOUSES), "offered": len(st_pool),
                    "sold": st_sold}
    else:
        st_rate = None
        st_basis = {"houses": sorted(SELL_THROUGH_HOUSES), "offered": len(st_pool),
                    "reason": "too few lots at a house that publishes unsold lots"}

    top = max(sold, key=lambda r: r["price_inr"]) if sold else None

    return {
        "lots_recorded_total": offered,
        "lots_sold_total": len(sold),
        "sell_through": st_rate,
        "sell_through_basis": st_basis,
        "lots_12mo": len(last12),
        "sold_12mo": len(sold12),

        "median_12mo_inr": median([r["price_inr"] for r in sold12]),
        "median_all_inr": median([r["price_inr"] for r in sold]),
        "ppsi_12mo": round(ppsi(c12)) if c12 else None,
        "high_inr": top["price_inr"] if top else None,
        "high_lot": ({"title": top["title"], "house": top["house"],
                      "date": top["sale_date"]} if top else None),
        "delta_12mo_pct": round(delta, 1) if delta is not None else None,
        "delta_basis": delta_basis,
        "index": index,
        "index_basis": basis,
        # Saffronart results pages carry no medium or size, so those lots cannot
        # enter a per-square-inch measure. State which houses actually backed the
        # index rather than letting the desk imply it covers every house shown.
        "index_houses": sorted({r["house"] for r in comparable}),
        "above_high_est_rate": _above_rate(sold),
        "non_exportable_count": sum(1 for r in rows if r["non_exportable"]),
    }


def _above_rate(sold):
    graded = [r for r in sold if r["est_high_inr"]]
    if not graded:
        return None
    return round(sum(1 for r in graded if r["price_inr"] > r["est_high_inr"])
                 / len(graded) * 100)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-lots", type=int, default=TRACKED_MIN_LOTS)
    ap.add_argument("--out", default=str(ROOT / "data" / "desk.json"))
    args = ap.parse_args()

    con = connect()
    today = date.today()

    lots = con.execute("""
        SELECT * FROM lot
        WHERE artist_key IS NOT NULL AND sale_date IS NOT NULL
        ORDER BY sale_date DESC
    """).fetchall()
    print(f"{len(lots)} dated lots with a resolved artist")

    by_artist = defaultdict(list)
    for r in lots:
        by_artist[r["artist_key"]].append(r)

    names = {r["key"]: r["display"] for r in con.execute("SELECT key, display FROM artist")}

    artists = {}
    for key, rows in by_artist.items():
        sold_n = sum(1 for r in rows if r["sold"] and r["price_inr"])
        if sold_n < args.min_lots:
            continue
        st = artist_stats(rows, today)
        artists[key] = {
            "key": key,
            "name": names.get(key, key.title()),
            "stats": st,
            "records": [{
                "date": r["sale_date"], "house": r["house"], "sale_id": r["sale_id"],
                "lot": r["lot_no"], "title": r["title"], "medium": r["medium"],
                "size": r["size"], "year": r["year"],
                "est_low": r["est_low_inr"], "est_high": r["est_high_inr"],
                "price": r["price_inr"], "price_usd": r["price_usd"],
                "sold": bool(r["sold"]),
                "above_high": bool(r["est_high_inr"] and r["price_inr"]
                                   and r["price_inr"] > r["est_high_inr"]),
                "non_exportable": bool(r["non_exportable"]),
                "url": r["url"], "image": r["image_url"],
            } for r in rows[:60]],
        }

    # trending: biggest 12-month movers among artists with real recent volume
    trending = sorted(
        (a for a in artists.values()
         if a["stats"]["delta_12mo_pct"] is not None),
        key=lambda a: a["stats"]["delta_12mo_pct"], reverse=True)

    # The feed is the desk's market read, so it carries artworks by artists we
    # actually track. Without this the newest sale wins outright — a Gandhi
    # letters-and-books consignment filled the whole feed on first run.
    NON_ART = {"Collectibles and Furniture", "Books", "Antiquty", "Jewelry",
               "Photography", "Chemical Alterations"}
    feed_src = [r for r in lots
                if r["sold"] and r["price_inr"]
                and r["artist_key"] in artists
                and (r["category"] or "") not in NON_ART]

    feed = [{
        "date": r["sale_date"], "house": r["house"], "artist": names.get(r["artist_key"]),
        "artist_key": r["artist_key"], "title": r["title"],
        "price": r["price_inr"], "est_low": r["est_low_inr"], "est_high": r["est_high_inr"],
        "above_high": bool(r["est_high_inr"] and r["price_inr"] and r["price_inr"] > r["est_high_inr"]),
        "url": r["url"],
    } for r in feed_src][:60]

    sales = [dict(r) for r in con.execute(
        "SELECT id, house, name, start_date, lot_count, url FROM sale ORDER BY start_date DESC LIMIT 40")]

    fx = fetch_fx()

    desk = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "coverage": {
            "lots": len(lots),
            "sales": con.execute("SELECT COUNT(*) c FROM sale").fetchone()["c"],
            "artists_seen": len(by_artist),
            "artists_tracked": len(artists),
            "houses": [r["house"] for r in con.execute(
                "SELECT house, COUNT(*) n FROM lot GROUP BY house ORDER BY n DESC")],
            "from": min(r["sale_date"] for r in lots),
            "to": max(r["sale_date"] for r in lots),
        },
        "caveats": {
            "sell_through": "Computed only over houses that publish unsold lots "
                            "(Pundole's). AstaGuru and Saffronart list sold lots "
                            "only, so a figure spanning all three would read ~100% "
                            "and be false. Each artist names its own basis.",
            "index_quality": "Per-square-inch holds size and medium constant but NOT "
                             "quality. A percentage move is consignment-weighted, "
                             "not quality-adjusted — always read it with the two "
                             "medians and the two sample sizes beside it.",
            "index": "Median price per square inch within one medium class, rebased. "
                     "Derived from tracked public results, not a vendor index. "
                     "Only lots carrying a medium and size can contribute — see "
                     "each artist's index_houses. Median and high figures cover "
                     "every house; the index may cover fewer.",
            "coverage": "AstaGuru, Saffronart and Pundole's. Christie's blocks "
                        "automated access; Sotheby's not yet ingested.",
        },
        "fx": fx,
        "artists": artists,
        "trending": [a["key"] for a in trending[:25]],
        "feed": feed,
        "recent_sales": sales,
    }

    with open(args.out, "w") as f:
        json.dump(desk, f, ensure_ascii=False, separators=(",", ":"))

    print(f"tracked {len(artists)} artists (>= {args.min_lots} sold lots) of {len(by_artist)} seen")
    print(f"wrote {args.out}")

    print("\nTop movers, 12 months:")
    for a in trending[:10]:
        s = a["stats"]
        b = s["delta_basis"]
        print(f"  {a['name'][:24]:24} {s['delta_12mo_pct']:>+7.1f}%  "
              f"({b['medium']}, n={b['n_now']} v {b['n_prior']}, "
              f"\u20b9{b['ppsi_now']:,}/sqin v \u20b9{b['ppsi_prior']:,})  "
              f"median {inr(s['median_12mo_inr']) or '—':>11}  "
              f"high {inr(s['high_inr'])}")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
