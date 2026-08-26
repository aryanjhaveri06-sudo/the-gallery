"""Property test for the two money formatters. Run before shipping a price change.

    python3 tests/money_invariants.py

Checks every real price and estimate in the database, plus synthetic values at
every decade boundary, against invariants that each came from a shipped bug:

  no leading zero   "₹0–0 lakh" was a real estimate band on Saffronart's online
                    sales; "₹0.8–1.2 cr" is how the trade does NOT write 80 lakh.
  no collapse       rounding to whole lakh printed 1.08, 1.32 and 1.92 lakh all
                    as one price.
  monotonic         a larger number must never display as a smaller one.
  no inversion      a band must never read high-to-low.

The JS mirror in index.html is checked separately — see tests/README.md.
"""
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))
from export_app import inr, band                                    # noqa: E402


def magnitude(s):
    n = float(re.sub(r"[^0-9.]", "", s.replace(",", "")) or 0)
    return n * 10_000_000 if "cr" in s else n * 100_000 if "lakh" in s else n


def main():
    con = sqlite3.connect(ROOT / "data" / "artdesk.db")
    prices = sorted({r[0] for r in con.execute(
        "SELECT price_inr FROM lot WHERE price_inr IS NOT NULL")})
    bands = [(r[0], r[1]) for r in con.execute(
        "SELECT est_low_inr, est_high_inr FROM lot "
        "WHERE est_low_inr IS NOT NULL AND est_high_inr IS NOT NULL")]
    con.close()

    for e in range(0, 11):
        for m in (1, 1.5, 2, 2.5, 5, 9, 9.9, 9.99, 9.999):
            prices.append(int(10 ** e * m))
    prices = sorted(set(prices))

    fails = []

    zeros = [v for v in prices if (inr(v) or "").startswith("₹0")]
    if zeros:
        fails.append(f"inr renders a leading zero: {zeros[:3]}")

    buckets = {}
    for v in prices:
        buckets.setdefault(inr(v), []).append(v)
    collapsed = [(k, vs) for k, vs in buckets.items()
                 if len(vs) > 1 and max(vs) >= min(vs) * 1.10]
    if collapsed:
        fails.append(f"inr collapses values >=10% apart: {collapsed[:3]}")

    nonmono = [(a, b) for a, b in zip(prices, prices[1:])
               if magnitude(inr(a)) > magnitude(inr(b))]
    if nonmono:
        fails.append(f"inr is not monotonic: {nonmono[:3]}")

    bad = []
    for lo, hi in bands:
        b = band(lo, hi)
        if not b:
            continue
        if b.startswith("₹0") or "0–0" in b:
            bad.append((lo, hi, b))
            continue
        left, _, right = b.partition("–")
        unit = lambda s: "cr" if "cr" in s else "lakh" if "lakh" in s else "r"
        if unit(left) == unit(right) and magnitude(left) > magnitude(right) * 1.0001:
            bad.append((lo, hi, b))
    if bad:
        fails.append(f"band is malformed: {bad[:3]}")

    print(f"{len(prices)} prices, {len(bands)} estimate bands")
    if fails:
        for f in fails:
            print("  FAIL " + f)
        return 1
    print("  all invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
