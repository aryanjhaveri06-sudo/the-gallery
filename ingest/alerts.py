"""Match her watch list against what is new, and remember what has been said.

A watch is one artist plus what should fire (see db/schema.sql `watch`):
  upcoming  a lot by them has appeared in a forthcoming catalogue
  results   a lot by them has just sold (or been bought in, where the house
            says so)
  min_inr   an optional floor — the high estimate, or the price, must clear it

Runs nightly after upcoming_lots.py, inside the refresh job. It reads the
watch list from the private site with its own token (never the desk key),
finds what is new since the last run, and writes data/alerts.json:

  items   everything fired in the last 14 days, newest first, each with the
          moment it fired — the app's Alerts screen shows this list, and the
          morning roundup sends whatever fired in the last day
  watches the list itself, so the app can show it without the API

`alert_sent` in the database is the memory: one row per (kind, lot), so a lot
that stays in a catalogue for six weeks is announced once.

Usage:  python3 ingest/alerts.py [--dry-run]
Env:    ALERT_TOKEN  (GitHub secret; matches the Pages secret of the same name)
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

from common import ROOT, connect

WATCHES_URL = "https://the-gallery-ct1.pages.dev/api/watches"
OUT = ROOT / "data" / "alerts.json"
NEW_WITHIN_H = 30           # a lot first seen inside this window is "new" (nightly + slack)
RESULT_DAYS = 3             # a sale inside this window is "just happened"
KEEP_DAYS = 14


def fetch_watches(token):
    req = urllib.request.Request(WATCHES_URL, headers={"X-Alert-Token": token, "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("watches") or []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="match and print, record nothing")
    args = ap.parse_args()

    token = os.environ.get("ALERT_TOKEN")
    if not token:
        print("::warning::ALERT_TOKEN not set — no alerts matched")
        return 0
    watches = fetch_watches(token)
    by_key = {w["artist_key"]: w for w in watches}
    print(f"{len(watches)} watches", flush=True)

    con = connect()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS alert_sent (
          key TEXT PRIMARY KEY, at TEXT NOT NULL, payload TEXT NOT NULL);
    """)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")
    fired = []

    if by_key and con.execute("SELECT name FROM sqlite_master WHERE name='upcoming_lot'").fetchone():
        since = (now - timedelta(hours=NEW_WITHIN_H)).isoformat(timespec="seconds")
        for r in con.execute("SELECT * FROM upcoming_lot WHERE artist_key IS NOT NULL AND first_seen >= ?", (since,)):
            w = by_key.get(r["artist_key"])
            if not w or not w["upcoming"]:
                continue
            if w["min_inr"] and (r["est_high_inr"] or 0) < w["min_inr"]:
                continue
            fired.append({
                "key": f"upcoming:{r['id']}", "kind": "upcoming", "artist": w["artist_name"], "artist_key": r["artist_key"],
                "house": r["house"], "sale": r["sale_title"], "date": r["sale_date"], "lot": r["lot_no"],
                "title": r["title"], "medium": r["medium"], "size": r["size"],
                "est_low": r["est_low_inr"], "est_high": r["est_high_inr"],
                "currency": r["currency"], "est_low_native": r["est_low_native"], "est_high_native": r["est_high_native"],
                "url": r["url"] or r["sale_url"], "image": r["image_url"],
            })

    if by_key:
        since = (now - timedelta(days=RESULT_DAYS)).date().isoformat()
        for r in con.execute("SELECT * FROM lot WHERE artist_key IS NOT NULL AND sale_date >= ?", (since,)):
            w = by_key.get(r["artist_key"])
            if not w or not w["results"]:
                continue
            if w["min_inr"] and (r["price_inr"] or r["est_high_inr"] or 0) < w["min_inr"]:
                continue
            fired.append({
                "key": f"result:{r['id']}", "kind": "result", "artist": w["artist_name"], "artist_key": r["artist_key"],
                "house": r["house"], "sale": None, "date": r["sale_date"], "lot": r["lot_no"],
                "title": r["title"], "medium": r["medium"], "size": r["size"],
                "est_low": r["est_low_inr"], "est_high": r["est_high_inr"],
                "price": r["price_inr"] if r["sold"] else None, "sold": bool(r["sold"]),
                "currency": r["currency"], "price_native": r["price_native"],
                "url": r["url"], "image": r["image_url"],
            })

    already = {row["key"] for row in con.execute("SELECT key FROM alert_sent")}
    new = [f for f in fired if f["key"] not in already]
    for f in new:
        f["at"] = now_iso
        print(f"  {f['kind']:8} {f['artist']:22} {f['house']:11} {f['date']}  lot {f['lot']}  {str(f['title'])[:40]}", flush=True)
    print(f"{len(fired)} matches, {len(new)} new")

    if args.dry_run:
        return 0
    con.executemany("INSERT INTO alert_sent (key, at, payload) VALUES (?,?,?)",
                    [(f["key"], now_iso, json.dumps(f, ensure_ascii=False)) for f in new])
    con.commit()

    keep_since = (now - timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
    items = [json.loads(row["payload"]) for row in con.execute(
        "SELECT payload FROM alert_sent WHERE at >= ? ORDER BY at DESC", (keep_since,))]
    OUT.write_text(json.dumps({
        "generated_at": now_iso,
        "watches": [{k: w[k] for k in ("artist_key", "artist_name", "upcoming", "results", "min_inr")} for w in watches],
        "items": items,
    }, ensure_ascii=False, separators=(",", ":")))
    print(f"-> {OUT} ({len(items)} items in the last {KEEP_DAYS} days)")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
