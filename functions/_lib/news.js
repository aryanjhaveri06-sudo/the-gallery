/**
 * Live market headlines, fetched at the edge.
 *
 * The nightly pipeline (ingest/news.py) bakes a copy into app_data.json, which
 * caps freshness at 24 hours and only if the cron ran. This endpoint fetches
 * Google News directly so the private desk is current within the cache window.
 *
 * The filter constants below MUST stay in step with ingest/news.py — same
 * queries, same gates. If you change one, change the other.
 *
 * Why regex and not a parser: Workers have no DOMParser. The feed is
 * machine-generated and we need four fields per item, so a parser would be
 * ceremony. Everything extracted is escaped by the app before it is rendered.
 */

/* Google News is NOT usable here: it answers a Cloudflare Worker with 503,
   every time, measured. Bing News RSS does answer, but its items carry no
   <pubDate> and it served results from 2023 — useless for a live feed.
   So this goes direct to publishers instead, which is the better source anyway.
   All five verified from a Worker on 2026-08-31: 200, dated, and current.

   The nightly pipeline still queries Google News from GitHub Actions, where it
   works, and that catches the house-specific stories (Saffronart, AstaGuru)
   these general feeds will not. The app merges the two. */
const FEEDS = [
  ["The Hindu",          "https://www.thehindu.com/entertainment/art/feeder/default.rss"],
  ["The Indian Express", "https://indianexpress.com/section/lifestyle/art-and-culture/feed/"],
  ["Economic Times",     "https://economictimes.indiatimes.com/magazines/panache/rssfeeds/1466318837.cms"],
  ["Hindustan Times",    "https://www.hindustantimes.com/feeds/rss/lifestyle/art-culture/rssfeed.xml"],
  ["The Art Newspaper",  "https://www.theartnewspaper.com/rss.xml"],
];

// A publisher feed carries everything that section publishes, so the India gate
// has to be explicit here in a way it did not need to be for an India-targeted
// search query.
const INDIA_TERMS = [
  "india", "indian", "mumbai", "bombay", "delhi", "kolkata", "calcutta",
  "chennai", "bengaluru", "bangalore", "hyderabad", "jaipur", "goa", "kochi",
  "bengal", "south asian", "subcontinent", "saffronart", "astaguru", "pundole",
  "kiran nadar", "india art fair", "art mumbai", "kochi-muziris",
  // NOT "biennale" — that let the Gwangju Biennale through as Indian news.
];

const NOT_HERS = [
  "native american", "santa fe", "indigenous", "first nations", "navajo",
  "cherokee", "pueblo", "tribal nation", "indian country", "red cloud",
  "southwestern association", "kiowa", "comanche", "apache",
];

const NOT_A_PUBLISHER = [
  "linkedin", "facebook", "instagram", "youtube", "reddit", "medium.com",
  "pr newswire", "prnewswire", "globenewswire", "businesswire", "yahoo finance",
  "vajiram", "testbook", "byjus", "unacademy", "adda247",
];

// This is an ART desk. "Indian art auction" also matches the closing auction in
// an MSCI index rebalance; "market" matches the equity market.
const FINANCE_NOISE = [
  "sensex", "nifty", "msci", "share price", "shares", "stock", "stocks",
  "bourses", "equity", "equities", "ipo", "market cap", "rebalancing",
  "closing auction", "trading session", "mutual fund", "sebi", "bond yield",
  "futures", "nasdaq", "dow jones", "quarterly results", "gdp", "inflation",
  "brokerage", "listing gains", "derivatives", "crore in market",
];

const ART_TERMS = [
  "art", "arts", "artist", "artists", "artwork", "artworks", "painting",
  "paintings", "painter", "sculpture", "sculptor", "canvas", "gallery",
  "galleries", "collector", "collectors", "museum", "biennale", "biennial",
  "exhibition", "drawing", "drawings", "watercolour", "watercolor",
  "lithograph", "masterpiece", "provenance", "antiquities", "memorabilia",
  "manuscript", "saffronart", "astaguru", "pundole", "sotheby", "sothebys",
  "christie", "christies", "bonhams", "phillips", "kiran nadar",
];

const ENTITIES = {
  amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ",
  rsquo: "’", lsquo: "‘", ldquo: "“", rdquo: "”",
  mdash: "—", ndash: "–", hellip: "…",
};

function unescapeXml(t) {
  return String(t || "").replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, (m, body) => {
    if (body[0] === "#") {
      const code = body[1] === "x" || body[1] === "X"
        ? parseInt(body.slice(2), 16) : parseInt(body.slice(1), 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : m;
    }
    return ENTITIES[body] ?? m;
  });
}

const clean = t => unescapeXml(String(t || "").replace(/<[^>]+>/g, " ")).replace(/\s+/g, " ").trim();

const wordCache = new Map();
function hasWord(hay, term) {
  let rx = wordCache.get(term);
  if (!rx) {
    rx = new RegExp("(?<!\\w)" + term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?!\\w)");
    wordCache.set(term, rx);
  }
  return rx.test(hay);
}

