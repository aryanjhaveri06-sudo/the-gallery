"""The morning roundup — Aashna's art news, on Telegram, every day at 08:00 IST.

One message: what is on the market (Indian art headlines from the last two
days), the sales in her diary this week, and a few stories from further
afield. Every headline is a link. It is built from what the desk already
holds — nothing is re-fetched from publishers here — so it can never say
something the desk does not:

* `data/news.json`      — the nightly Google-sourced layer (committed ~02:30 IST)
* `/api/news`           — the live publisher layer on the private site (15-min cache)
* `data/app_data.json`  — the diary (`events`) for "this week"

Both news layers are needed; neither alone is enough (see news.py). Stories
are deduped on URL and on headline, because the same story arrives under a
news.google.com link and a publisher link.

Telegram: HTML parse mode, previews off (one message, not eight cards),
4096-character cap respected by trimming items, never by cutting mid-link.
Sends to every chat id in TELEGRAM_ROUNDUP_CHAT_IDS (comma-separated) — hers,
and his if he wants a copy. A send failure is reported, not swallowed.

Usage:  python3 ingest/roundup.py [--dry-run] [--days 2]
Env:    TELEGRAM_BOT_TOKEN, TELEGRAM_ROUNDUP_CHAT_IDS
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from common import ROOT, get

LIVE_NEWS = "https://the-gallery-ct1.pages.dev/api/news"
MARKET_N = 7
WIDER_N = 3
CAP = 4000

IST = timezone(timedelta(hours=5, minutes=30))


def norm(h):
    return re.sub(r"[^a-z0-9]+", " ", (h or "").lower()).strip()


def merge(*lists):
    """Newest first, one copy of each story."""
    seen_url, seen_head, out = set(), set(), []
    for items in lists:
        for it in items or []:
            u = (it.get("url") or "").split("?")[0].rstrip("/")
            h = norm(it.get("headline"))
            if not h or u in seen_url or h in seen_head:
                continue
            seen_url.add(u)
            seen_head.add(h)
            out.append(it)
    out.sort(key=lambda it: it.get("date") or "", reverse=True)
    return out


HOUSES = re.compile(r"saffronart|astaguru|pundole|christie|sotheby|bonhams|storyltd|prinseps", re.I)
MONEY = re.compile(r"auction|sale|sold|record|crore|lakh|₹|\$|million|estimate|hammer|bid", re.I)


def ranker(artists):
    """Headlines that name a house, a tracked artist or a price come first.

    The desk's filter lets an occasional stray through ("the Art of Mindful
    Living"); tightening it has cost real headlines twice, so the roundup
    ranks instead. On a busy day the strays fall off the bottom; on a quiet
    one they are still art news from India, which is what was asked for.
    """
    surnames = {n.split()[-1].lower() for n in artists if len(n.split()[-1]) >= 4}
    surnames -= {"khan", "singh", "kumar", "shah", "roy", "bose", "das", "sen"}

    def score(it):
        h = it.get("headline") or ""
        words = set(re.findall(r"[a-z]+", h.lower()))
        return (2 * bool(HOUSES.search(h)) + 2 * bool(words & surnames) + bool(MONEY.search(h)),
                it.get("date") or "")
    return score


def inr(n):
    """₹5.37 cr / ₹48 lakh, the trade's units, for a Telegram line."""
    if not n:
        return None
    if n >= 1e7:
        v = n / 1e7
        return f"\u20b9{v:.2f} cr" if v < 10 else f"\u20b9{v:.1f} cr"
    if n >= 1e5:
        v = n / 1e5
        return f"\u20b9{v:.1f} lakh" if v < 10 else f"\u20b9{v:.0f} lakh"
    return f"\u20b9{n:,}"


def alert_lines(since_iso):
    """What her watches fired since the last roundup (data/alerts.json)."""
    try:
        d = json.loads((ROOT / "data" / "alerts.json").read_text())
    except (OSError, ValueError):
        return []
    out = []
    for it in d.get("items", []):
        if (it.get("at") or "") < since_iso:
            continue
        who = esc(it["artist"])
        what = esc(it.get("title") or "Untitled")
        est = f"{inr(it['est_low'])}\u2013{inr(it['est_high'])}" if it.get("est_low") and it.get("est_high") else None
        native = f" ({esc(it['currency'])} {it['est_low_native']:,}\u2013{it['est_high_native']:,})" \
            if it.get("currency") and it["currency"] != "INR" and it.get("est_low_native") else ""
        link = f"<a href=\"{esc(it['url'])}\">{what}</a>" if it.get("url") else what
        when = fmt_day(it["date"]) if it.get("date") else ""
        if it["kind"] == "upcoming":
            out.append(f"\u2022 <b>{who}</b> \u2014 {link} \u00b7 {esc(it['house'])}, {when}, lot {esc(str(it.get('lot') or ''))}"
                       + (f" \u00b7 est. {est}{native}" if est else ""))
        else:
            price = f"<b>sold {inr(it['price'])}</b>" if it.get("sold") and it.get("price") else "<b>unsold</b>"
            out.append(f"\u2022 <b>{who}</b> \u2014 {link} \u00b7 {price} at {esc(it['house'])}, {when}"
                       + (f" \u00b7 est. {est}" if est else ""))
    return out


