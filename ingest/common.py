"""Shared plumbing for the Art Desk ingesters.

Stdlib only, on purpose: this has to run unattended on whatever host we end up
using, and every dependency is one more thing that breaks a scheduled refresh.
"""

import html as htmllib
import json
import os
import re
import sqlite3
import time
from datetime import date
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("ARTDESK_DB") or ROOT / "data" / "artdesk.db")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def get(url, expect_json=True, retries=3, pause=0.7):
    """GET with a browser UA, linear backoff, and a courtesy pause.

    Every house we pull from is someone else's server; pause is not optional.
    """
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/json, text/html;q=0.9",
                "Accept-Language": "en-GB,en;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=45) as r:
                body = r.read()
            time.sleep(pause)
            if expect_json:
                return json.loads(body)
            return body.decode("utf-8", "replace")
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries}: {url} ({last})")


# --------------------------------------------------------------------------
# Artist identity
# --------------------------------------------------------------------------

# The houses spell the same painter several ways. Normalising to initials-plus-
# surname collapses most of it ("S H Raza" / "S. H. Raza" / "S.H.RAZA"), but it
# cannot know that "Sayed Haider Raza" is the same man, so full names that the
# trade abbreviates are seeded by hand. Add to this as new spellings turn up.
ALIASES = {
    "sayed haider raza": "s h raza",
    "syed haider raza": "s h raza",
    "maqbool fida husain": "m f husain",
    "francis newton souza": "f n souza",
    "vasudeo s gaitonde": "v s gaitonde",
    "vasudeo santu gaitonde": "v s gaitonde",
    "vs gaitonde": "v s gaitonde",
    "tyeb mehta": "tyeb mehta",
    "amrita shergil": "amrita sher-gil",
    "amrita sher gil": "amrita sher-gil",
    "bhupen khakhar": "bhupen khakhar",
    "nasreen mohamedi": "nasreen mohamedi",
    "akbar padamsee": "akbar padamsee",
    "krishen khanna": "krishen khanna",
    "ram kumar": "ram kumar",
    "jehangir sabavala": "jehangir sabavala",
    "ganesh pyne": "ganesh pyne",
    "jamini roy": "jamini roy",
    "nandalal bose": "nandalal bose",
    "raja ravi varma": "raja ravi varma",
    "nicholas roerich": "nicholas roerich",
    "subodh gupta": "subodh gupta",
    "atul dodiya": "atul dodiya",
    "anjolie ela menon": "anjolie ela menon",
    "satish gujral": "satish gujral",
    "k h ara": "k h ara",
    "kh ara": "k h ara",
    "j swaminathan": "j swaminathan",
    "jagdish swaminathan": "j swaminathan",
    "b prabha": "b prabha",
    "sakti burman": "sakti burman",
    "hemendranath mazumdar": "hemendranath mazumdar",
    "t vaikuntam": "t vaikuntam",
    "thota vaikuntam": "t vaikuntam",
    # Pundole's catalogues the full legal name where the trade abbreviates.
    "vasudev s gaitonde": "v s gaitonde",
    "vasudeo s gaitonde": "v s gaitonde",
    "kattingeri krishna hebbar": "k k hebbar",
    "krishnaji howlaji ara": "k h ara",
    "narayan shridhar bendre": "n s bendre",
    "hari ambadas gade": "h a gade",
    "mahadev viswanath dhurandhar": "m v dhurandhar",
    "abdulrahim apabhai almelkar": "a a almelkar",
    "laxman narayan taskar": "l n taskar",
    "abdul aziz raiba": "a a raiba",
    "gopal ghose": "gopal ghose",
    "sailoz mookherjea": "sailoz mukherjea",
}

_PUNCT = re.compile(r"[.’'`,]")
_SPACE = re.compile(r"\s+")
_STRIP = re.compile(r"\b(shri|sri|mr|dr|late)\b")


def normalise_artist(raw):
    """Return (canonical_key, display_name).

    canonical_key is what we join on; display_name is what the desk shows.
    """
    if not raw:
        return None, None
    display = _SPACE.sub(" ", str(raw).strip())

    key = display.lower()
    key = _PUNCT.sub("", key)          # "S. H. Raza" -> "s h raza"
    key = key.replace("-", "-")
    key = _STRIP.sub("", key)
    key = _SPACE.sub(" ", key).strip()

    # collapse glued initials: "sh raza" -> "s h raza"
    parts = key.split(" ")
    if parts and len(parts[0]) == 2 and parts[0].isalpha() and len(parts) > 1:
        parts = [parts[0][0], parts[0][1]] + parts[1:]
        key = " ".join(parts)

    key = ALIASES.get(key, key)
    return key, display


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS sale (
  id           TEXT PRIMARY KEY,   -- 'astaguru:220'
  house        TEXT NOT NULL,
  name         TEXT,
  start_date   TEXT,               -- ISO
  end_date     TEXT,
  url          TEXT,
  lot_count    INTEGER,
  sold_count   INTEGER,          -- only Pundole's exposes the unsold set
  fetched_at   TEXT
);