function isHers(item) {
  const hay = (item.headline + " " + (item.why || "")).toLowerCase();
  if (NOT_HERS.some(t => hay.includes(t))) return false;
  if (NOT_A_PUBLISHER.some(t => item.source.toLowerCase().includes(t))) return false;
  if (FINANCE_NOISE.some(t => hasWord(hay, t))) return false;
  if (!ART_TERMS.some(t => hasWord(hay, t))) return false;
  return INDIA_TERMS.some(t => hasWord(hay, t));
}

function parseFeed(xml, publisher) {
  const out = [];
  const blocks = xml.split("<item>").slice(1);
  for (const raw of blocks) {
    const b = raw.split("</item>")[0];
    const pick = tag => {
      const m = b.match(new RegExp("<" + tag + "[^>]*>([\\s\\S]*?)</" + tag + ">"));
      if (!m) return "";
      // Publisher feeds wrap nearly everything in CDATA.
      const inner = m[1].replace(/^\s*<!\[CDATA\[/, "").replace(/\]\]>\s*$/, "");
      return clean(inner);
    };
    const headline = pick("title");
    const url = pick("link");
    if (!headline || !url) continue;
    const ts = Date.parse(pick("pubDate") || pick("dc:date"));
    if (!Number.isFinite(ts)) continue;
    out.push({ headline, url, source: publisher, when: ts,
               why: pick("description").slice(0, 150) });
  }
  return out;
}

const STOP = new Set(("a an and the of for in on at to by with from as is are was were it its this that " +
  "these those his her their new after over into out up down auction auctions sale sales sells sold art " +
  "artist artwork million crore rs inr").split(" "));

const signature = h => new Set(
  (h.toLowerCase().match(/[a-z0-9]+/g) || []).filter(w => w.length > 3 && !STOP.has(w)));

/** Quota by subject, newest first — one story told six ways must not fill the panel. */
function diversify(items, limit, perSubject = 2) {
  const freq = new Map();
  for (const i of items) for (const w of signature(i.headline)) freq.set(w, (freq.get(w) || 0) + 1);
  const ubiquitous = Math.max(3, Math.floor(items.length / 2));
  const used = new Map(), out = [];
  for (const i of [...items].sort((a, b) => b.when - a.when)) {
    const keys = [...signature(i.headline)].filter(w => {
      const f = freq.get(w) || 0; return f >= 2 && f <= ubiquitous;
    });
    if (keys.some(w => (used.get(w) || 0) >= perSubject)) continue;
    for (const w of keys) used.set(w, (used.get(w) || 0) + 1);
    out.push(i);
    if (out.length >= limit) break;
  }
  return out;
}

const UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36";

const CACHE_KEY = "https://gallery.internal/news-v1";
const TTL_SECONDS = 900;   // 15 minutes at the edge

export async function liveNews(ctx) {
  const cache = caches.default;
  const key = new Request(CACHE_KEY);
  const hit = await cache.match(key);
  if (hit) {
    const payload = await hit.json();
    return { ...payload, cached: true };
  }

  const results = await Promise.allSettled(FEEDS.map(async ([publisher, url]) => {
    const r = await fetch(url, {
      headers: { "User-Agent": UA, Accept: "application/rss+xml, application/xml, text/xml" },
      cf: { cacheTtl: 600, cacheEverything: true },
    });
    if (!r.ok) throw new Error(publisher + " -> " + r.status);
    return parseFeed(await r.text(), publisher);
  }));

  const seen = new Set(), pool = [];
  const errors = [];
  let feedsOk = 0;
  const cutoff = Date.now() - 21 * 24 * 3600 * 1000;
  for (const res of results) {
    if (res.status !== "fulfilled") {
      // allSettled swallows the reason; without this a total failure looks
      // identical to a quiet news day.
      errors.push(String((res.reason && res.reason.message) || res.reason).slice(0, 160));
      continue;
    }
    feedsOk++;
    for (const item of res.value) {
      if (item.when < cutoff) continue;
      if (!isHers(item)) continue;
      const k = item.url.split("?")[0].slice(-90);
      if (seen.has(k)) continue;
      seen.add(k);
      pool.push(item);
    }
  }

  const payload = {
    generated_at: new Date().toISOString(),
    feeds_ok: feedsOk,
    feeds_total: FEEDS.length,
    errors: errors.slice(0, 4),
    on_market: diversify(pool, 12).map(i => ({
      date: new Date(i.when).toISOString().slice(0, 10),
      source: i.source, headline: i.headline, why: "", url: i.url,
    })),
  };

  // Only cache a result worth serving; a bad fetch must not stick for 15 minutes.
  if (payload.on_market.length) {
    const body = new Response(JSON.stringify(payload), {
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "public, max-age=" + TTL_SECONDS,
      },
    });
    ctx.waitUntil(cache.put(key, body.clone()));
  }
  return { ...payload, cached: false };
}
