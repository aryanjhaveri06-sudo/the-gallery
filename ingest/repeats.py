"""Find the same work sold more than once — the only honest price index.

A per-square-inch median moves with what happened to be consigned; a work that
sold in 2007 and again in 2018 measures the market with quality held constant.
That is what Artnet and Artprice sell, and what nobody does for Indian art.

The trap is the serial title. Raza painted dozens of "Bindu" at 39 x 39 in and
Tyeb Mehta more than one "Diagonal" at 69 x 59, so artist + title + size names
a *kind* of work, not a work. Only the picture settles it. Measured 2026-09-11
on the pairs that matter:

    Tyeb "Kali"    2007 -> 2018   rgb 0.91  edge 0.90   the known resale
    Raza "Jaipur"  2021 -> 2022   rgb 0.98  edge 1.00   the known resale
    Raza "Bindu"   2020 vs 2022   rgb 0.75  edge 0.47   two works, same template
    Tyeb "Diagonal" 2015 vs 2022  rgb -0.03 edge 0.28   two works

The edge channel is what separates a template from a work: two Bindus share a
composition and differ in every band. Both thresholds must pass. A pair that
fails is simply not claimed — a missed resale costs nothing, a false one puts a
wrong provenance in front of a collector.

How: candidates are lots by one artist whose dimensions agree within half an
inch (either orientation) and whose titles do not contradict — equal, or at
least one of them "Untitled"/blank. Each candidate's picture is fetched once,
trimmed to the painting (the house's border colour is sampled at the corner),
resized to 24 x 24 RGB and stored in `image_sig`; the comparison z-scores each
channel and correlates, then does the same on a FIND_EDGES pass of the
grayscale. Matching pairs are unioned into chains, written to `repeat`.

Needs Pillow, which is why this runs in its own workflow and not the nightly
stdlib refresh. A run touches only lots with no signature yet.

Usage:  python3 ingest/repeats.py [--artist KEY] [--limit N]
"""

import argparse
import hashlib
import io
import math
import re
import sys
import time
import urllib.parse
import urllib.request

from PIL import Image, ImageChops, ImageFilter, ImageOps

from common import UA, connect

N = 24                # signature edge, pixels
RGB_MIN = 0.85        # min over the three channels
EDGE_MIN = 0.65        # real resales measured 0.86–1.00; look-alike Bindus 0.47–0.49
DIM_TOL = 0.6         # inches; the houses round differently (39.25 / 39.4 / 39.5)
PAUSE = 0.8

_DIMS = re.compile(r"\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)")
_MEDIUM = {"canvas": "canvas", "board": "canvas", "paper": "paper", "card": "paper",
           "bronze": "sculpture", "fibreglass": "sculpture", "fiberglass": "sculpture",
           "wood": "sculpture", "steel": "sculpture", "marble": "sculpture"}


def dims(size):
    m = _DIMS.match(size or "")
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    return (min(a, b), max(a, b))


