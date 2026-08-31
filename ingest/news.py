"""Pull real art-market headlines. Nothing on this page is written by us.

This replaces four authored headlines that used to sit in index.html and read as
if they were reported. They were invented — including a sell-through figure that
`art-desk-data-sources` rule 4 says cannot be derived from either house's
results. A desk that quotes a number to a collector needs the number to be real,
so headline, publisher, date and link all come from the publisher's own feed.

Two tiers, kept apart on purpose:

  ON HER MARKET  Google News RSS, one query per thing she actually trades. Free,
                 keyless, and it reaches the Indian trade and business press —
                 Livemint, Rediff, the art trade blogs — which is where this
                 market is actually covered.
  WIDER          The international art press. Real, but mostly not about her
                 category, so it is labelled as context and never mixed in.

Why not score the international feeds for Indian relevance? That was tried. Over
a fortnight of five trade feeds it produced exactly one match, and that match was
"Santa Fe Indian Market" — Native American art. The word is ambiguous and these
outlets simply do not cover the Indian secondary market often. Asking Google News
the question directly works; inferring it from a general feed does not.

Feeds verified 2026-08-26.

Usage:  python3 ingest/news.py [--days 45] [--limit 12] [--out FILE]
"""

import argparse
import json
import re
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from common import ROOT, get

# One query per thing she trades. Kept simple deliberately: Google News returns
# nothing for quoted, OR-heavy queries — the two elaborate ones tried first came
# back with zero items while these return a hundred.
QUERIES = [
    # `when:` is not optional. Google News RSS ranks by RELEVANCE and truncates
    # at 100, so without a recency bound the fresh stories fall off the end:
    # "Saffronart" alone returned 100 items with ONE from the past week and
    # nothing newer than six days old, while "Saffronart when:7d" returned seven,
    # all fresh. That is exactly why this desk sat on 25 August headlines for six
    # days while the nightly refresh ran green every single night.
    #
    # Houses get a longer window than themes: low volume, always relevant.
    # 21 days, not 14: a record price is still live information to a dealer a
    # fortnight later, and a 14-day window dropped the ₹16.2 crore Gandhi sale
    # off the desk while it was still the biggest Indian result of the season.
    "Indian art auction when:21d",
    "Indian art market when:21d",
    "modern Indian art when:21d",
    "Saffronart when:30d",
    "AstaGuru auction when:30d",
    "Pundole's auction when:30d",
]
GOOGLE_NEWS = ("https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en")

# "Indian" also means Native American in the American press. A headline whose
# only Indian signal is the bare word, alongside one of these, is not hers.
NOT_HERS = [
    "native american", "santa fe", "indigenous", "first nations", "navajo",
    "cherokee", "pueblo", "tribal nation", "indian country", "red cloud",
    "southwestern association",          # ...for Indian Arts — Santa Fe, not Mumbai
    "kiowa", "comanche", "apache",
]

# Google News indexes social posts and PR wires alongside journalism. A desk
# quoting a headline to a collector needs a publisher behind it, not a LinkedIn
# post with an emoji in the title.
NOT_A_PUBLISHER = [
    "linkedin", "facebook", "instagram", "youtube", "reddit", "medium.com",
    "pr newswire", "prnewswire", "globenewswire", "businesswire", "yahoo finance",
    "vajiram", "testbook", "byjus", "unacademy", "adda247",   # exam-prep listicles
]

# Read directly, exactly as functions/_lib/news.js does. These are where the
# pictures come from: Google News carries none and hides the publisher URL, so
# an India tier built only on Google is a wall of text.
INDIA_FEEDS = [
    ("The Hindu",          "https://www.thehindu.com/entertainment/art/feeder/default.rss"),
    ("The Indian Express", "https://indianexpress.com/section/lifestyle/art-and-culture/feed/"),
    ("Economic Times",     "https://economictimes.indiatimes.com/magazines/panache/rssfeeds/1466318837.cms"),
    ("Hindustan Times",    "https://www.hindustantimes.com/feeds/rss/lifestyle/art-culture/rssfeed.xml"),
]

WIDER_FEEDS = [
    ("The Art Newspaper", "https://www.theartnewspaper.com/rss.xml"),
    ("artnet News",       "https://news.artnet.com/feed"),
    ("ARTnews",           "https://www.artnews.com/feed/"),
    ("Hyperallergic",     "https://hyperallergic.com/feed/"),
    ("Artforum",          "https://www.artforum.com/feed/"),
]

# The vocabulary of this market. Lower-cased, matched on word boundaries.
STRIP_TAGS = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")
ENTITY = re.compile(r"&(#x?[0-9a-fA-F]+|[a-zA-Z]+);")

