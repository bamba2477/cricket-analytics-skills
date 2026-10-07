#!/usr/bin/env python3
"""
Add an AI-written match story to each generated report.

Reads the <report>.json files that batch_reports.py writes, sends a compact
version of the computed stats to a language model, fact-checks the reply,
and inserts the story at the top of the matching .md report. Reports that
already have a story are skipped.

Providers (chosen automatically, or set NARRATOR=github|anthropic):
  - GitHub Models (free): used when GITHUB_TOKEN is set. Inside GitHub
    Actions this works with no setup beyond `permissions: models: read`.
    Locally, use a personal access token with the "Models" read permission.
    Model: NARRATOR_MODEL (default openai/gpt-4o-mini).
  - Anthropic Claude (paid, best quality): used when ANTHROPIC_API_KEY is set.
    Model: NARRATOR_MODEL (default claude-sonnet-5-5).
If neither is available, the script exits quietly and stats-only reports stay.

Fact-check: every number in the story must appear in the computed report.
A story that cites a number the data doesn't contain is retried once with
feedback, then discarded. This is what makes a small free model safe to use.

  python automation/narrate.py reports/
  NARRATOR=anthropic python automation/narrate.py reports/ --limit 5

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

MARKER = "<!-- narrative -->"
GITHUB_URL = "https://models.github.ai/inference/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

SYSTEM = """You write short, vivid cricket match reports from computed statistics.

Rules:
- Use ONLY numbers that appear in the JSON you are given. Never calculate new
  numbers (no differences, no balls remaining, no averages). Copy figures exactly.
- Don't mention anything the data can't show: shot types, pitch, weather,
  injuries, emotions, career records, previous matches.
- The data is real even if the players, teams or dates are unfamiliar to you.
  Squads change every season. Never question the data.
- Each innings is named after its batting team; its bowlers belong to
  "bowling_team".
- Write in plain English. Use players' names exactly as given."""

PROMPT = """Write the headline and story for this match:
- First line: one bold sentence with the result and the deciding factor.
- Then 2-3 short paragraphs on how the match was won and lost, citing specific
  overs, phases and players.
No headings, no tables, no preamble.

<match>
{data}
</match>"""

NUM = re.compile(r"\d+(?:\.\d+)?")
ALWAYS_OK = {str(n) for n in range(0, 11)}  # small counts ("two sixes", "3 wickets") are low-risk


# --------------------------------------------------------------------------- #
# Payload and fact-check
# --------------------------------------------------------------------------- #
def compact(report: dict) -> dict:
    """The story needs far less than the full report; keep it under free-tier limits."""
    out = {"match": {k: report["match"].get(k) for k in
                     ("teams", "dates", "venue", "event", "match_type", "toss", "result",
                      "player_of_match")},
           "top_performers": report["top_performers"],
           "turning_points": [p["detail"] for p in report["turning_points"]],
           "innings": []}
    for inn in report["innings"]:
        best = sorted(inn["partnerships"], key=lambda p: -p["runs"])[:2]
        out["innings"].append({
            "batting_team": inn["team"], "bowling_team": inn.get("bowling_team"),
            "super_over": inn["super_over"], "score": f"{inn['total']}/{inn['wickets']}",
            "overs": inn["overs"], "run_rate": inn["run_rate"], "target": inn["target"],
            "batting": [f"{b['name']} {b['runs']} ({b['balls']}) {b['dismissal']}"
                        for b in inn["batting"] if b["balls"] or b["runs"]],
            "bowling": [f"{b['name']} {b['wickets']}/{b['runs']} in {b['overs']} ov, econ {b['economy']}"
                        for b in inn["bowling"]],
            "phases": [f"{p['phase']} (overs {p['overs']}): {p['runs']}/{p['wickets']}, RR {p['run_rate']}"
                       for p in inn["phases"]],
            "fall_of_wickets": [f"{f['score']}-{f['wicket']} {f['player']} ({f['over']})"
                                for f in inn["fall_of_wickets"]],
            "best_partnerships": [f"{p['runs']} off {p['balls']} balls: {' & '.join(p['batters'])}"
                                  for p in best],
        })
    return out


def _norm(tok: str) -> str:
    return str(float(tok)).rstrip("0").rstrip(".") if "." in tok else str(int(tok))


