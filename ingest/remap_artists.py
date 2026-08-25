"""Re-resolve every lot's artist key against the current ALIASES table.

Artist keys are written at ingest time, so adding an alias does nothing to rows
already in the database. Rather than re-pulling ~19,000 lots from three houses,
this recomputes the key from the stored `artist_raw` and merges the artist rows
that collapse together.

Run it after editing ALIASES in common.py. It is idempotent.

Usage:  python3 ingest/remap_artists.py [--dry-run]
"""

import argparse
import json
import sys
from collections import defaultdict

from common import connect, normalise_artist


def fix_displays(con, dry_run=False):
    """Show the spelling the trade uses, not the fullest one on record.

    An ALIASES entry folds "Maqbool Fida Husain" onto the key "m f husain", but
    whichever house was ingested last would otherwise leave its own spelling as
    the display name. Prefer whichever observed spelling normalises to the key.
    """
    from common import _PUNCT, _SPACE

    def norm(x):
        return _SPACE.sub(" ", _PUNCT.sub("", (x or "").lower())).strip()

    changed = []
    for a in con.execute("SELECT key, display FROM artist").fetchall():
        if norm(a["display"]) == a["key"]:
            continue
        raws = [r[0] for r in con.execute(
            "SELECT DISTINCT artist_raw FROM lot WHERE artist_key=? AND artist_raw IS NOT NULL",
            (a["key"],))]
        better = next((r for r in raws if norm(r) == a["key"]), None)
        if better and better != a["display"]:
            changed.append((better, a["key"]))

    if changed:
        print(f"{len(changed)} display names revert to the trade spelling:")
        for disp, key in changed[:8]:
            print(f"  {key:28} -> {disp}")
        if not dry_run:
            con.executemany("UPDATE artist SET display=? WHERE key=?", changed)
            con.commit()
    return len(changed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = connect()
    rows = con.execute(
        "SELECT id, artist_raw, artist_key FROM lot WHERE artist_raw IS NOT NULL"
    ).fetchall()

    moves = defaultdict(int)
    updates = []
    for r in rows:
        key, _display = normalise_artist(r["artist_raw"])
        if key and key != r["artist_key"]:
            updates.append((key, r["id"]))
            moves[(r["artist_key"], key)] += 1

    fix_displays(con, args.dry_run)

    if not updates:
        print("nothing to remap — every lot already resolves to its current key")
        con.close()
        return

    print(f"{len(updates)} lots move across {len(moves)} key changes:")
    for (old, new), n in sorted(moves.items(), key=lambda kv: -kv[1])[:20]:
        print(f"  {n:>5}  {old or '(none)':32} -> {new}")

    if args.dry_run:
        print("\ndry run — nothing written")
        con.close()
        return

    con.executemany("UPDATE lot SET artist_key=? WHERE id=?", updates)

    # Fold the artist rows together: keep the display that matches the surviving
    # key, and union the per-house ids so cross-checking still works.
    for old, new in {(o, n) for o, n in moves}:
        if not old:
            continue
        a = con.execute("SELECT display, house_ids FROM artist WHERE key=?", (old,)).fetchone()
        b = con.execute("SELECT display, house_ids FROM artist WHERE key=?", (new,)).fetchone()
        if not a:
            continue
        ids = {}
        for row in (a, b):
            if row and row["house_ids"]:
                ids.update(json.loads(row["house_ids"]))
        display = (b["display"] if b else None) or a["display"]
        con.execute(
            "INSERT OR REPLACE INTO artist (key, display, house_ids) VALUES (?,?,?)",
            (new, display, json.dumps(ids)))
        con.execute("DELETE FROM artist WHERE key=?", (old,))

    # Drop artist rows nothing points at any more.
    con.execute("""
        DELETE FROM artist WHERE key NOT IN
          (SELECT DISTINCT artist_key FROM lot WHERE artist_key IS NOT NULL)
    """)
    con.commit()

    n = con.execute("SELECT COUNT(*) c FROM artist").fetchone()["c"]
    print(f"\nremapped. {n} distinct artists remain.")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
