"""Ingest Saffronart auction results.

Unlike AstaGuru there is no JSON API, but `AuctionResults.aspx?eid=N` is plain
server-rendered HTML — no headless browser needed for the results themselves.

Two wrinkles worth knowing before touching this:

* Results are paginated 25 to a page, and paging is ASP.NET `__doPostBack` —
  there is no querystring shortcut (pageno/page/pg/pagesize were all tested and
  ignored). So we carry __VIEWSTATE / __EVENTVALIDATION forward and POST for
  each subsequent page, exactly as the browser does.
* Discovering *which* event ids exist needs a browser, because the auction index
  loads over XHR. That list is cached in `data/saffronart_sales.json`.

robots.txt is honoured: disallowed event ids are skipped, not fetched.

Usage:  python3 ingest/saffronart.py [--limit N] [--since YYYY]
"""

import argparse
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from common import ROOT, UA, connect, get, normalise_artist, upsert_artist

BASE = "https://www.saffronart.com"
HOUSE = "Saffronart"
RESULTS = BASE + "/auctions/AuctionResults.aspx?eid={eid}"
WORK = BASE + "/auctions/PostWork.aspx?l={wid}"
ROBOTS = BASE + "/robots.txt"
MAX_PAGES = 40                       # ~1000 lots; nothing here comes close

MONTHS = {m[:3].lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}

_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def text(fragment):
    if not fragment:
        return ""
    s = _TAGS.sub(" ", fragment)
    s = html.unescape(s)
    return _WS.sub(" ", s).strip()


def money(s, symbol):
    m = re.search(re.escape(symbol) + r"\s*([\d,]+)", s or "")
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", "")) or None
    except ValueError:
        return None


# --------------------------------------------------------------------------
# fetching, including ASP.NET paging
# --------------------------------------------------------------------------

