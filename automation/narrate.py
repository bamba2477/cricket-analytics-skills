#!/usr/bin/env python3
"""
Optional: add an AI-written match story to each generated report.

Reads the <report>.json files that batch_reports.py writes, sends the computed
stats plus the skill's instructions to Claude, and inserts a short narrative at
the top of the matching .md report. Reports that already have a story are
skipped.

Requires ANTHROPIC_API_KEY. Without it, the script exits quietly, so the
scheduled workflow still produces stats-only reports.

  ANTHROPIC_API_KEY=... python automation/narrate.py reports/
  CLAUDE_MODEL=claude-haiku-4-5 python automation/narrate.py reports/   # cheaper

Standard library only (calls the Messages API over HTTPS).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "plugins" / "cricket-analytics" / "skills" / "cricket-match-report" / "SKILL.md"
MARKER = "<!-- narrative -->"
API_URL = "https://api.anthropic.com/v1/messages"

PROMPT = """Here is the computed JSON for one cricket match. Write ONLY the
"Headline" and "The story" parts of the report described in your instructions:
one bold headline sentence, then 2-4 short paragraphs. Use only numbers present
in the JSON. No scorecards, no top-performers table, no footer, no preamble.

<match_json>
{data}
</match_json>"""


def call_claude(system: str, user: str, model: str, key: str) -> str:
    body = json.dumps({
        "model": model,
        "max_tokens": 900,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        out = json.loads(resp.read())
    return "".join(b.get("text", "") for b in out.get("content", [])).strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Add Claude-written narratives to match reports.")
    ap.add_argument("reports_dir", type=Path)
    ap.add_argument("--limit", type=int, default=20, help="max reports to narrate per run")
    args = ap.parse_args(argv)

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("ANTHROPIC_API_KEY not set; skipping narratives (stats-only reports kept).")
        return 0
    model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
    system = SKILL.read_text(encoding="utf-8")

    done = 0
    for j in sorted(args.reports_dir.glob("*.json"), reverse=True):
        md = j.with_suffix(".md")
        if not md.exists() or MARKER in md.read_text(encoding="utf-8"):
            continue
        report = json.loads(j.read_text(encoding="utf-8"))
        if report.get("checks"):
            print(f"  skip {md.name}: data consistency warnings")
            continue
        # Over-by-over detail is large and the story doesn't need it.
        slim = {**report, "innings": [{k: v for k, v in inn.items() if k != "over_by_over"}
                                      for inn in report["innings"]]}
        try:
            story = call_claude(system, PROMPT.format(data=json.dumps(slim)), model, key)
        except urllib.error.HTTPError as e:
            print(f"  API error for {md.name}: {e.code} {e.read()[:200]!r}")
            return 1
        lines = md.read_text(encoding="utf-8").split("\n")
        # Insert after the title block (first blank line following the header lines).
        at = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
        lines[at:at] = [MARKER, story, ""]
        md.write_text("\n".join(lines), encoding="utf-8")
        print(f"  narrated {md.name}")
        done += 1
        if done >= args.limit:
            break
    print(f"Narratives added: {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
