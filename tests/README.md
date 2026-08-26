# Tests

Two harnesses, both written after a run of avoidable bugs reached the live desk.

## `money_invariants.py`

    python3 tests/money_invariants.py

Every real price and estimate in the database, plus synthetic values at each
decade boundary, checked against invariants that each came from a shipped bug:
no leading zero, no collapse of distinct values, monotonic, no inverted band.

## `render_matrix.js`

Paste into the browser console with the app open (works on the local server or
either live site). Sweeps ~1,200 combinations of **viewport × auth state × screen
× panel × calendar month/day × adverse data shape**, in about six seconds.

That it *finishes* is part of the test. One case feeds the calendar a sale ending
in the year 9999; before the day-spanning loop was bounded, that spun ~2.9 million
iterations and hung the tab rather than failing.

`node --check` proves only that the file parses. Every runtime bug this desk has
shipped lived on one of those axes.

**Read the comment at the top before trusting a green run.** `render()` wraps each
section in `guard()`, so a failure surfaces as a `console.error` and leaves stale
content in `#main`. A harness that only try/catches reports everything green —
the first version of this one did, twice, while fifteen real failures were
sitting underneath. It listens on `console.error` and clears `#main` first.

If it never goes red, distrust the harness before you trust the code.

## Cross-language check

`inr()` in `ingest/export_app.py` and `inrShort()` in `index.html` render the same
rupee figures — one in the feed, one on a client card — and must agree. They have
disagreed twice: Python rounds half-to-even where JS rounds half-up, and
`(1.035).toFixed(2)` is `"1.03"` because the float is really 1.0349999…. Both now
round half-up on the integer rupee value. To re-check, dump `inr()` for a sample
to JSON, serve it beside the app, and compare against `inrShort()` in the console.