def title_key(t):
    t = re.sub(r"[^a-z0-9 ]", " ", (t or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    return None if (not t or t.startswith("untitled")) else t


def medium_class(m):
    m = (m or "").lower()
    for word, cls in _MEDIUM.items():
        if word in m:
            return cls
    return None


def small_url(u):
    """The smallest picture each house will give, since the signature is 24px."""
    if "_aucres." in u:
        return u.replace("_aucres.", "_thumb.")
    if u.startswith("https://www.christies.com/img/lotimages/"):
        return u + ("&" if "?" in u else "?") + "w=400"
    if u.startswith("https://images1.bonhams.com/image?"):
        return u + "&width=400"
    return u


def fetch_image(u):
    p = urllib.parse.urlsplit(u)
    u = urllib.parse.urlunsplit(p._replace(path=urllib.parse.quote(p.path)))
    req = urllib.request.Request(u, headers={"User-Agent": UA, "Referer": "https://the-gallery-ct1.pages.dev/"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    time.sleep(PAUSE)
    return Image.open(io.BytesIO(data)).convert("RGB")


def signature(im):
    """Trim to the painting, resize, return N*N*3 bytes."""
    bg = Image.new("RGB", im.size, im.getpixel((min(2, im.width - 1), min(2, im.height - 1))))
    mask = ImageChops.difference(im, bg).convert("L").point(lambda v: 255 if v > 28 else 0)
    bb = mask.getbbox()
    if bb and (bb[2] - bb[0]) > im.width * 0.3 and (bb[3] - bb[1]) > im.height * 0.3:
        im = im.crop(bb)
    im = im.resize((N, N), Image.LANCZOS)
    return im.tobytes()


def _z(v):
    m = sum(v) / len(v)
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / len(v)) or 1.0
    return [(x - m) / sd for x in v]


def _corr(a, b):
    return sum(x * y for x, y in zip(a, b)) / len(a)


def compare(sa, sb):
    """(rgb, edge) correlations between two stored signatures."""
    ia = Image.frombytes("RGB", (N, N), sa)
    ib = Image.frombytes("RGB", (N, N), sb)
    rgb = min(_corr(_z([p[c] for p in ia.getdata()]), _z([p[c] for p in ib.getdata()]))
              for c in range(3))
    ea = _z(list(ImageOps.grayscale(ia).filter(ImageFilter.FIND_EDGES).getdata()))
    eb = _z(list(ImageOps.grayscale(ib).filter(ImageFilter.FIND_EDGES).getdata()))
    return rgb, _corr(ea, eb)


def same_work(sa, sb):
    rgb, edge = compare(sa, sb)
    return rgb >= RGB_MIN and edge >= EDGE_MIN, rgb, edge


def candidate_pairs(rows):
    """Pairs of lots (same artist) whose size and title do not rule out one work."""
    out = []
    lots = [(r, dims(r["size"]), title_key(r["title"]), medium_class(r["medium"])) for r in rows]
    lots = [x for x in lots if x[1]]
    lots.sort(key=lambda x: x[1][0] * x[1][1])
    for i, (ra, da, ta, ma) in enumerate(lots):
        for rb, db, tb, mb in lots[i + 1:]:
            if db[0] * db[1] > (da[0] + DIM_TOL) * (da[1] + DIM_TOL):
                break                       # sorted by area; nothing further can fit
            if abs(da[0] - db[0]) > DIM_TOL or abs(da[1] - db[1]) > DIM_TOL:
                continue
            if ra["sale_date"] == rb["sale_date"] and ra["sale_id"] == rb["sale_id"]:
                continue                    # two lots in one sale are two works
            if ta and tb and ta != tb:
                continue
            if ma and mb and ma != mb:
                continue
            out.append((ra, rb))
    return out


def ensure_sig(con, lot, cache):
    if lot["id"] in cache:
        return cache[lot["id"]]
    row = con.execute("SELECT sig FROM image_sig WHERE lot_id=?", (lot["id"],)).fetchone()
    if row:
        cache[lot["id"]] = row["sig"]
        return row["sig"]
    if not lot["image_url"]:
        cache[lot["id"]] = None
        return None
    try:
        sig = signature(fetch_image(small_url(lot["image_url"])))
    except Exception as e:
        print(f"    image failed {lot['id']}: {e}", flush=True)
        sig = None
    con.execute("INSERT OR REPLACE INTO image_sig (lot_id, sig) VALUES (?,?)", (lot["id"], sig))
    cache[lot["id"]] = sig
    return sig


class Union:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def join(self, a, b):
        self.p[self.find(a)] = self.find(b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artist", help="one artist key, e.g. 's h raza'")
    ap.add_argument("--limit", type=int, default=0, help="stop after N new images")
    args = ap.parse_args()

    con = connect()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS image_sig (lot_id TEXT PRIMARY KEY, sig BLOB);
        CREATE TABLE IF NOT EXISTS repeat (
          lot_id   TEXT PRIMARY KEY,
          chain_id TEXT NOT NULL,      -- the earliest lot id in the chain
          rgb      REAL, edge REAL     -- the weakest link that admitted this lot
        );
        CREATE INDEX IF NOT EXISTS idx_repeat_chain ON repeat(chain_id);
    """)

    q = "SELECT id, sale_id, sale_date, artist_key, title, medium, size, image_url FROM lot WHERE artist_key IS NOT NULL AND size IS NOT NULL"
    params = ()
    if args.artist:
        q += " AND artist_key=?"
        params = (args.artist,)
    by_artist = {}
    for r in con.execute(q, params):
        by_artist.setdefault(r["artist_key"], []).append(r)

    pairs = [(k, p) for k, rows in by_artist.items() for p in candidate_pairs(rows)]
    print(f"{len(by_artist)} artists, {len(pairs)} candidate pairs", flush=True)

    had = {r["lot_id"] for r in con.execute("SELECT lot_id FROM image_sig")}
    need = {l["id"] for _, (a, b) in pairs for l in (a, b)} - had
    print(f"{len(need)} pictures to fetch ({len(had)} already signed)", flush=True)

    cache, fetched = {}, 0
    u = Union()
    scores = {}
    for i, (key, (a, b)) in enumerate(pairs):
        new = sum(1 for l in (a, b) if l["id"] in need and l["id"] not in cache)
        if args.limit and fetched + new > args.limit:
            continue
        sa, sb = ensure_sig(con, a, cache), ensure_sig(con, b, cache)
        fetched += new
        if fetched and fetched % 50 == 0 and new:
            con.commit()
            print(f"  ...{fetched} fetched", flush=True)
        if not sa or not sb:
            continue
        ok, rgb, edge = same_work(sa, sb)
        if ok:
            u.join(a["id"], b["id"])
            for x in (a["id"], b["id"]):
                if x not in scores or scores[x][0] > rgb:
                    scores[x] = (rgb, edge)
    con.commit()

    chains = {}
    for x in u.p:
        chains.setdefault(u.find(x), []).append(x)
    # canonical chain id = the earliest sale in the chain, by date then id
    date_of = {r["id"]: r["sale_date"] for rows in by_artist.values() for r in rows}
    rows_out = []
    for members in chains.values():
        if len(members) < 2:
            continue
        cid = min(members, key=lambda x: (date_of.get(x) or "", x))
        rows_out += [(m, cid, scores[m][0], scores[m][1]) for m in members]

    if not args.artist and not args.limit:
        con.execute("DELETE FROM repeat")
    else:
        con.executemany("DELETE FROM repeat WHERE lot_id=?", [(m,) for m, *_ in rows_out])
    con.executemany("INSERT OR REPLACE INTO repeat (lot_id, chain_id, rgb, edge) VALUES (?,?,?,?)", rows_out)
    con.commit()
    n_chain = len({c for _, c, *_ in rows_out})
    print(f"\n{n_chain} works sold more than once, {len(rows_out)} lots, {fetched} new pictures.")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