ENTITIES = {
    "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " ",
    "rsquo": "’", "lsquo": "‘", "ldquo": "“", "rdquo": "”",
    "mdash": "—", "ndash": "–", "hellip": "…", "amp;": "&",
}


def unescape(text):
    def sub(m):
        body = m.group(1)
        if body.startswith("#"):
            try:
                code = int(body[2:], 16) if body[1] in "xX" else int(body[1:])
                return chr(code)
            except (ValueError, OverflowError):
                return m.group(0)
        return ENTITIES.get(body, m.group(0))
    return ENTITY.sub(sub, text or "")


def clean(text, limit=None):
    """Feed HTML down to plain text. Publishers put markup in descriptions."""
    out = WS.sub(" ", unescape(STRIP_TAGS.sub(" ", text or ""))).strip()
    if limit and len(out) > limit:
        cut = out[:limit].rsplit(" ", 1)[0]
        out = cut.rstrip(" ,;:.—–") + "…"
    return out


def parse_when(raw):
    """RSS uses RFC 822, Atom uses ISO 8601. Accept either, UTC or bust."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        d = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        try:
            d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d is None:
        return None
    return d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)


def tag(el):
    """Strip the Atom namespace so RSS and Atom read the same."""
    return el.tag.split("}")[-1]


def first(el, *names):
    for child in el:
        if tag(child) in names:
            if tag(child) == "link" and not (child.text or "").strip():
                return child.get("href") or ""
            return (child.text or "").strip()
    return ""


IMG_IN_HTML = re.compile(r'<img[^>]*\ssrc="([^"]+)"')


def feed_image(el, raw=""):
    """Publishers ship an image, but never in the same tag.

    media:content and media:thumbnail carry a url attribute; ET uses enclosure;
    some only inline an <img> in the description HTML. Try them in that order.
    """
    for child in el:
        t = tag(child)
        if t in ("content", "thumbnail", "enclosure"):
            u = child.get("url") or child.get("href")
            if u and u.startswith("http"):
                return u
    for child in el:
        if tag(child) in ("description", "summary", "encoded"):
            m = IMG_IN_HTML.search(child.text or "")
            if m:
                return m.group(1)
    return None


def read_feed(source, url):
    body = get(url, expect_json=False, pause=1.0)
    try:
        root = ET.fromstring(body.encode("utf-8", "replace"))
    except ET.ParseError as e:
        raise RuntimeError(f"unparseable feed: {e}")

    out = []
    for el in root.iter():
        if tag(el) not in ("item", "entry"):
            continue
        title = clean(first(el, "title"))
        link = first(el, "link")
        if not (title and link):
            continue
        when = parse_when(first(el, "pubDate", "published", "updated", "date"))
        summary = clean(first(el, "description", "summary", "subtitle"), 150)
        out.append({
            "source": source,
            "headline": title,
            "url": link,
            "when": when,
            "why": summary,
            "image": feed_image(el, body),
        })
    return out


# Surnames that are also ordinary words, or so common they would match anything.
def google_news(query):
    """Google News wraps the publisher's name into the title as " - Publisher"
    and repeats it in <source>; keep the source, strip it from the headline."""
    url = GOOGLE_NEWS.format(q=urllib.parse.quote(query))
    body = get(url, expect_json=False, pause=1.2)
    root = ET.fromstring(body.encode("utf-8", "replace"))
    out = []
    for el in root.iter():
        if tag(el) != "item":
            continue
        title = clean(first(el, "title"))
        link = first(el, "link")
        if not (title and link):
            continue
        source = ""
        for child in el:
            if tag(child) == "source":
                source = clean(child.text)
        if source and title.endswith(" - " + source):
            title = title[: -(len(source) + 3)].strip()
        out.append({
            "source": source or "Google News",
            "headline": title,
            "url": link,
            "when": parse_when(first(el, "pubDate")),
            "why": "",          # Google's description is just the headline again
            "image": None,      # Google News carries none; publisher feeds do
            "query": query,
        })
    return out


STOPWORDS = set("""a an and the of for in on at to by with from as is are was were
it its this that these those his her their new after over into out up down
auction auctions sale sales sells sold art artist artwork million crore rs inr
""".split())


def signature(headline):
    """The words that actually identify a story, for spotting the same story
    told by six papers. Google News returns each syndication separately: one
    Gandhi manuscripts sale filled seven of twelve slots."""
    words = re.findall(r"[a-z0-9]+", headline.lower())
    return {w for w in words if len(w) > 3 and w not in STOPWORDS}


# A market story names a house or an auction event. "collector" alone is not
# enough — it matched a furniture showroom.
MARKET_SIGNAL = re.compile(
    r"saffronart|astaguru|pundole|sotheby|christie|bonhams|"
    r"\b(auction|auctions|auctioned|sold|sells|fetches|record|hammer|"
    r"consignment|crore|lakh)\b", re.I)


def is_market(item):
    return bool(MARKET_SIGNAL.search(item["headline"] + " " + item.get("why", "")))


OG_IMAGE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)(?::src)?["\']'
    r'[^>]+content=["\']([^"\']+)["\']', re.I)
OG_IMAGE_REV = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+'
    r'(?:property|name)=["\'](?:og:image|twitter:image)(?::src)?["\']', re.I)


def resolve_image(url):
    """Fetch a story's own og:image.

    NOT for news.google.com links. That interstitial carries no canonical, no
    data-n-au and no JS redirect we can read — the publisher URL is inside
    obfuscated script — and its og:image is Google's logo. Resolving twelve of
    them returned the SAME googleusercontent URL twelve times, which looks like
    success and is not. Google-sourced items get no picture; that is why the
    India feeds below are read directly.
    """
    if "news.google.com" in url or "google.com/rss" in url:
        return None
    try:
        html = get(url, expect_json=False, retries=1, pause=0.4)
    except Exception:
        return None
    m = OG_IMAGE.search(html) or OG_IMAGE_REV.search(html)
    if not m:
        return None
    src = m.group(1).strip()
    if src.startswith("//"):
        src = "https:" + src
    if not src.startswith("http"):
        return None
    if "googleusercontent.com" in src or "news.google.com" in src:
        return None
    return src


def diversify(items, limit, per_subject=2):
    """Keep the panel varied, newest first.

    Word overlap does not cluster syndicated coverage: one Gandhi manuscripts
    sale arrived as "Memorabilia Smashes Global Auction Records", "Artefacts,
    Including Handwritten Notes" and "688 handwritten notes sell for Rs 16.2
    crore" — the same story three ways, sharing one significant word between
    them. So the rule is not similarity but quota: whatever the distinctive
    words of a story are, at most `per_subject` items may carry them.

    "Distinctive" means appearing in a handful of candidates, not in most of
    them — "indian" and "market" identify nothing here.
    """
    freq = {}
    for it in items:
        for w in signature(it["headline"]):
            freq[w] = freq.get(w, 0) + 1
    # A word is a SUBJECT marker when it repeats but is not near-universal:
    # "gandhi" in six of fourteen headlines is one story told six times;
    # "indian" in eight of fourteen is just the beat this desk covers.
    ubiquitous = max(3, len(items) // 2)

    used, out = {}, []
    for it in sorted(items, key=lambda i: -i["when"].timestamp()):
        keys = [w for w in signature(it["headline"])
                if 2 <= freq.get(w, 0) <= ubiquitous]
        if any(used.get(w, 0) >= per_subject for w in keys):
            continue
        for w in keys:
            used[w] = used.get(w, 0) + 1
        out.append(it)
        if len(out) >= limit:
            break
    return out


# THIS IS AN ART DESK, NOT A MARKETS DESK. "Indian art auction" also matches the
# closing auction in an MSCI index rebalance and "market" matches the equity
# market, so bounding the queries for freshness dragged in "MSCI Rebalancing
# Threatens to Turn Messy" and "Top 10 Indian firms lose Rs 1.13 lakh crore in
# market". None of that goes in front of a collector.
FINANCE_NOISE = [
    "sensex", "nifty", "msci", "share price", "shares", "stock", "stocks",
    "bourses", "equity", "equities", "ipo", "market cap", "rebalancing",
    "closing auction", "trading session", "mutual fund", "sebi", "bond yield",
    "futures", "nasdaq", "dow jones", "quarterly results", "gdp", "inflation",
    "brokerage", "listing gains", "derivatives", "crore in market",
]

# ...and it has to be about art at all. Word-boundary matched, so "art" does not
# fire on "part" or "start".
ART_TERMS = [
    "art", "arts", "artist", "artists", "artwork", "artworks", "painting",
    "paintings", "painter", "sculpture", "sculptor", "canvas", "gallery",
    "galleries", "collector", "collectors", "museum", "biennale", "biennial",
    "exhibition", "drawing", "drawings", "watercolour", "watercolor",
    "lithograph", "masterpiece", "provenance", "antiquities", "memorabilia",
    "manuscript", "saffronart", "astaguru", "pundole", "sotheby", "sothebys",
    "christie", "christies", "bonhams", "phillips", "kiran nadar",
]

INDIA_TERMS = [
    "india", "indian", "mumbai", "bombay", "delhi", "kolkata", "calcutta",
    "chennai", "bengaluru", "bangalore", "hyderabad", "jaipur", "goa", "kochi",
    "bengal", "south asian", "subcontinent", "saffronart", "astaguru",
    "pundole", "kiran nadar", "india art fair", "art mumbai", "kochi-muziris",
]

_word_cache = {}


def has_word(hay, term):
    """Whole-word match. Substring matching let "art" fire on "part"."""
    rx = _word_cache.get(term)
    if rx is None:
        rx = _word_cache[term] = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)")
    return bool(rx.search(hay))


def is_hers(item):
    """Three gates, and a story has to clear all of them:

      - not Native American — "Indian" is ambiguous in the American press
      - from a publisher, not a LinkedIn post or an exam-prep listicle
      - about ART, and not about the equity market
    """
    hay = (item["headline"] + " " + item.get("why", "")).lower()
    if any(t in hay for t in NOT_HERS):
        return False
    if any(t in item["source"].lower() for t in NOT_A_PUBLISHER):
        return False
    if any(has_word(hay, t) for t in FINANCE_NOISE):
        return False
    if not any(has_word(hay, t) for t in ART_TERMS):
        return False
    # An India-targeted query still returns American stories that merely say
    # "Indian" — a community art-auction fundraiser in the US made the desk.
    # Mirrors INDIA_TERMS in functions/_lib/news.js; keep the two in step.
    return any(has_word(hay, t) for t in INDIA_TERMS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--out", default=str(ROOT / "data" / "news.json"))
    args = ap.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    seen = set()

    def collect(items, bucket):
        kept = 0
        for it in items:
            if not it.get("when") or it["when"] < cutoff:
                continue
            # Dedupe on the HEADLINE as well as the URL. The same Hindu story
            # arrives twice — once through Google News under a news.google.com
            # link, once from the Hindu feed direct — and the URLs do not match,
            # so a URL-only key printed it twice.
            url_key = re.sub(r"[?#].*$", "", it["url"])[-90:]
            head_key = "h:" + re.sub(r"[^a-z0-9]+", " ",
                                     it["headline"].lower()).strip()[:70]
            if url_key in seen or head_key in seen:
                continue
            seen.add(url_key)
            seen.add(head_key)
            bucket.append(it)
            kept += 1
        return kept

    # PUBLISHER FEEDS FIRST. The same story often arrives twice — from the
    # publisher with an image, and through Google News without one. Whichever is
    # collected first wins the dedupe, so read the ones that carry pictures
    # before the ones that do not.
    mine = []
    for source, url in INDIA_FEEDS:
        try:
            got = [i for i in read_feed(source, url) if is_hers(i)]
        except Exception as e:
            print(f"  {source:30} FAILED {e}", flush=True)
            continue
        print(f"  {source:30} {collect(got, mine):>3} kept", flush=True)

    # Google second, for the house-specific stories the general feeds miss.
    for q in QUERIES:
        try:
            got = [i for i in google_news(q) if is_hers(i)]
        except Exception as e:
            print(f"  {q[:28]:30} FAILED {e}", flush=True)
            continue
        print(f"  {q[:28]:30} {collect(got, mine):>3} kept", flush=True)

    wider = []
    for source, url in WIDER_FEEDS:
        try:
            got = read_feed(source, url)
        except Exception as e:
            print(f"  {source:30} FAILED {e}", flush=True)
            continue
        print(f"  {source:30} {collect(got, wider):>3} kept", flush=True)

    # Reserve slots for market stories. Newest-first selection alone filled every
    # slot with fresh gallery-scene pieces and evicted the ₹16.2 crore Gandhi
    # record — the biggest Indian result of the season — because it was six days
    # older. She trades on the market half; it does not get crowded out by a
    # furniture showroom opening.
    market = [i for i in mine if is_market(i)]
    scene = [i for i in mine if not is_market(i)]
    mine = sorted(diversify(market, 6) + diversify(scene, 8),
                  key=lambda i: -i["when"].timestamp())[: args.limit]
    wider = diversify(wider, args.limit)

    # Google-sourced items arrive imageless; go and get one for each of the few
    # that actually made the cut, rather than for all two hundred candidates.
    need = [i for i in mine + wider if not i.get("image")]
    print(f"\n  resolving og:image for {len(need)} items", flush=True)
    got = 0
    for i in need:
        i["image"] = resolve_image(i["url"])
        if i["image"]:
            got += 1
    print(f"  {got}/{len(need)} resolved", flush=True)

    def shape(i):
        return {
            "date": i["when"].strftime("%Y-%m-%d"),
            "source": i["source"],
            "headline": i["headline"],
            "why": i["why"],
            "url": i["url"],
            "image": i.get("image"),
        }

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "on_market": [shape(i) for i in mine],
        "wider": [shape(i) for i in wider],
    }
    with open(args.out, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print(f"\n{len(payload['on_market'])} on her market, "
          f"{len(payload['wider'])} wider -> {args.out}")
    for i in payload["on_market"][:8]:
        print(f"  {i['date']}  {i['source'][:20]:22} {i['headline'][:58]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
