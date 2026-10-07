#!/usr/bin/env python3
"""
Generate match reports for every Cricsheet file in a folder.

Deterministic and free to run: no API key needed. Used by the scheduled
GitHub Actions workflow, and handy locally:

  python automation/batch_reports.py data/ -o reports/
  python automation/batch_reports.py data/ -o reports/ --since 2026-09-01 --match-type T20

Writes one markdown report per match plus reports/index.md, newest first.
Matches whose report already exists are skipped, so reruns are cheap.
Also writes <report>.json next to each report for the optional narrative step.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "cricket-analytics" / "skills" / "cricket-match-report" / "scripts"))
from match_report import build_report, to_markdown  # noqa: E402


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Batch-generate cricket match reports.")
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("reports"))
    ap.add_argument("--since", help="only matches starting on/after YYYY-MM-DD")
    ap.add_argument("--match-type", help="only this match_type (T20, ODI, Test, ...)")
    ap.add_argument("--force", action="store_true", help="regenerate existing reports")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    made, skipped, failed = 0, 0, []
    for f in sorted(args.data_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            info = data["info"]
        except (json.JSONDecodeError, KeyError):
            continue
        date = (info.get("dates") or ["unknown"])[0]
        if args.since and date < args.since:
            continue
        if args.match_type and info.get("match_type") != args.match_type:
            continue
        name = f"{date}_{slug(' v '.join(info.get('teams', [f.stem])))}_{f.stem}"
        md_path = args.out / f"{name}.md"
        if md_path.exists() and not args.force:
            skipped += 1
            continue
        report = build_report(data)
        if report["checks"]:
            failed.append((f.name, report["checks"]))
        md_path.write_text(to_markdown(report), encoding="utf-8")
        (args.out / f"{name}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
        made += 1

    # Rebuild the index from every report on disk, newest first.
    rows = []
    for j in sorted(args.out.glob("*.json"), reverse=True):
        try:
            r = json.loads(j.read_text(encoding="utf-8"))["match"]
        except (json.JSONDecodeError, KeyError):
            continue
        date = (r.get("dates") or ["?"])[0]
        title = " vs ".join(r.get("teams", []))
        rows.append(f"| {date} | [{title}]({j.with_suffix('.md').name}) | "
                    f"{r.get('match_type', '')} | {r.get('event') or ''} | {r.get('result')} |")
    index = ["# Match reports", "",
             "| Date | Match | Format | Event | Result |", "|---|---|---|---|---|", *rows, "",
             "*Data: [Cricsheet](https://cricsheet.org) (ODC-By).*"]
    (args.out / "index.md").write_text("\n".join(index), encoding="utf-8")

    print(f"Reports: {made} new, {skipped} already existed, {len(failed)} with data warnings.")
    for name, checks in failed:
        print(f"  ⚠ {name}: {'; '.join(checks)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
