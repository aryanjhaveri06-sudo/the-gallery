# Deploying the The Gallery — free, self-updating

The desk has to run on an iPad and an iPhone, and iPadOS cannot run a scraper, a
database or a background job. So the work happens elsewhere and the devices are
pure clients. None of it needs a paid server.

```
GitHub Actions (nightly cron, free)
        │  runs the stdlib-Python ingesters
        ▼
   data/app_data.json  ──commit──▶  GitHub Pages (free static host)
                                          │
                                          ▼
                          iPad / iPhone — Add to Home Screen
```

`index.html` carries an embedded copy of the data and *also* tries to fetch
`data/app_data.json` at load. On Pages the fetch wins, so the nightly commit only
rewrites a ~283 KB JSON. Offline, or inside a published Artifact where the CSP
blocks cross-origin fetches, the embedded copy renders instead. The desk is never
blank and never stale by more than one refresh.

---

## What it costs

| Piece | Free tier | What we use |
|---|---|---|
| GitHub Actions | unlimited on public repos; 2,000 min/month private | ~5 min/night ≈ 150 min/month |
| GitHub Pages | public repos on a free account | one static site |
| Frankfurter (FX) | free, keyless | 1 call per refresh |

**Zero.** The earlier "$5/month" estimate was wrong — a rented server is not needed
for the market data.

The one catch: on a free personal account **Pages only publishes from a public
repo** (private-repo Pages needs GitHub Pro). That is fine here, because
everything published is auction data the houses already publish themselves. It is
also exactly why the client book must not live in this repository — see
`.gitignore`, which blocks it explicitly.

---

## First-time setup

```bash
# from this directory
git init && git branch -M main
git add . && git commit -m "The Gallery"
gh repo create the-gallery --public --source=. --push
```

Then in the repository, **Settings → Pages → Source: Deploy from a branch →
main / (root)**. The site appears at `https://<user>.github.io/the-gallery/`.

Seed the database once, so the first scheduled run has history to build on:

```bash
# Actions tab → "Refresh auction data" → Run workflow → mode: full
gh workflow run refresh.yml -f mode=full
```

A full rebuild takes roughly 40 minutes, most of it Pundole's mandatory 10-second
crawl delay. Every night after that is incremental: about five minutes, of which
four are the Saffronart medium-and-size backfill chipping away 250 lots at a time.
Once that backfill is exhausted a nightly run drops to about a minute.

On the iPad: open the Pages URL in Safari → Share → **Add to Home Screen**.

---

## How the refresh stays cheap

Newest sales sit at the top of each house's list, so the nightly job pulls only
the four most recent per house. Re-ingesting a sale is harmless — every write is
`INSERT OR REPLACE`.

The SQLite database is the accumulated history and is **cached** between runs
rather than committed; at 11 MB a nightly binary commit would add roughly 4 GB of
git history a year. If the cache is ever evicted the workflow notices the missing
file and does a full rebuild by itself.

Saffronart's sale index loads over XHR and cannot be listed without a browser, so
it is cached in `data/saffronart_sales.json`. `--discover 6` probes the event ids
just above the highest known one each night, which is how new Saffronart sales
find their way in.

## When it breaks

These are someone else's websites, and they change. The likely failures, in order:

1. **A house changes its markup** — the ingester logs `FAILED` per sale and keeps
   going, so one broken house never takes the others down. Check the run summary.
2. **Cron stops firing** — GitHub disables schedules on repos with 60 days of no
   activity. The workflow commits on most runs, which prevents it.
3. **Cache miss** — costs one slow full rebuild, then resumes.

Nothing here retries forever or hammers a host: the courtesy pause in
`common.get()` and Pundole's 10-second crawl delay are not optional.

---

## The client book does not go here

Market data is public and belongs on a public host. Collector holdings, budgets
and conversation notes are not, and are sensitive under India's DPDP Act. When the
CRM is built it needs a private, authenticated backend with encryption at rest —
Cloudflare Workers plus KV covers it inside their free tier, and unlike GitHub
Pages it supports a private repository.

---

# The private half — Cloudflare

The client book cannot live on the public site, so it lives on Cloudflare Pages
with the same repo behind it. Two sites, one codebase:

| | GitHub Pages | Cloudflare Pages |
|---|---|---|
| URL | `aryanjhaveri06-sudo.github.io/the-gallery/` | `the-gallery.pages.dev` |
| Market data | yes | yes |
| Client book | sample only | **real, behind sign-in** |
| Her calendar | no | yes |
| Who can open it | anyone | only allowlisted emails |

The app detects which one it is on: `/api` answers on Cloudflare and 404s on
GitHub, so the public copy quietly shows the sample book instead of breaking.

## Why the same origin

Safari on iPad blocks third-party cookies. An API on `*.workers.dev` with the app
on `github.io` would lose the Access session on exactly the device this is for.
Pages Functions run on the app's own domain, so the cookie is first-party.

## Setup

```bash
npm install
npx wrangler login                       # opens a browser; authorises this machine

# 1. the database
npx wrangler d1 create gallery-crm       # copy the database_id into wrangler.toml
npm run schema:remote                    # create the tables
npx wrangler d1 execute gallery-crm --remote --file=db/seed.sql   # optional demo rows

# 2. the site
npx wrangler pages project create the-gallery --production-branch main
npm run deploy
```

Then in the dashboard, **Zero Trust → Access → Applications → Add → Self-hosted**:

- Application domain: `the-gallery.pages.dev`, path `api`
- Policy: *Allow*, rule **Emails** → Aashna's address (and yours)
- Copy the **Application Audience (AUD) tag**

Put those into `wrangler.toml` under `[vars]` — `ACCESS_TEAM_DOMAIN`,
`ACCESS_AUD`, `ALLOWED_EMAILS` — and redeploy. Until both the team domain and the
AUD are set the API refuses every request, so a half-finished setup cannot leak
the book.

Finally, her calendar:

```bash
npx wrangler pages secret put CALENDAR_ICS_URL
```

Paste the secret iCal address — Google Calendar → *Settings for my calendars* →
*Integrate calendar* → **Secret address in iCal format**; or iCloud → share the
calendar → **Public Calendar** and copy the `webcal://` link, changed to
`https://`. It is a secret because it grants read access to her whole diary,
which is why it is a Worker secret and never a var.

## Local development

```bash
npm run seed:local
npx wrangler pages dev . --port 8788 --binding CRM_DEV_IDENTITY=you@example.com
```

`CRM_DEV_IDENTITY` bypasses Access for local work only. It must never be set on
the deployed project — if it is, anyone reaching the API is treated as signed in.

## What is protected, and how

- Access runs the login (email one-time code). No password is handled by this
  code, or by me.
- The Worker **verifies the Access JWT** — RS256 against the team's published
  keys, plus audience, issuer and expiry — rather than trusting the email header,
  which is forgeable by anything that reaches the Worker around Access.
- `ALLOWED_EMAILS` is a second lock, so a mis-scoped Access policy alone cannot
  open the book.
- Every write records the verified email in `audit`.
- Responses are `no-store, private` — a client book must not sit in any cache.
- `.gitignore` blocks `crm/`, `.wrangler/` and `.dev.vars`. Note that
  `.gitignore` has no trailing-comment syntax: `.wrangler/ # note` is a literal
  pattern that matches nothing. Comments go on their own line.

## Costs

Free. D1 allows 5 GB and 5 million row reads a day; Pages Functions allow 100,000
requests a day; Access is free to 50 users. One desk is nowhere near any of them.
