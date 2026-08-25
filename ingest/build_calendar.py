"""Publish the auction diary as calendar.ics, for subscribing to.

This is the direction that works on a public host: the sale dates go *out* into
her own Calendar app. Her meetings do not come in — that would mean putting a
private diary on a world-readable site, so it belongs with the CRM on a private
backend instead.

Subscribing (iPad / iPhone):
  Settings -> Calendar -> Accounts -> Add Account -> Other
  -> Add Subscribed Calendar -> the calendar.ics URL

Apple and Google both re-poll a subscribed feed on their own schedule, so the
nightly refresh reaches her calendar without her doing anything.

Usage:  python3 ingest/build_calendar.py [--out FILE]
"""

import argparse
import sys
from datetime import date, datetime, timedelta, timezone

from common import ROOT, connect

PRODID = "-//The Gallery//Auction Diary//EN"
CAL_NAME = "The Gallery — Auctions"


def fold(line):
    """RFC 5545 caps a content line at 75 octets; continuations start with a space."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode("utf-8")) > 74:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n".join(out)


def esc(text):
    """Backslash, semicolon, comma and newline are the reserved characters."""
    return (str(text or "")
            .replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "calendar.ics"))
    args = ap.parse_args()

    con = connect()
    try:
        rows = con.execute(
            "SELECT * FROM event WHERE starts >= ? ORDER BY starts",
            (date.today().isoformat(),)).fetchall()
    except Exception:
        rows = []                       # event table not built yet

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    out = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{CAL_NAME}",
        "X-WR-TIMEZONE:Asia/Kolkata",
        # Both Apple and Google honour one of these two hints for poll frequency.
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]

    for r in rows:
        start = datetime.strptime(r["starts"], "%Y-%m-%d").date()
        # All-day events are half-open in iCalendar: DTEND is the morning after.
        end = datetime.strptime(r["ends"] or r["starts"], "%Y-%m-%d").date()
        dtend = end + timedelta(days=1)

        desc = []
        if r["kind"]:
            desc.append(r["kind"])
        if r["lot_count"]:
            desc.append(f"{r['lot_count']} lots")
        if r["url"]:
            desc.append(r["url"])

        out += [
            "BEGIN:VEVENT",
            fold(f"UID:{esc(r['id'])}@the-gallery"),
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
            f"DTEND;VALUE=DATE:{dtend:%Y%m%d}",
            fold(f"SUMMARY:{esc(r['house'])} — {esc(r['title'])}"),
            fold(f"DESCRIPTION:{esc(' · '.join(desc))}"),
        ]
        if r["city"]:
            out.append(fold(f"LOCATION:{esc(r['city'])}"))
        if r["url"]:
            out.append(fold(f"URL:{esc(r['url'])}"))
        out += [
            "TRANSP:TRANSPARENT",        # a sale should not block her as "busy"
            "BEGIN:VALARM",              # a day's notice, which is what a desk wants
            "TRIGGER:-P1D",
            "ACTION:DISPLAY",
            fold(f"DESCRIPTION:{esc(r['title'])} tomorrow"),
            "END:VALARM",
            "END:VEVENT",
        ]

    out.append("END:VCALENDAR")
    text = "\r\n".join(out) + "\r\n"

    with open(args.out, "w", newline="") as f:
        f.write(text)
    print(f"{len(rows)} events -> {args.out} ({len(text)} bytes)")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
