"""is_hers() — the gate that decides what reaches her desk.

    python3 tests/news_filter.py

Every REJECT below actually made the live feed on 2026-09-08, which is what
prompted the filter: "art" and "arts" are in the name of a Performing Arts
Center, a stone-arts company selling pooja-room decor, and an experiential art
franchise, so all three cleared the art gate.

The KEEPs are the same day's real stories that must survive it. A blocklist
that only ever rejects is easy to write and useless; these are the guard rails.

NOT_HER_ART is mirrored in functions/_lib/news.js. This tests the Python copy;
if they drift the desk and the edge disagree about the same story.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "ingest"))
from news import is_hers

REJECT = [
    ("theutahreview.com", "Nitya Nritya set to celebrate 10th anniversary with an extensive "
                          "weekend of Indian classical and contemporary dance and music at "
                          "Mid-Valley Performing Arts Center, Kingsbury Hall"),
    ("openpr.com",        "Tilak Stone Arts Discusses Trends Shaping Indian Pooja Room Design"),
    ("Indian Retailer",   "Franchise TV Exclusive: French Experiential Art Brand Iris Galerie "
                          "Prepares for India Entry"),
    # the gates that already existed must still hold
    ("Mint",             "Top 10 Indian firms lose Rs 1.13 lakh crore in market cap"),
    ("Santa Fe New Mexican", "Native American Indian art market opens in Santa Fe"),
    ("The Hindu",        "Mumbai monsoon floods disrupt local trains"),          # no art
    ("artnet News",      "Basquiat painting sells for $40M at Christie's"),      # no India
]

# Gets through, and stays that way on purpose. Blocking any of these needs a word
# back in NOT_HER_ART — "craft", "dancer", "film" — and each of those words kills
# a story in KEEP below: a Saffronart sale of Jamini Roy's Santhal DANCERS, the
# CRAFT of Tyeb Mehta's line, Husain FILM reels auctioned with his paintings.
# The trade is deliberate and runs one way: a story she does not need costs her
# one glance, a market result she never sees costs her the result. If a future
# reader is tempted to tighten this, that is the thing to weigh.
TOLERATED = [
    ("The Hindu",         "Sanskriti Museum at Humayun's Tomb celebrates India's everyday art "
                          "and craft traditions"),
    ("The Times of India","Indian classical dancer wins award at Mumbai arts festival"),
    ("Variety",           "Indian film on a Mumbai painter wins at Cannes"),
]

KEEP = [
    # real, from the same day
    ("CNBC TV18",       "Mahatma Gandhi's handwritten manuscript sets world record at "
                        "\u00a31.34 million Saffronart auction"),
    ("BLLNR.asia",      "AstaGuru Expands Into Singapore as Asia's Collector Market Grows"),
    ("Hindustan Times", "Mahatma Gandhi's manuscript sells for \u20b916.2 crore at auction, "
                        "sets record - Check details | India News"),
    ("The Hindu",       "Latika Katt's final works celebrate nature and transience at "
                        "Delhi's Gallery Threshold"),
    ("The Hindu",       "Botticelli's Madonna and Child comes to Bengaluru",
                        "The Renaissance painting is part of One Mother, Many Mother Tongues, "
                        "a new exhibition, co-curated by Naman Ahuja and Andrea Anastasio"),
    ("The Art Newspaper", "Tyeb Mehta canvas leads Pundole's Mumbai sale"),
    ("ARTnews",         "Kiran Nadar Museum of Art opens new Delhi building"),

    # THE ONES THAT MATTER. Each carries a word the blocklist reaches for, in the
    # context that makes it hers. The first version of NOT_HER_ART killed all of
    # these — a Saffronart sale, an AstaGuru auction, a crore-level Sher-Gil —
    # and the test passed anyway, because none of the fixtures above touch a
    # blocked word. A blocklist is only as good as the keeps that constrain it.
    ("The Hindu",       "Jamini Roy's Santhal dancers lead Saffronart's Mumbai sale"),
    ("Mint",            "Husain's Bollywood muse: a painter's love affair with cinema, "
                        "at Pundole's"),
    ("The Hindu",       "The craft of Tyeb Mehta's line, revisited at Kiran Nadar Museum"),
    ("Indian Express",  "Anjolie Ela Menon textile works head AstaGuru's September auction"),
    ("The Hindu",       "Ceramics and pottery by Gurcharan Singh at Delhi gallery show"),
    ("The Hindu",       "Raza's Bindu and the music of pure form: a Mumbai retrospective"),
    ("Indian Express",  "The drama of Souza's brushwork: new Indian art exhibition in Delhi"),
    ("Hindustan Times", "MF Husain film reels auctioned alongside Indian modern paintings"),
    ("Telegraph India", "Bengal School artisans' watercolours at Kolkata auction"),
]

# Known ceiling, not a bug to fix here: the India gate is literal, so a headline
# that names only an artist ("Amrita Sher-Gil painting of Hungarian dancers sells
# for Rs 12 crore") is dropped for want of an India word. Deliberate — it is what
# keeps American community art-auction fundraisers off the desk. Teaching
# INDIA_TERMS the 48 tracked artists would fix it and would also let a Souza in
# Lisbon through.

bad = 0
for row in REJECT:
    source, headline, why = (row + ("",))[:3] if len(row) == 2 else row
    if is_hers({"headline": headline, "source": source, "why": why}):
        print(f"  LET THROUGH  {source} — {headline[:70]}"); bad += 1
    else:
        print(f"  ok reject    {source} — {headline[:60]}")
for row in KEEP:
    source, headline, why = (row + ("",))[:3] if len(row) == 2 else row
    if not is_hers({"headline": headline, "source": source, "why": why}):
        print(f"  WRONGLY CUT  {source} — {headline[:70]}"); bad += 1
    else:
        print(f"  ok keep      {source} — {headline[:60]}")

# The edge runs its own copy of these lists in functions/_lib/news.js, so the
# desk and the live layer can disagree about the same story without anyone
# noticing. Compare them rather than trusting the comment that says to.
import re, news
js = (pathlib.Path(__file__).resolve().parent.parent / "functions/_lib/news.js").read_text()
for name in ("NOT_HER_ART", "FINANCE_NOISE", "ART_TERMS", "INDIA_TERMS", "NOT_HERS", "NOT_A_PUBLISHER"):
    m = re.search(r"const %s = \[(.*?)\];" % name, js, re.S)
    if not m:
        print(f"  DRIFT  {name} not found in news.js"); bad += 1; continue
    # strip // comments first: news.js explains why "biennale" is NOT in
    # INDIA_TERMS, and a naive scan reads the explanation as a term
    body = re.sub(r"//[^\n]*", "", m.group(1))
    in_js = set(re.findall(r'"([^"]+)"', body))
    in_py = set(getattr(news, name))
    if in_js != in_py:
        only_js, only_py = sorted(in_js - in_py), sorted(in_py - in_js)
        print(f"  DRIFT  {name}: js-only {only_js} | py-only {only_py}"); bad += 1
    else:
        print(f"  ok in step   {name} ({len(in_py)} terms)")

for row in TOLERATED:
    source, headline, why = (row + ("",))[:3] if len(row) == 2 else row
    verdict = "gets through" if is_hers({"headline": headline, "source": source, "why": why}) else "now blocked"
    print(f"  tolerated: {verdict} — {headline[:56]}")

print(f"\n{len(REJECT)} reject + {len(KEEP)} keep + {len(TOLERATED)} tolerated + 6 list comparisons — "
      f"{'all correct' if not bad else str(bad) + ' WRONG'}")
sys.exit(1 if bad else 0)
