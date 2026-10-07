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
    Model: NARRATOR_MODEL (default: openai/gpt-4o-mini, falling back to
    openai/gpt-4.1-mini if the first doesn't respond).
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
import gzip
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
class NarratorError(RuntimeError):
    """The model service answered, but not with a usable reply."""


def _post(url: str, body: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
        status, ctype = resp.status, resp.headers.get("Content-Type", "?")
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        snippet = raw[:200].decode("utf-8", errors="replace") or "(empty body)"
        raise NarratorError(f"HTTP {status}, {ctype}, {len(raw)} bytes: {snippet}") from None


def call_github(messages: list, model: str, token: str) -> str:
    # Headers as documented at docs.github.com/en/github-models/quickstart
    out = _post(GITHUB_URL,
                {"model": model, "max_tokens": 700, "temperature": 0.4,
                 "messages": [{"role": "system", "content": SYSTEM}, *messages]},
                {"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    try:
        return out["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        raise NarratorError(f"unexpected reply: {json.dumps(out)[:200]}") from None


def call_anthropic(messages: list, model: str, key: str) -> str:
    out = _post(ANTHROPIC_URL,
                {"model": model, "max_tokens": 900, "system": SYSTEM, "messages": messages},
                {"x-api-key": key, "anthropic-version": "2023-06-01"})
    return "".join(b.get("text", "") for b in out.get("content", [])).strip()


def pick_providers():
    """Ordered list of (name, model, call) to try; later entries are fallbacks."""
    choice = os.environ.get("NARRATOR", "auto").lower()
    gh, ak = os.environ.get("GITHUB_TOKEN"), os.environ.get("ANTHROPIC_API_KEY")
    custom = os.environ.get("NARRATOR_MODEL")
    if choice in ("anthropic", "auto") and ak:
        model = custom or "claude-sonnet-5-5"
        return [("Claude", model, lambda m, model=model: call_anthropic(m, model, ak))]
    if choice in ("github", "auto") and gh:
        models = [custom] if custom else ["openai/gpt-4o-mini", "openai/gpt-4.1-mini"]
        return [("GitHub Models", mdl, lambda m, mdl=mdl: call_github(m, mdl, gh))
                for mdl in models]
    return []


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
    """Never fails the run: stories are a bonus on top of the reports."""
    ap = argparse.ArgumentParser(description="Add AI-written stories to match reports.")
    ap.add_argument("reports_dir", type=Path)
    ap.add_argument("--limit", type=int, default=15, help="max reports to narrate per run")
    args = ap.parse_args(argv)

    providers = pick_providers()
    if not providers:
        print("No GITHUB_TOKEN or ANTHROPIC_API_KEY; skipping stories (stats-only reports kept).")
        return 0
    name, model, call = providers.pop(0)
    print(f"Writing stories with {name} ({model})")

    done, rejected, written_any = 0, 0, False
    for j in sorted(args.reports_dir.glob("*.json"), reverse=True):
        md = j.with_suffix(".md")
        if not md.exists() or MARKER in md.read_text(encoding="utf-8"):
            continue
        report = json.loads(j.read_text(encoding="utf-8"))
        if report.get("checks"):
            print(f"  skip {md.name}: data consistency warnings")
            continue
        while True:
            try:
                story, bad = narrate_one(call, report)
                break
            except urllib.error.HTTPError as e:
                detail = e.read()[:300].decode(errors="replace")
                problem = f"HTTP {e.code}: {detail}"
                if e.code == 429:
                    print(f"  Rate limit reached ({problem}).\n"
                          "  Remaining reports will get stories on the next run.")
                    return _summary(done, rejected)
            except (NarratorError, urllib.error.URLError, TimeoutError, OSError) as e:
                problem = str(e)
            # The model didn't work. Before any story succeeded, try the next model.
            if providers and not written_any:
                print(f"  {model} failed ({problem}); trying next model")
                name, model, call = providers.pop(0)
                print(f"Writing stories with {name} ({model})")
                continue
            print(f"  Story service error with {model}: {problem}\n"
                  "  Skipping stories this run; reports are unaffected.\n"
                  "  Tip: set a NARRATOR_MODEL repository variable to try a different model.")
            return _summary(done, rejected)
        if story is None:
            rejected += 1
            print(f"  rejected story for {md.name}: unsupported numbers {', '.join(bad)}")
            continue
        written_any = True
        lines = md.read_text(encoding="utf-8").split("\n")
        at = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
        credit = f"*Story written by {model} from the computed stats below.*"
        lines[at:at] = [MARKER, story, "", credit, ""]
        md.write_text("\n".join(lines), encoding="utf-8")
        print(f"  narrated {md.name}")
        done += 1
        if done >= args.limit:
            break
    return _summary(done, rejected)


def _summary(done: int, rejected: int) -> int:
    print(f"Stories added: {done}, rejected by fact-check: {rejected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
