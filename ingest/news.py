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
    "Indian art auction",
    "Indian art market",
    "modern Indian art sale",
    "Saffronart auction",
    "AstaGuru auction",
    "Pundole's auction",
]
GOOGLE_NEWS = ("https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en")

# "Indian" also means Native American in the American press. A headline whose
# only Indian signal is the bare word, alongside one of these, is not hers.
NOT_HERS = [
    "native american", "santa fe", "indigenous", "first nations", "navajo",
    "cherokee", "pueblo", "tribal nation", "indian country", "red cloud",
    "southwestern association",          # ...for Indian Arts — Santa Fe, not Mumbai
]

# Google News indexes social posts and PR wires alongside journalism. A desk
# quoting a headline to a collector needs a publisher behind it, not a LinkedIn
# post with an emoji in the title.
NOT_A_PUBLISHER = [
    "linkedin", "facebook", "instagram", "youtube", "reddit", "medium.com",
    "pr newswire", "prnewswire", "globenewswire", "businesswire", "yahoo finance",
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


def is_hers(item):
    """Guard the two ways this query goes wrong: in the American press "Indian"
    often means Native American, and Google indexes posts as though they were
    reporting."""
    hay = (item["headline"] + " " + item["why"]).lower()
    if any(t in hay for t in NOT_HERS):
        return False
    src = item["source"].lower()
    return not any(t in src for t in NOT_A_PUBLISHER)


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
            key = re.sub(r"[?#].*$", "", it["url"])[-90:]
            if key in seen:
                continue
            seen.add(key)
            bucket.append(it)
            kept += 1
        return kept

    mine = []
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

    mine = diversify(mine, args.limit)
    wider = diversify(wider, args.limit)

    def shape(i):
        return {
            "date": i["when"].strftime("%Y-%m-%d"),
            "source": i["source"],
            "headline": i["headline"],
            "why": i["why"],
            "url": i["url"],
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