def load():
    nightly = json.loads((ROOT / "data" / "news.json").read_text())
    try:
        live = get(LIVE_NEWS, pause=0)
    except Exception as e:                       # the live layer is a bonus, not a dependency
        print(f"live news unavailable: {e}", file=sys.stderr)
        live = {}
    app = json.loads((ROOT / "data" / "app_data.json").read_text())
    rank = ranker(a.get("name", "") for a in (app.get("artists") or {}).values())
    market = merge(live.get("on_market"), nightly.get("on_market"))
    return (market, merge(nightly.get("wider")), app.get("events") or [], rank)


def esc(s):
    return html.escape(s or "", quote=False)


def item(it):
    src = f" — {esc(it['source'])}" if it.get("source") else ""
    return f"• <a href=\"{esc(it['url'])}\">{esc(it['headline'])}</a>{src}"


def fmt_day(iso):
    return datetime.fromisoformat(iso).strftime("%a %-d %b")


def build(market, wider, events, today, days, rank):
    since = (today - timedelta(days=days)).isoformat()
    fresh = [it for it in market if (it.get("date") or "") >= since]
    if len(fresh) < 3:                            # a quiet weekend: reach back rather than send three lines
        fresh = list(market)
    fresh = sorted(fresh, key=rank, reverse=True)[:MARKET_N]

    week_end = (today + timedelta(days=7)).isoformat()
    this_week = sorted((e for e in events
                        if e.get("starts") and (e.get("ends") or e["starts"]) >= today.isoformat()
                        and e["starts"] <= week_end),
                       key=lambda e: e["starts"])

    head = f"<b>AG NEWSROOM</b>\n{today.strftime('%A %-d %B %Y')}"
    parts = [head]

    alerts = alert_lines((datetime.now(timezone.utc) - timedelta(hours=26)).isoformat(timespec="seconds"))
    if alerts:
        more = f"\n<i>and {len(alerts) - 15} more on the desk</i>" if len(alerts) > 15 else ""
        parts.append("<b>YOUR ALERTS</b>\n" + "\n".join(alerts[:15]) + more)

    if fresh:
        parts.append("<b>ON THE MARKET</b>\n" + "\n".join(item(it) for it in fresh))

    if this_week:
        rows = []
        for e in this_week:
            when = fmt_day(e["starts"]) + (f"–{fmt_day(e['ends'])}" if e.get("ends") and e["ends"] != e["starts"] else "")
            title = esc(e.get("title") or "")
            if e.get("url"):
                title = f"<a href=\"{esc(e['url'])}\">{title}</a>"
            rows.append(f"• {when} · <b>{esc(e.get('house') or '')}</b> — {title}")
        parts.append("<b>SALES THIS WEEK</b>\n" + "\n".join(rows))

    if wider[:WIDER_N]:
        parts.append("<b>FURTHER AFIELD</b>\n" + "\n".join(item(it) for it in wider[:WIDER_N]))

    parts.append("<i>From the desk's own record. This bot only sends; replies are not read.</i>")

    text = "\n\n".join(parts)
    # Trim whole items from the bottom of the market list until it fits.
    if len(text) > CAP and MARKET_N > 3:
        shorter = [it for it in market if it in fresh[:-1]]
        return build(shorter, wider, events, today, days, rank)
    return text


def send(token, chat_id, text):
    body = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body)
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())
    if not d.get("ok"):
        raise RuntimeError(d.get("description") or "telegram refused the message")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the message, send nothing")
    ap.add_argument("--days", type=int, default=2)
    args = ap.parse_args()

    market, wider, events, rank = load()
    today = datetime.now(IST).date()
    text = build(market, wider, events, today, args.days, rank)

    if args.dry_run:
        print(text)
        print(f"\n[{len(text)} chars, {len(market)} market stories held, {len(wider)} wider]")
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chats = [c.strip() for c in os.environ.get("TELEGRAM_ROUNDUP_CHAT_IDS", "").split(",") if c.strip()]
    if not token or not chats:
        print("::error::TELEGRAM_BOT_TOKEN and TELEGRAM_ROUNDUP_CHAT_IDS must both be set")
        return 1
    failed = 0
    for c in chats:
        try:
            send(token, c, text)
            print(f"sent to {c}")
        except Exception as e:
            failed += 1
            print(f"::error::send to {c} failed: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
