#!/usr/bin/env python3
"""
Download ball-by-ball match files from Cricsheet (https://cricsheet.org).

Cricsheet publishes free JSON match data under the Open Data Commons
Attribution License (ODC-By). Credit Cricsheet wherever you publish results.

Usage:
  python fetch_cricsheet.py --list                      # show known datasets
  python fetch_cricsheet.py recently_added_7 -o data/   # last 7 days, all formats
  python fetch_cricsheet.py ipl -o data/ipl
  python fetch_cricsheet.py t20s --since 2026-01-01 -o data/t20i
  python fetch_cricsheet.py t20s --event "Asian Games" -o data/asian-games

There's no download per tournament for international events such as the
Asian Games or a World Cup; download the format's archive and use --event,
which keeps matches whose event name contains that text (any case).

Datasets are zip archives named <dataset>_json.zip on Cricsheet's downloads
page; any name listed there works, not only the ones below.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://cricsheet.org/downloads/{name}_json.zip"
KNOWN = {
    "recently_added_2": "Matches added in the last 2 days (all formats)",
    "recently_added_7": "Matches added in the last 7 days (all formats)",
    "recently_added_30": "Matches added in the last 30 days (all formats)",
    "ipl": "Indian Premier League",
    "t20s": "T20 internationals, men's and women's",
    "t20s_male": "Men's T20 internationals",
    "t20s_female": "Women's T20 internationals",
    "odis": "One-day internationals",
    "tests": "Test matches",
    "wpl": "Women's Premier League",
    "all": "Every match on Cricsheet (large download)",
}


def event_name(info: dict) -> str:
    ev = info.get("event")
    return (ev.get("name", "") if isinstance(ev, dict) else str(ev or ""))


def fetch(name: str, out_dir: Path, since: str | None, match_type: str | None,
          event: str | None = None) -> int:
    url = BASE.format(name=name)
    print(f"Downloading {url} ...", file=sys.stderr)
    req = urllib.request.Request(url, headers={"User-Agent": "cricket-analytics-skills/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = resp.read()
        final_url, ctype, status = resp.geturl(), resp.headers.get("Content-Type", "?"), resp.status

    if not zipfile.is_zipfile(io.BytesIO(payload)):
        snippet = payload[:300].decode("utf-8", errors="replace").replace("\n", " ")
        hint = ("The dataset name may be wrong; run with --list or check "
                "https://cricsheet.org/downloads/."
                if final_url.rstrip("/") != url.rstrip("/") or b"404" in payload[:2000]
                else "Cricsheet may be blocking automated downloads from this network "
                     "(common for cloud servers such as GitHub Actions).")
        raise RuntimeError(
            f"expected a zip file but got {len(payload)} bytes of {ctype} "
            f"(HTTP {status}, from {final_url}).\n  Start of response: {snippet[:200]}\n  {hint}")

    out_dir.mkdir(parents=True, exist_ok=True)
    kept = 0
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        for member in zf.namelist():
            # Only flat *.json files; ignore READMEs and anything with a path component.
            if not member.endswith(".json") or "/" in member or "\\" in member:
                continue
            raw = zf.read(member)
            if since or match_type or event:
                try:
                    info = json.loads(raw).get("info", {})
                except json.JSONDecodeError:
                    continue
                first_date = (info.get("dates") or [""])[0]
                if since and first_date < since:
                    continue
                if match_type and info.get("match_type") != match_type:
                    continue
                if event and event.lower() not in event_name(info).lower():
                    continue
            (out_dir / member).write_bytes(raw)
            kept += 1
    print(f"Saved {kept} match files to {out_dir}", file=sys.stderr)
    return kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Download Cricsheet JSON match data.")
    ap.add_argument("dataset", nargs="?", help="dataset name, e.g. ipl, t20s, recently_added_7")
    ap.add_argument("-o", "--out", type=Path, default=Path("data"))
    ap.add_argument("--since", help="keep matches starting on/after YYYY-MM-DD")
    ap.add_argument("--match-type", help="keep only this match_type, e.g. T20, ODI, Test")
    ap.add_argument("--event", help='keep matches whose event name contains this, e.g. "Asian Games"')
    ap.add_argument("--list", action="store_true", help="list common dataset names")
    args = ap.parse_args(argv)

    if args.list or not args.dataset:
        for k, v in KNOWN.items():
            print(f"  {k:<20} {v}")
        print("\nFull list: https://cricsheet.org/downloads/")
        return 0
    try:
        fetch(args.dataset, args.out, args.since, args.match_type, args.event)
    except Exception as e:  # network errors, 404 for unknown dataset names, bad zips
        print(f"Download failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