CREATE TABLE IF NOT EXISTS artist (
  key          TEXT PRIMARY KEY,   -- canonical, e.g. 's h raza'
  display      TEXT NOT NULL,
  house_ids    TEXT                -- JSON: {"astaguru": "<uuid>"}
);

CREATE TABLE IF NOT EXISTS lot (
  id               TEXT PRIMARY KEY,  -- 'astaguru:220:14'
  sale_id          TEXT NOT NULL,
  house            TEXT NOT NULL,
  lot_no           TEXT,
  sale_date        TEXT,
  artist_key       TEXT,
  artist_raw       TEXT,
  title            TEXT,
  medium           TEXT,
  size             TEXT,
  year             TEXT,
  category         TEXT,
  est_low_inr      INTEGER,
  est_high_inr     INTEGER,
  hammer_inr       INTEGER,          -- fall of hammer, before premium
  price_inr        INTEGER,          -- inclusive of buyer's premium ("sold for")
  price_usd        INTEGER,
  currency         TEXT,             -- sale currency; NULL means INR
  est_low_native   INTEGER,          -- the figures as the house printed them,
  est_high_native  INTEGER,          -- in `currency`; *_inr are converted at
  price_native     INTEGER,          -- the sale-date rate (see fx_rate)
  sold             INTEGER,          -- 1 sold, 0 bought in
  bid_count        INTEGER,
  non_exportable   INTEGER,          -- National Art Treasure
  premium_pct      REAL,
  provenance       TEXT,
  notes            TEXT,
  image_url        TEXT,
  url              TEXT,
  FOREIGN KEY (sale_id) REFERENCES sale(id)
);

CREATE INDEX IF NOT EXISTS idx_lot_artist ON lot(artist_key);
CREATE INDEX IF NOT EXISTS idx_lot_date   ON lot(sale_date);
CREATE INDEX IF NOT EXISTS idx_lot_house  ON lot(house);