def unsupported_numbers(story: str, report: dict) -> list[str]:
    """Numbers in the story that appear nowhere in the computed report."""
    allowed = {_norm(t) for t in NUM.findall(json.dumps(report))} | ALWAYS_OK
    bad = []
    for tok in NUM.findall(story):
        if _norm(tok) not in allowed and tok not in bad:
            bad.append(tok)
    return bad


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
def _post(url: str, body: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def call_github(messages: list, model: str, token: str) -> str:
    out = _post(GITHUB_URL,
                {"model": model, "max_tokens": 700, "temperature": 0.4,
                 "messages": [{"role": "system", "content": SYSTEM}, *messages]},
                {"authorization": f"Bearer {token}", "accept": "application/json"})
    return out["choices"][0]["message"]["content"].strip()


def call_anthropic(messages: list, model: str, key: str) -> str:
    out = _post(ANTHROPIC_URL,
                {"model": model, "max_tokens": 900, "system": SYSTEM, "messages": messages},
                {"x-api-key": key, "anthropic-version": "2023-06-01"})
    return "".join(b.get("text", "") for b in out.get("content", [])).strip()


def pick_provider():
    choice = os.environ.get("NARRATOR", "auto").lower()
    gh, ak = os.environ.get("GITHUB_TOKEN"), os.environ.get("ANTHROPIC_API_KEY")
    if choice in ("anthropic", "auto") and ak:
        model = os.environ.get("NARRATOR_MODEL") or "claude-sonnet-5-5"
        return "Claude", model, lambda m: call_anthropic(m, model, ak)
    if choice in ("github", "auto") and gh:
        model = os.environ.get("NARRATOR_MODEL") or "openai/gpt-4o-mini"
        return "GitHub Models", model, lambda m: call_github(m, model, gh)
    return None


# --------------------------------------------------------------------------- #
def narrate_one(call, report: dict) -> tuple[str | None, list[str]]:
    messages = [{"role": "user", "content": PROMPT.format(data=json.dumps(compact(report)))}]
    story = call(messages)
    bad = unsupported_numbers(story, report)
    if bad:  # one retry with specific feedback
        messages += [{"role": "assistant", "content": story},
                     {"role": "user", "content":
                      f"These numbers are not in the data: {', '.join(bad)}. Rewrite the story "
                      "using only numbers that appear in the data. Reply with the story only."}]
        story = call(messages)
        bad = unsupported_numbers(story, report)
    return (None, bad) if bad else (story, [])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Add AI-written stories to match reports.")
    ap.add_argument("reports_dir", type=Path)
    ap.add_argument("--limit", type=int, default=15, help="max reports to narrate per run")
    args = ap.parse_args(argv)

    provider = pick_provider()
    if not provider:
        print("No GITHUB_TOKEN or ANTHROPIC_API_KEY; skipping stories (stats-only reports kept).")
        return 0
    name, model, call = provider
    print(f"Writing stories with {name} ({model})")

    done, rejected = 0, 0
    for j in sorted(args.reports_dir.glob("*.json"), reverse=True):
        md = j.with_suffix(".md")
        if not md.exists() or MARKER in md.read_text(encoding="utf-8"):
            continue
        report = json.loads(j.read_text(encoding="utf-8"))
        if report.get("checks"):
            print(f"  skip {md.name}: data consistency warnings")
            continue
        try:
            story, bad = narrate_one(call, report)
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode(errors="replace")
            print(f"  API error {e.code} on {md.name}: {detail}")
            if e.code == 429:
                print("  Rate limit reached; remaining reports will get stories next run.")
                break
            return 1
        if story is None:
            rejected += 1
            print(f"  rejected story for {md.name}: unsupported numbers {', '.join(bad)}")
            continue
        lines = md.read_text(encoding="utf-8").split("\n")
        at = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
        credit = f"*Story written by {model} from the computed stats below.*"
        lines[at:at] = [MARKER, story, "", credit, ""]
        md.write_text("\n".join(lines), encoding="utf-8")
        print(f"  narrated {md.name}")
        done += 1
        if done >= args.limit:
            break
    print(f"Stories added: {done}, rejected by fact-check: {rejected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
