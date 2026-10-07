#!/usr/bin/env python3
"""
Add an AI-written match story to each generated report.

Reads the <report>.json files that batch_reports.py writes, sends a compact
version of the computed stats to a language model, fact-checks the reply,
and inserts the story at the top of the matching .md report. Reports that
already have a story are skipped.

Providers (chosen automatically, or set NARRATOR=groq|anthropic):
  - Groq (free tier): used when GROQ_API_KEY is set. Get a key at
    https://console.groq.com/keys. Model: NARRATOR_MODEL (default
    openai/gpt-oss-120b, falling back to openai/gpt-oss-20b).
  - Anthropic Claude (paid, best quality): used when ANTHROPIC_API_KEY is set.
    Model: NARRATOR_MODEL (default claude-sonnet-5-5).
If neither key is set, the script exits quietly and stats-only reports stay.

Free tiers have per-minute token limits, so requests are spaced out
(NARRATOR_DELAY seconds between stories, default 10 for Groq) and a rate-limit
reply is waited out and retried rather than ending the run.

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
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MARKER = "<!-- narrative -->"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Name the client explicitly: Cloudflare (in front of Groq) rejects urllib's
# default "Python-urllib/x.y" signature with error 1010.
USER_AGENT = "cricket-analytics-skills/0.3 (+https://github.com/bamba2477/cricket-analytics-skills)"
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib turns a redirected POST into a GET and drops the body. Surface
    redirects instead, so _post can re-send the POST itself (like curl -L -X POST)."""
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)
_TRUSTED_HOSTS = ("groq.com", "anthropic.com")


def _post(url: str, body: dict, headers: dict) -> dict:
    data = json.dumps(body).encode()
    hops = []
    for _ in range(5):
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": USER_AGENT, **headers})
        try:
            with _OPENER.open(req, timeout=120) as resp:
                raw = resp.read()
                status, ctype = resp.status, resp.headers.get("Content-Type", "?")
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            break
        except urllib.error.HTTPError as e:
            location = e.headers.get("Location") if e.code in (301, 302, 303, 307, 308) else None
            if not location:
                raise
            nxt = urllib.parse.urljoin(url, location)
            hops.append(f"{e.code} -> {nxt}")
            host = urllib.parse.urlparse(nxt).hostname or ""
            if not host.endswith(_TRUSTED_HOSTS):  # never send credentials elsewhere
                raise NarratorError(f"redirected to untrusted host {host}; not following") from None
            url = nxt
    else:
        raise NarratorError(f"too many redirects: {'; '.join(hops)}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        snippet = raw[:200].decode("utf-8", errors="replace").strip() or "(empty body)"
        via = f" after redirects [{'; '.join(hops)}]" if hops else ""
        raise NarratorError(f"HTTP {status}, {ctype}, {len(raw)} bytes{via}: {snippet}") from None


def call_openai_compatible(url: str, messages: list, model: str, key: str) -> str:
    out = _post(url,
                # Reasoning models spend part of max_tokens thinking, so leave room.
                {"model": model, "max_tokens": 2000, "temperature": 0.4,
                 "messages": [{"role": "system", "content": SYSTEM}, *messages]},
                {"Authorization": f"Bearer {key}"})
    try:
        text = out["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        raise NarratorError(f"unexpected reply: {json.dumps(out)[:200]}") from None
    if not text.strip():
        raise NarratorError(f"empty story (finish_reason={out['choices'][0].get('finish_reason')})")
    return text.strip()


def _waiting_out_rate_limits(call, max_waits: int = 3, max_sleep: int = 65):
    """Wrap a provider call so HTTP 429 sleeps for Retry-After and retries."""
    def wrapped(messages):
        for attempt in range(max_waits + 1):
            try:
                return call(messages)
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == max_waits:
                    raise
                try:
                    wait = float(e.headers.get("Retry-After") or 20)
                except ValueError:
                    wait = 20
                wait = min(max(wait, 1), max_sleep)
                print(f"  rate limited; waiting {wait:.0f}s")
                time.sleep(wait)
    return wrapped


def call_anthropic(messages: list, model: str, key: str) -> str:
    out = _post(ANTHROPIC_URL,
                {"model": model, "max_tokens": 900, "system": SYSTEM, "messages": messages},
                {"x-api-key": key, "anthropic-version": "2023-06-01"})
    return "".join(b.get("text", "") for b in out.get("content", [])).strip()


def pick_providers():
    """Ordered list of (name, model, call, delay) to try; later entries are fallbacks."""
    choice = os.environ.get("NARRATOR", "auto").lower()
    groq, ak = os.environ.get("GROQ_API_KEY"), os.environ.get("ANTHROPIC_API_KEY")
    custom = os.environ.get("NARRATOR_MODEL")
    delay = os.environ.get("NARRATOR_DELAY")
    if choice in ("anthropic", "auto") and ak:
        model = custom or "claude-sonnet-5-5"
        call = _waiting_out_rate_limits(lambda m: call_anthropic(m, model, ak))
        return [("Claude", model, call, float(delay or 0))]
    if choice in ("groq", "auto") and groq:
        models = [custom] if custom else ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
        return [("Groq", mdl,
                 _waiting_out_rate_limits(lambda m, mdl=mdl: call_openai_compatible(GROQ_URL, m, mdl, groq)),
                 float(delay or 10)) for mdl in models]
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
    ap.add_argument("--time-budget", type=float, default=600,
                    help="stop starting new stories after this many seconds (default 600)")
    args = ap.parse_args(argv)
    started = time.monotonic()

    providers = pick_providers()
    if not providers:
        print("No GROQ_API_KEY or ANTHROPIC_API_KEY; skipping stories (stats-only reports kept).")
        return 0
    name, model, call, delay = providers.pop(0)
    print(f"Writing stories with {name} ({model})")

    pending = [j for j in sorted(args.reports_dir.glob("*.json"), reverse=True)
               if j.with_suffix(".md").exists()
               and MARKER not in j.with_suffix(".md").read_text(encoding="utf-8")]
    print(f"{len(pending)} report(s) without a story; writing up to {args.limit}")

    done, rejected, written_any = 0, 0, False
    for n, j in enumerate(pending, 1):
        md = j.with_suffix(".md")
        elapsed = time.monotonic() - started
        if elapsed > args.time_budget:
            print(f"  Time budget of {args.time_budget:.0f}s used; "
                  "remaining reports will get stories on the next run.")
            break
        print(f"  [{n}/{min(len(pending), args.limit)}] {md.name} ({elapsed:.0f}s elapsed)")
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
                if e.code == 429:  # still limited after waiting: daily quota is used up
                    print(f"  Rate limit reached ({problem}).\n"
                          "  Remaining reports will get stories on the next run.")
                    return _summary(done, rejected)
            except (NarratorError, urllib.error.URLError, TimeoutError, OSError) as e:
                problem = str(e)
            # The model didn't work. Before any story succeeded, try the next model.
            if providers and not written_any:
                print(f"  {model} failed ({problem}); trying next model")
                name, model, call, delay = providers.pop(0)
                print(f"Writing stories with {name} ({model})")
                continue
            print(f"  Story service error with {model}: {problem}\n"
                  "  Skipping stories this run; reports are unaffected.\n"
                  "  Tip: check the API key secret, or set a NARRATOR_MODEL variable to try another model.")
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
        time.sleep(delay)  # stay under per-minute token limits
    return _summary(done, rejected)


def _summary(done: int, rejected: int) -> int:
    print(f"Stories added: {done}, rejected by fact-check: {rejected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
