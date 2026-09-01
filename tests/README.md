# Tests

Written after a run of avoidable bugs reached the live desk. Each one exists
because of a specific failure, named below.

## `money_invariants.py`

    python3 tests/money_invariants.py

Every real price and estimate in the database, plus synthetic values at each
decade boundary, checked against invariants that each came from a shipped bug:
no leading zero, no collapse of distinct values, monotonic, no inverted band.

## `render_matrix.js`

Paste into the browser console with the app open (works on the local server or
either live site). Sweeps ~3,600 combinations of **viewport × auth state × screen
× tab × panel × calendar month/day × story key × search query × adverse data
shape**.

    eval() blocks the browser tool past its timeout. Run it DETACHED onto
    window.__mx and poll, or it looks like a hang.

One case is not a sweep but an assertion, and it is the reason the file grew a
Clients section: `S.client` shipped holding an id from the deleted sample book, so
a wide screen opened Clients on a collector that does not exist and sat on
"Opening the card…" for ever. Nothing threw — the old harness would have passed it.
The check is that a live book with `S.client` pointing at a ghost ends up on a real
card. Verified red by disabling `settleClientSelection`.

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

## `overflow_sweep.js`

Paste into the browser console, then run it again at **375 / 768 / 1280 / 1920**
after any change to type, spacing or a column. Type size has broken this layout
twice, both times by a handful of pixels nobody sees until it is on her iPad, and
neither was visible in a screenshot.

It reports three things and deliberately not more: the page or `#main` scrolling
sideways; an element outside the viewport whose ancestors do not scroll on
purpose; and a leaf whose text will not fit its box. Containers are excluded — a
container is wider than its box whenever a chip row or the lot rail bleeds to the
edge on purpose — and `overflow:hidden` is excluded, because an ellipsis is not an
overflow.

Force `isPad`/`isSplit` from `matchMedia` first, which the file does: viewport
emulation does not fire matchMedia change events, so the flags go stale and you
measure the wrong layout.

Same rule as the matrix: verified red by injecting
`.sec-head h2{white-space:nowrap;font-size:60px}` — 21 findings — and removing it
again — 0.

## Cross-language check

`inr()` in `ingest/export_app.py` and `inrShort()` in `index.html` render the same
rupee figures — one in the feed, one on a client card — and must agree. They have
disagreed twice: Python rounds half-to-even where JS rounds half-up, and
`(1.035).toFixed(2)` is `"1.03"` because the float is really 1.0349999…. Both now
round half-up on the integer rupee value. To re-check, dump `inr()` for a sample
to JSON, serve it beside the app, and compare against `inrShort()` in the console.

## `safeurl.js`

    node tests/safeurl.js

Unit-tests the URL guard in `index.html`. `esc()` stops a value breaking OUT of
an attribute; it does not stop the value BEING a script, and a `javascript:` URL
from a hand-typed `manual_events.json` or an RSS `<link>` rendered as a live link
that ran on click — measured, three of them.

The cases that matter are the ones where a browser is more permissive than it
looks: it strips TAB, LF and CR out of a URL *before* parsing the scheme, so a
scheme split across a newline still executes. The guard strips the control range
first, then requires http(s).

## `lot_value.mjs`

    node tests/lot_value.mjs

`lotValue()` is the comparator behind Today's "Top result". The feed carries a
price as the house printed it (`"₹9.98 cr"`) with no number behind it, so ranking
lots by value means reading the string back — the one thing this desk otherwise
refuses to do with money, because the two formatters have disagreed twice. Two
rules make it safe, and both are tested: it only ever **orders** (the printed
string is what reaches the page), and it fails **closed** (an unreadable price
returns null and drops out of the ranking rather than parsing to 0 and passing
for the cheapest lot in the sale).

It lifts the definitions out of `index.html` at run time rather than copying
them, so the test cannot drift from the code.

Written after "Top result" was found showing ₹3.00 cr while a ₹9.98 cr Souza sat
in the same forty lots: the tile had been sorting by percentage over estimate.

## Verifying the Market figures against the houses

Not a file — a method, written down because it found three wrong figures.

1. **Recompute every displayed number from `data/app_data.json`** and compare
   against the DOM. That is what caught `17 of 33`.
2. **Reclassify every lot** against its own estimate band. `vs_est` is null for a
   lot that sold *inside* its estimate, which is why it is the wrong denominator
   for "cleared the high estimate" — 7 of 40 landed inside the band here.
3. **Join the feed back to the house.** AstaGuru answers
   `/api/auctions/filter-lots?auctionId={id}&limit=1000&page=1`; join on the
   trailing slug of the desk's `url` (the API's `slug` is the whole path, the
   desk's URL carries only its tail), and read prices from `lot.auctionState`
   (`hammerWithMarginINR`, `currentHammerINR`, `isBoughtIn` — never
   `lotAmountWithMargin` or `outbidHammer`, which come back zeroed). All 15
   AstaGuru lots agreed on artist, title, estimate band, sold price and
   percentage.
4. **Allow for display rounding.** Prices print to the whole lakh, so a
   recomputation from the printed string can be ~2.6% off at ₹19 lakh and 3
   points off on a percentage. The stored percentages come from exact rupees and
   are the accurate ones; verify against `data/artdesk.db`, not the strings.

## Injection sweep (manual)

Paste into the console with the app open: poison every text field a person can
type — client name, title, brief, wants, holdings, log notes, follow-up reasons,
plus event and news fields — with `<img src=x onerror=...>` and `"><svg onload=...>`,
then render every screen, panel and viewport and check nothing executed. Last run
2026-08-26: 63 renders, 0 executions.