def _http(url, data=None, referer=None, retries=3):
    """Saffronart times out sporadically under load; retry rather than lose a sale."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-GB,en;q=0.9",
                **({"Content-Type": "application/x-www-form-urlencoded"} if data else {}),
                **({"Referer": referer} if referer else {}),
            })
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries}: {url} ({last})")


def _hidden(name, page):
    m = (re.search(r'id="%s"[^>]*value="([^"]*)"' % name, page)
         or re.search(r'name="%s"[^>]*value="([^"]*)"' % name, page))
    return html.unescape(m.group(1)) if m else ""


def pages(eid, pause=1.0):
    """Yield every results page for one event, following postback paging."""
    url = RESULTS.format(eid=eid)
    page = _http(url)
    yield page
    time.sleep(pause)

    for n in range(2, MAX_PAGES + 1):
        targets = dict((num, tgt) for tgt, num in re.findall(
            r'__doPostBack\(&#39;([^&]+?)&#39;,&#39;&#39;\)">(\d+)</a>', page))
        tgt = targets.get(str(n))
        if not tgt:
            return
        form = urllib.parse.urlencode({
            "__EVENTTARGET": tgt,
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": _hidden("__VIEWSTATE", page),
            "__VIEWSTATEGENERATOR": _hidden("__VIEWSTATEGENERATOR", page),
            "__EVENTVALIDATION": _hidden("__EVENTVALIDATION", page),
        }).encode()
        page = _http(url, data=form, referer=url)
        yield page
        time.sleep(pause)


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def parse_title(page):
    """'Evening Sale ... -Feb-24, 2016 - Results' -> ('Evening Sale ...', '2016-02-24')."""
    m = re.search(r"<title>(.*?)</title>", page, re.S)
    if not m:
        return None, None
    t = _WS.sub(" ", html.unescape(m.group(1))).strip()
    t = re.sub(r"\s*-\s*Results\s*$", "", t)

    d = re.search(r"^(.*?)\s*-\s*([A-Za-z]{3})[a-z]*[- ](\d{1,2})(?:\s*-\s*\d{1,2})?,\s*(\d{4})$", t)
    if d and d.group(2)[:3].lower() in MONTHS:
        name = d.group(1).strip(" -|")
        iso = f"{int(d.group(4)):04d}-{MONTHS[d.group(2)[:3].lower()]:02d}-{int(d.group(3)):02d}"
        return name, iso
    y = re.search(r"(\d{4})", t)
    return t.strip(" -|"), (f"{y.group(1)}-01-01" if y else None)


def parse_lots(page):
    for b in re.split(r'<div class="blockheader">', page)[1:]:
        m = re.search(r"<label>\s*Lot\s*([\w\-]+)", b)
        lot_no = m.group(1) if m else None

        # The anchor is the ARTIST (the element is literally _ArtistName_) and
        # carries a stable Saffronart artist id. The image `title` attribute packs
        # "{work title}-{artist} - {category}" — note the artist slot is EMPTY for
        # antiquities and collectibles, which have no artist at all.
        a = re.search(r'_ArtistName_\d+"[^>]*artistid=(\d+)[^>]*>(.*?)</a>', b, re.S)
        house_artist_id = a.group(1) if a else None
        anchor = text(a.group(2)) if a else None

        artist = anchor or None
        title = anchor
        cat = None
        t = re.search(r'title="([^"]*)"', b)
        if t:
            head, _, cat = html.unescape(t.group(1)).rpartition(" - ")
            head, cat = (head or cat).strip(), (cat or "").strip() or None
            if anchor and head.endswith("-" + anchor):
                title = head[: -(len(anchor) + 1)].strip() or None
            elif head.endswith("-"):
                title, artist = head[:-1].strip() or None, None   # no artist: antiquity
            elif head and head != anchor:
                title = head
            else:
                title = None

        em = re.search(r'lblEstimates_\d+"[^>]*>(.*?)</label>', b, re.S)
        pm = re.search(r'value-price">(.*?)</div>', b, re.S)
        est, price = text(em.group(1) if em else ""), text(pm.group(1) if pm else "")

        lo = hi = None
        e2 = re.search(r"Rs\s*([\d,]+)\s*-\s*([\d,]+)", est)
        if e2:
            lo, hi = (int(e2.group(1).replace(",", "")), int(e2.group(2).replace(",", "")))

        img = re.search(r"<img src=['\"]([^'\"]+)['\"]", b)
        inr = money(price, "Rs")
        # each block carries the work id; PostWork.aspx?l={id} has medium + size
        did = re.search(r"data-id='(\d+)'", b)

        yield {
            "lot_no": lot_no, "artist": artist, "title": title, "category": cat,
            "house_artist_id": house_artist_id, "est_low": lo, "est_high": hi,
            "price_inr": inr, "price_usd": money(price, "$"),
            "sold": 1 if inr else 0, "image": img.group(1) if img else None,
            "work_id": did.group(1) if did else None,
        }


# --------------------------------------------------------------------------

def disallowed_eids():
    try:
        body = get(ROBOTS, expect_json=False, pause=0.2)
    except Exception:
        return set()
    return ({int(m) for m in re.findall(r"Disallow:.*?eid=(\d+)", body)} |
            {int(m) for m in re.findall(r"Disallow:\s*/auctions/\*-(\d+)", body)})


def ingest_event(con, eid):
    sale_id = f"saffronart:{eid}"
    name = iso = None
    kept = npages = 0

    for page in pages(eid):
        npages += 1
        if name is None:
            name, iso = parse_title(page)
        for lot in parse_lots(page):
            if not (lot["artist"] or lot["title"]):
                continue
            key, display = normalise_artist(lot["artist"])
            if key:
                upsert_artist(con, key, display, "saffronart", lot["house_artist_id"])
            con.execute("""
                INSERT OR REPLACE INTO lot (
                  id, sale_id, house, lot_no, sale_date,
                  artist_key, artist_raw, title, medium, size, year, category,
                  est_low_inr, est_high_inr, hammer_inr, price_inr, price_usd,
                  sold, bid_count, non_exportable, premium_pct,
                  provenance, notes, image_url, url
                ) VALUES (?,?,?,?,?, ?,?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?,?)
            """, (
                f"{sale_id}:{lot['lot_no']}", sale_id, HOUSE, lot["lot_no"], iso,
                key, lot["artist"], lot["title"], None, None, None, lot["category"],
                lot["est_low"], lot["est_high"], None, lot["price_inr"], lot["price_usd"],
                lot["sold"], None, 0, None,
                None, None, lot["image"],
                (WORK.format(wid=lot["work_id"]) if lot["work_id"]
                 else RESULTS.format(eid=eid)),
            ))
            kept += 1

    con.execute("""
        INSERT OR REPLACE INTO sale (id, house, name, start_date, end_date, url, lot_count, fetched_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (sale_id, HOUSE, name, iso, iso, RESULTS.format(eid=eid), kept,
          datetime.now(timezone.utc).isoformat(timespec="seconds")))
    con.commit()
    return name, iso, kept, npages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--since", type=int, default=0, help="skip sales before this year")
    ap.add_argument("--discover", type=int, default=0, metavar="N",
                    help="probe the N event ids above the highest known one and "
                         "add any that resolve to a results page")
    args = ap.parse_args()

    index = json.loads((ROOT / "data" / "saffronart_sales.json").read_text())
    eids = sorted(index["eids"], reverse=True)
    blocked = disallowed_eids()
    if blocked:
        eids = [e for e in eids if e not in blocked]
        print(f"robots.txt: skipping {len(blocked)} disallowed event ids")
    if args.limit:
        eids = eids[:args.limit]

    if args.discover:
        # The index is cached because it loads over XHR, so a new sale would
        # otherwise never appear. Event ids increment, so probe forward.
        known = set(index["eids"])
        top = max(known)
        added = []
        for eid in range(top + 1, top + 1 + args.discover):
            if eid in blocked:
                continue
            try:
                name, iso = parse_title(_http(RESULTS.format(eid=eid)))
                time.sleep(1.0)
            except Exception:
                continue
            if name and iso:
                added.append(eid)
                print(f"  discovered eid {eid}: {name} ({iso})", flush=True)
        if added:
            index["eids"] = sorted(known | set(added))
            (ROOT / "data" / "saffronart_sales.json").write_text(
                json.dumps(index, indent=1))
            eids = sorted(index["eids"], reverse=True)
            if args.limit:
                eids = eids[:args.limit]
        else:
            print("  no new event ids", flush=True)

    con = connect()
    print(f"Saffronart: {len(eids)} sales to pull", flush=True)
    total = 0
    for i, eid in enumerate(eids, 1):
        try:
            name, iso, kept, npages = ingest_event(con, eid)
        except Exception as e:
            print(f"  [{i:>3}/{len(eids)}] eid {eid:<5} FAILED {e}", flush=True)
            continue
        total += kept
        print(f"  [{i:>3}/{len(eids)}] eid {eid:<5} {str(name)[:36]:36} {iso}  "
              f"{kept:>4} lots / {npages}p", flush=True)

    print(f"\nDone. {total} Saffronart lots.")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
