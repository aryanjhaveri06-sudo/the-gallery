"""Inject data/app_data.json into the app HTML, between the REAL_DATA markers.

A published Artifact cannot fetch across origins, so the dataset has to travel
inside the page. This keeps that step repeatable: rebuild, re-export, re-embed.

Usage:  python3 ingest/embed_app.py [--html PATH]
"""

import argparse
import sys
from pathlib import Path

from common import ROOT

START = "/* REAL_DATA_START */"
END = "/* REAL_DATA_END */"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default=str(ROOT / "index.html"))
    ap.add_argument("--data", default=str(ROOT / "data" / "app_data.json"))
    args = ap.parse_args()

    html = Path(args.html).read_text(encoding="utf-8")
    data = Path(args.data).read_text(encoding="utf-8").strip()

    i, j = html.find(START), html.find(END)
    if i < 0 or j < 0:
        sys.exit(f"markers not found in {args.html}")

    # `let`, not `const`: loadLiveData() reassigns REAL when the fetched
    # data/app_data.json is newer than this embed. With `const` that assignment
    # threw, the throw was swallowed by loadLiveData's own try/catch, and the
    # app silently ran on the embedded copy forever.
    block = f"{START}\nlet REAL = {data};\n"
    Path(args.html).write_text(html[:i] + block + html[j:], encoding="utf-8")
    print(f"embedded {len(data) / 1024:.0f} KB into {args.html}")


if __name__ == "__main__":
    sys.exit(main())
