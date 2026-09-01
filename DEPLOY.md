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
| URL | `aryanjhaveri06-sudo.github.io/the-gallery/` | `the-gallery-ct1.pages.dev` |
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

## Status — done

Deployed 26 August 2026 on Aashna's Cloudflare account
(`aashna.jhaveri96@gmail.com`, account `2a0686c4e5ad7d4827b664425eeb99aa`).

- D1 `gallery-crm` — `fc8e569d-499d-4553-87c9-fec3e3c484ce`, schema applied,
  demo rows loaded. Clear them before real clients go in: `DELETE FROM client;`
  (holdings, logs and follow-ups cascade).
- Pages project `the-gallery` → **https://the-gallery-ct1.pages.dev**
- `DB` binding attached and confirmed on the production deployment.
- `/api/*` returns 401 to everyone, because the Access variables are still
  empty. That is the fail-closed path, not a fault.

## Status — still to do (needs the dashboard)

The wrangler OAuth token carries `d1` and `pages` scope but **no Access scope**,
and Access is not yet enabled on the account, so these three steps are yours:

1. **Enable Access.** dash.cloudflare.com → **Zero Trust** → pick a team name.
   That name becomes `<team>.cloudflareaccess.com`.
2. **Add the application.** Zero Trust → **Access → Applications → Add →
   Self-hosted**. Domain `the-gallery-ct1.pages.dev`, path `api`. Policy
   *Allow*, rule **Emails** → Aashna's address and yours. Copy the
   **Application Audience (AUD) tag**.
3. **Fill the variables.** Pages → the-gallery → Settings → Variables:
   `ACCESS_TEAM_DOMAIN` = `<team>.cloudflareaccess.com`,
   `ACCESS_AUD` = the AUD tag, `ALLOWED_EMAILS` = the same addresses.
   Redeploy. The client book lights up.

## Keeping the two sites in sync

The nightly workflow pushes to GitHub, which updates GitHub Pages on its own.
Cloudflare will not follow unless you connect it: Pages → the-gallery →
**Settings → Builds & deployments → Connect to Git** → this repo, branch `main`,
build command empty, output directory `/`. After that every nightly commit
deploys both sites. Until then, `npm run deploy` publishes the Cloudflare copy
by hand.

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

## The drafter — Mistral

Optional. Without it the desk works exactly as before; the Pitch generator shows
its template and no button appears. With it, the Knowledge screen grows a
**Write it out** button that turns the same records into a note she can send.

Get a key at <https://console.mistral.ai> → **API Keys**. The free *Experiment*
plan needs a phone number and no card.

```bash
npx wrangler pages secret put MISTRAL_API_KEY --project-name the-gallery
```

Paste the key when it prompts; it never goes in the repo, in `wrangler.toml`, or
in the page. The browser asks *this* origin for a draft — it never talks to
Mistral, so the key cannot be read out of a public `index.html`.

**Do this second, in the same console: Admin → Privacy → turn OFF "use my data
for model improvement."** The free tier trains on API input by default, and
these requests carry a collector's first name and what she collects. The paid
tiers are opted out already; the free one is not.

### What actually leaves the origin

Enforced in `pitchFacts()` in `functions/api/[[path]].js`, not left to the
caller. Sent: the collector's **first name**, tier, the "what they collect" and
"still looking for" fields, the **artist names** they own, the date and channel
of the last contact, and up to four public auction results. Deliberately not
sent: the surname, the Background field (it names firms), what she paid for
anything, and the text of any logged conversation.

### The figure check

§11 says never invent a market fact, and a model that writes "up 34% since 2019"
into a letter to a buyer is worse than no model. So the prompt carries the facts
and forbids any others — and then `unsupportedFigures()` reads the answer back
and rejects it if any run of digits in it is not in the facts it was given. A
rejected draft is discarded, she keeps the template, and the API answers 422.
Prompting is not a control; this is. It is deliberately strict: a wrong
rejection costs a fallback, a wrong pass costs a wrong number sent to a
collector.

Cover it with `node tests/ai_grounding.mjs` — it stubs Mistral, so it needs no
key and spends no quota.

### Rate limits and models

The free tier is rate limited and Mistral no longer publishes the number, so
nothing calls it on render — every draft is one deliberate button press, and a
429 comes back as "give it a minute" rather than a retry storm.

Mistral renames its models often (`mistral-small-2503` became
`mistral-small-4-0-26-03`). If the configured id is rejected the Worker asks the
account what it actually has and picks the smallest sensible model, once per
isolate. Pin one with a `MISTRAL_MODEL` var if you want a specific model.

To retire the drafter, delete the secret — the button disappears on the next
load and nothing else changes.

## Local development

```bash
npm run seed:local
npx wrangler pages dev . --port 8788 --binding CRM_DEV_IDENTITY=you@example.com
```

`CRM_DEV_IDENTITY` bypasses Access for local work only. It must never be set on
the deployed project — if it is, anyone reaching the API is treated as signed in.

`MISTRAL_BASE_URL` points the drafter at something other than api.mistral.ai, so
the route can be driven end to end against a local stub without a key or quota.
Like `CRM_DEV_IDENTITY`, it is for local work only and is never set in production.

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
