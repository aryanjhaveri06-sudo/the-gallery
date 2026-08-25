# The Gallery — data pipeline

Real auction data behind The Gallery, for Indian modern and contemporary art.

Stdlib Python only (no pip installs, no venv). Runs on `/usr/bin/python3`.

```bash
python3 ingest/astaguru.py          # full AstaGuru history
python3 ingest/saffronart.py        # full Saffronart history
python3 ingest/enrich_saffronart.py # medium + size (resumable, ~1s per lot)
python3 ingest/build_desk.py        # derive stats -> data/desk.json
python3 ingest/export_app.py        # trim to the app subset -> data/app_data.json
python3 ingest/embed_app.py         # inject it into ../art-desk-mobile.html
```

Current state: **18,252 dated lots, 293 sales, 2000-11-24 to 2026-08-18**,
992 artists seen, 215 with enough depth to price.

Everything lands in `data/artdesk.db` (SQLite, WAL). `data/desk.json` is the
single file the iPad/iPhone app reads.

---

## What the sources actually give us

Verified 25 August 2026, not assumed.

| Source | Access | Notes |
|---|---|---|
| **AstaGuru** | Open JSON API | `/api/auctions/get-auctions-by-status` and `/api/auctions/filter-lots`. No auth, no headers, no rate limit hit. 132 past sales, 2008→2026. `robots.txt` allows it. |
| **Saffronart** | Server-rendered HTML | `AuctionResults.aspx?eid=N`. 168 sales with results, back to Nov 2000. Paginated 25/page via ASP.NET postback. |
| **Pundole's** | Open, not yet built | Auction Mobility platform at `auctions.pundoles.com`. `robots.txt` allows all, `Crawl-delay: 10`. |
| **Sotheby's** | Reachable, fragile | 200 with a browser UA, heavy bot defences. |
| **Christie's** | Blocked | Connection refused outright to automated clients. |
| **FX** | Free, keyless | `api.frankfurter.dev`, ECB-backed. |
| **Art news** | Free RSS | artnet, Artforum, Hyperallergic, The Art Newspaper. |
| **Artprice / MutualArt** | Paid, no dev API | ~$99/yr basic to $245–749/mo. Licences generally forbid caching result rows — which is why the index here is computed from public house results instead. |

### AstaGuru money fields — easy to get wrong

`lotAmountWithMargin` and `outbidHammer` come back **zeroed** and must be ignored.
The real figures are `currentHammerINR` (fall of hammer), `hammerWithMarginINR`
(what the house publishes as "Sold for"), and `isBoughtIn`.

The feed also carries things the sample data only pretended to have:
`isNonExportable` (National Art Treasure), full `provenance`, `charges`
(premium / GST / TCS / customs, so landed cost is computable), a stable
`creatorID` per artist, and multi-currency estimates.

### Saffronart parsing — the field trap

The `_ArtistName_` anchor holds the **artist**; the image `title` attribute packs
`"{work title}-{artist} - {category}"`. Reading the anchor as the title (the
obvious first guess) silently swaps artist and title on every painting. The
artist slot in the image title is *empty* for antiquities, which is how lots
with no artist are detected.

---

## Methodology, and what we refuse to publish

**Artist identity.** Houses spell the same painter several ways. Names normalise
to lowercase initials-plus-surname (`S. H. Raza` → `s h raza`), glued initials
are split (`sh raza` → `s h raza`), and full names the trade abbreviates are
seeded by hand in `ALIASES` (`sayed haider raza` → `s h raza`). Both houses also
expose stable per-artist ids, stored in `artist.house_ids` for cross-checking.

**Price index — median ₹ per square inch, within one medium class.**
Not median price. Median price measures *which works came to market*, not what
the market did: a year of large oils following a year of works on paper reads as
a several-hundred-percent "rise". That bug was live in the first build and
produced +780% for Ram Kumar. Normalising by area and holding medium constant
(canvas / paper / sculpture never mixed) removes most of it. A 12-month move is
quoted only when both windows carry at least `MIN_COMPARABLES` lots; otherwise
the figure is `null` and the desk must say *not enough comparables*.

**Quality is not controlled for.** Per-square-inch holds size and medium
constant; it cannot tell a major canvas from a studio piece of the same
dimensions. Akbar Padamsee read **+452%** on a window whose prior year was five
modest heads around ₹40 lakh and whose current year included a ₹10.8 crore
*Metascape* — correct data, real consignment shift, useless as a price signal.
Hence `MIN_COMPARABLES = 10`, a `MIN_BALANCE` check so a 9-versus-5 comparison
never qualifies, and both medians plus both sample sizes published beside every
percentage. The app repeats the caveat in words under the chart.

**Sell-through — deliberately not published.** Saffronart's results pages list
only lots that sold, so the offered set is invisible. AstaGuru's feed marks only
~1.4% bought in, which no real auction achieves. Any sell-through computed here
would read as ~100% and be false — and it is exactly the sort of figure that
gets repeated to a client. It needs the houses' own sale sheets.

---

## Where this runs

The app is for an iPad and an iPhone, and iPadOS cannot run scrapers, a database
or a background refresh. So the split is fixed:

* **Server** — runs the ingesters on a schedule, holds the SQLite database,
  serves `desk.json`.
* **Devices** — pure clients, offline cache for reading.

A device-local client book is not an option either: with two devices it would
fork into two divergent copies. The CRM therefore needs the same backend, with
real auth and encryption at rest — collector holdings, budgets and conversation
notes are sensitive personal *and* commercial data under India's DPDP Act.

## Refresh

Auction results only change when a sale closes, so a nightly run is ample; FX
and news want a few times a day. Both houses are someone else's servers — the
courtesy pause in `common.get()` is not optional.

Saffronart's *sale index* is the one part needing a browser (it loads over XHR),
so `data/saffronart_sales.json` is a cached list topped up occasionally. The
results pages themselves need no browser.
