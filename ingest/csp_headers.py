"""Write the `_headers` file Cloudflare Pages serves with every response.

The important one is the Content-Security-Policy. The page is one inline
script, so the policy names that script by its SHA-256 and nothing else may
run: not a script injected through a headline, an image URL, a client note
or a house feed — the browser refuses it. The ten inline `on…=` handlers are
allowed the same way, each by its own hash ('unsafe-hashes'). Styles stay
'unsafe-inline' (hundreds of style attributes, no script risk). Images may
come from anywhere — the publishers and the houses are many and change —
but connections may only go to this origin and the private site (the public
copy reads news across).

Generated at deploy, never committed: a code push changes the script hash,
and a stale hash would blank the site. deploy.yml and refresh.yml both run
this immediately before `wrangler pages deploy`. It checks itself: the hash
it writes must be the hash of the script in the file it just read.

Usage:  python3 ingest/csp_headers.py [--check]
"""

import argparse
import base64
import hashlib
import re
import sys

from common import ROOT

INDEX = ROOT / "index.html"
OUT = ROOT / "_headers"

_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)
_HANDLER = re.compile(r'\bon[a-z]+="([^"]*)"')


def sha(s):
    return "'sha256-" + base64.b64encode(hashlib.sha256(s.encode("utf-8")).digest()).decode() + "'"


def policy(html):
    scripts = [sha(m.group(1)) for m in _SCRIPT.finditer(html)]
    handlers = sorted({sha(h) for h in _HANDLER.findall(html)})
    if not scripts:
        raise SystemExit("no inline script found — refusing to write a policy that would block everything")
    return "; ".join([
        "default-src 'self'",
        "script-src " + " ".join(scripts + ["'unsafe-hashes'"] + handlers),
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src * data: blob:",
        "connect-src 'self' https://the-gallery-ct1.pages.dev https://fonts.googleapis.com",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "upgrade-insecure-requests",
    ])


def render(html):
    return f"""/*
  Content-Security-Policy: {policy(html)}
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()
  Cross-Origin-Opener-Policy: same-origin

/api/*
  Cache-Control: no-store, private

/data/img/*
  Cache-Control: public, max-age=2592000, immutable
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify an existing _headers matches index.html")
    args = ap.parse_args()
    html = INDEX.read_text(encoding="utf-8")
    text = render(html)
    if args.check:
        ok = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        print("_headers matches index.html" if ok else "_headers is STALE")
        return 0 if ok else 1
    OUT.write_text(text, encoding="utf-8")
    n = len(_HANDLER.findall(html))
    print(f"_headers written: 1 script hash, {len(set(_HANDLER.findall(html)))} handler hashes ({n} handlers)")


if __name__ == "__main__":
    sys.exit(main())
