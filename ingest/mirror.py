"""Small copies of the Saffronart pictures the desk actually shows.

Saffronart publishes each work at 159px (soft in anything bigger than a
thumbnail) and at ~2800px (about a megabyte). Nothing between. The other
houses resize on request; Saffronart cannot, so a 480px copy is made here for
the works that appear on the desk — the records on artist pages, the strips,
the feed, the repeat chains, the forthcoming lots — and only those. Not the
archive: a 12,000-picture sweep looks like copying the archive, and a block
on that would take the nightly results feed with it (same client, same
address). This is a few thousand, at one every 1.5 seconds, capped per run.

Copies live in data/img/<md5 of the original url>.jpg, committed, and served
as static files by both sites; data/mirror.json maps original → copy and
export_app.py swaps the URL in. Nothing large is kept: fetch, shrink, discard.

Needs Pillow, so it runs in its own weekly workflow like repeats.py.

Usage:  python3 ingest/mirror.py [--limit 800] [--budget-minutes 150]
"""

import argparse
import hashlib
import io
import json
import sys
import time
import urllib.request

from PIL import Image

from common import ROOT, UA

APP = ROOT / "data" / "app_data.json"
MAP = ROOT / "data" / "mirror.json"
DIR = ROOT / "data" / "img"
EDGE = 480
PAUSE = 1.5


def wanted(app):
    """Every Saffronart picture the bundle shows, most-seen first."""
    seen = {}
    def add(u, w):
        if u and "mediacloud.saffronart.com" in u and "_aucres." in u:
            seen[u] = seen.get(u, 0) + w
    for a in app.get("artists", {}).values():
        for r in a.get("records", []):
            add(r.get("image"), 3)
        for u in a.get("upcoming", []):
            add(u.get("image"), 2)
        for c in a.get("repeats", []):
            add(c.get("image"), 2)
    for f in app.get("feed", []):
        add(f.get("image"), 3)
    return [u for u, _ in sorted(seen.items(), key=lambda x: -x[1])]


def fetch_small(url):
    full = url.replace("_aucres.", ".")
    req = urllib.request.Request(full, headers={"User-Agent": UA, "Referer": "https://www.saffronart.com/"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((EDGE, EDGE), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=82, optimize=True, progressive=True)
    return out.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=800)
    ap.add_argument("--budget-minutes", type=int, default=150)
    args = ap.parse_args()
    started = time.time()

    app = json.loads(APP.read_text())
    try:
        mapping = json.loads(MAP.read_text())
    except (OSError, ValueError):
        mapping = {}
    DIR.mkdir(parents=True, exist_ok=True)

    todo = [u for u in wanted(app) if u not in mapping][:args.limit]
    print(f"{len(mapping)} already copied, {len(todo)} to do", flush=True)

    done = failed = 0
    for i, u in enumerate(todo, 1):
        if time.time() - started > args.budget_minutes * 60:
            print("  budget reached", flush=True)
            break
        key = hashlib.md5(u.encode()).hexdigest() + ".jpg"
        try:
            (DIR / key).write_bytes(fetch_small(u))
            mapping[u] = f"data/img/{key}"
            done += 1
        except Exception as e:
            failed += 1
            print(f"  failed {u[-40:]}: {e}", flush=True)
            if failed >= 10 and done == 0:
                print("  ten failures and nothing copied — stopping rather than keep knocking", flush=True)
                break
        time.sleep(PAUSE)
        if i % 50 == 0:
            MAP.write_text(json.dumps(mapping, indent=0, sort_keys=True))
            print(f"  [{i:>4}/{len(todo)}] {done} copied, {failed} failed", flush=True)

    MAP.write_text(json.dumps(mapping, indent=0, sort_keys=True))
    total = sum(p.stat().st_size for p in DIR.glob("*.jpg")) / 1e6
    print(f"\nDone. {done} copied this run, {failed} failed; {len(mapping)} copies, {total:.0f} MB in data/img.")


if __name__ == "__main__":
    sys.exit(main())