CREATE TABLE IF NOT EXISTS fx (
  day    TEXT NOT NULL,
  pair   TEXT NOT NULL,
  rate   REAL NOT NULL,
  PRIMARY KEY (day, pair)
);
"""


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.row_factory = sqlite3.Row
    # WAL lets one ingester write while another reads; the scheduled refresh will
    # run several of these at once and plain journal mode deadlocks them.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    # added after the first two houses shipped; CREATE TABLE IF NOT EXISTS will
    # not backfill a column onto an existing table
    cols = {r[1] for r in con.execute("PRAGMA table_info(sale)")}
    if "sold_count" not in cols:
        con.execute("ALTER TABLE sale ADD COLUMN sold_count INTEGER")
    cols = {r[1] for r in con.execute("PRAGMA table_info(lot)")}
    for c, t in (("currency", "TEXT"), ("est_low_native", "INTEGER"),
                 ("est_high_native", "INTEGER"), ("price_native", "INTEGER")):
        if c not in cols:
            con.execute(f"ALTER TABLE lot ADD COLUMN {c} {t}")
    return con


# --------------------------------------------------------------------------
# FX — the overseas houses sell in USD and GBP
# --------------------------------------------------------------------------

FX_HIST = "https://api.frankfurter.app/{day}?from={cur}&to=INR,USD"


def fx_rate(con, day, cur):
    """INR and USD per one unit of `cur` on `day`, cached in the fx table.

    Frankfurter is ECB reference data, keyless, back to 1999. A weekend or
    holiday answers with the previous business day's fix, which is what a
    saleroom would have used too; it is stored under the day asked for.
    """
    if cur == "INR":
        return 1.0, None
    rows = {r["pair"]: r["rate"] for r in con.execute(
        "SELECT pair, rate FROM fx WHERE day=? AND pair IN (?,?)",
        (day, f"{cur}/INR", f"{cur}/USD"))}
    if f"{cur}/INR" not in rows:
        d = get(FX_HIST.format(day=day, cur=cur), pause=0.2)
        rows = {f"{cur}/INR": d["rates"]["INR"], f"{cur}/USD": d["rates"].get("USD", 1.0)}
        con.executemany("INSERT OR REPLACE INTO fx (day, pair, rate) VALUES (?,?,?)",
                        [(day, k, v) for k, v in rows.items()])
    return rows[f"{cur}/INR"], rows.get(f"{cur}/USD")


def to_inr(amount, rate):
    return int(round(amount * rate)) if amount else None


# --------------------------------------------------------------------------
# Catalogue text — shared by the houses that print a blurb, not fields
# --------------------------------------------------------------------------

# "JAMINI ROY (1887-1972)" / "SUBODH GUPTA (B. 1964)" / "Ahmad X (Iran, born 1973)"
_ARTIST = re.compile(
    r"^(.*?)\s*\((?:[A-Za-z ]+,\s*)?(?:b(?:orn)?\.?\s*)?(\d{4})\s*[-–]?\s*(\d{4})?\)\s*$", re.I)
# Dimensions read "56 1/2 x 20 1/2 in. (144.2 x 52.1 cm.)". The fractional inches
# are a trap: a naive \d+ matches the "2" of "1/2" and yields "2 x 58 in". The cm
# pair is always plain decimal, so parse that and convert.
_DIM_CM = re.compile(r"(\d+(?:\.\d+)?)\s*[x\u00d7]\s*(\d+(?:\.\d+)?)\s*cm")
_FRAC = r"\d+(?:\s+\d+/\d+)?(?:\.\d+)?"
_DIM_IN = re.compile("(%s)\\s*[x\\u00d7]\\s*(%s)\\s*in\\b" % (_FRAC, _FRAC))
_MEDIUM_HINT = re.compile(
    r"\b(oil|acrylic|watercolours?|watercolors?|gouache|tempera|ink|pencil|charcoal|pastels?|"
    r"mixed media|serigraph|lithograph|etching|photograph|bronze|terracotta|marble|"
    r"wood|steel|gelatin|silver|collage|casein|enamel|fibreglass|fiberglass|graphite|"
    r"crayon|woodcut|linocut|screenprint|print|ballpoint|dye|paper|canvas|board)\b", re.I)
# "Executed in 1953" / "Painted circa 1882" / "signed and dated 'Sabavala '66'" /
# "signed 'Qadri 07'". A two-digit year must follow an apostrophe, or the
# dimensions two lines down ("72.9 x 91.7cm") would read as 1972.
_YEAR = re.compile(r"\b(?:painted|executed|dated|circa|c\.)\b[^\d]{0,60}?(?:\b((?:1[89]|20)\d\d)\b|'(\d\d)\b)", re.I)


def _num(t):
    """'7 7/8' -> 7.875, '24' -> 24.0"""
    t = t.strip()
    m = re.match(r"^(\d+)\s+(\d+)/(\d+)$", t)
    if m:
        return float(m.group(1)) + float(m.group(2)) / float(m.group(3))
    try:
        return float(t)
    except ValueError:
        return None


def split_artist(raw):
    """'JAMINI ROY (1887-1972)' -> ('Jamini Roy', '1887–1972')."""
    if not raw:
        return None, None
    s = re.sub(r"\s+", " ", htmllib.unescape(str(raw))).strip()
    m = _ARTIST.match(s)
    if not m:
        return s.title() if s.isupper() else s, None
    name = m.group(1).strip()
    name = name.title() if name.isupper() else name
    years = f"{m.group(2)}–{m.group(3)}" if m.group(3) else f"b. {m.group(2)}"
    return name, years


def parse_description(desc):
    """Pull medium and inch dimensions out of the catalogue blurb."""
    if not desc:
        return None, None
    text = re.sub(r"<br\s*/?>|</(?:div|p|li)>", "\n", htmllib.unescape(desc))
    text = re.sub(r"<[^>]+>", " ", text)
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.split("\n")]
    lines = [l for l in lines if l]

    medium = next((l for l in lines if _MEDIUM_HINT.search(l) and len(l) < 90), None)
    size = None
    cm = _DIM_CM.search(text)
    if cm:
        a, b = float(cm.group(1)) / 2.54, float(cm.group(2)) / 2.54
        size = "%.1f x %.1f in" % (a, b)
    else:
        d = _DIM_IN.search(text)
        if d:
            a, b = _num(d.group(1)), _num(d.group(2))
            if a and b:
                size = "%.1f x %.1f in" % (a, b)
    return medium, size


def year_from(text):
    """'Executed in 1953' / 'Painted circa 1882' / "dated '53" -> '1953'. None if unsaid."""
    if not text:
        return None
    m = _YEAR.search(text)
    if not m:
        return None
    if m.group(1):
        return m.group(1)
    yy = int(m.group(2))
    return str((2000 if yy <= date.today().year % 100 else 1900) + yy)


def upsert_artist(con, key, display, house, house_id=None):
    if not key:
        return
    row = con.execute("SELECT display, house_ids FROM artist WHERE key=?", (key,)).fetchone()
    ids = json.loads(row["house_ids"]) if row and row["house_ids"] else {}
    if house_id:
        ids[house] = house_id
    if row:
        # Prefer the spelling that matches the canonical key. The trade says
        # "M F Husain", not "Maqbool Fida Husain", and an ALIASES entry folding
        # the full name in must not drag the display name along with it.
        def _n(x):
            return _SPACE.sub(" ", _PUNCT.sub("", x.lower())).strip()
        cur, incoming = row["display"], display
        if _n(cur) == key and _n(incoming) != key:
            best = cur
        elif _n(incoming) == key and _n(cur) != key:
            best = incoming
        else:
            best = cur if len(cur) >= len(incoming) else incoming
        con.execute("UPDATE artist SET display=?, house_ids=? WHERE key=?",
                    (best, json.dumps(ids), key))
    else:
        con.execute("INSERT INTO artist (key, display, house_ids) VALUES (?,?,?)",
                    (key, display, json.dumps(ids)))
