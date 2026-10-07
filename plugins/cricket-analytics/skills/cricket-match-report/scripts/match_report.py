#!/usr/bin/env python3
"""
Cricket match report engine for Cricsheet JSON files.

Computes everything a match report needs, deterministically, so the narrative
written on top of it never invents a number:

  - result and innings totals
  - batting and bowling scorecards
  - phase breakdown (powerplay / middle / death, by format)
  - partnerships and fall of wickets
  - turning points: most expensive overs, wicket clusters, and a simple
    run-rate swing measure for the chasing side

Usage:
  python match_report.py MATCH.json                 # markdown to stdout
  python match_report.py MATCH.json --format json   # structured JSON
  python match_report.py MATCH.json -o report.md

Only the Python standard library is used.

Cricsheet conventions handled here:
  - overs are 0-indexed in the file; reported 1-indexed
  - wides and no-balls are not legal deliveries (do not advance the over)
  - a batter faces a no-ball but not a wide
  - byes and leg-byes are not charged to the bowler
  - run outs, retirements and obstructing the field are not bowler wickets
  - super-over innings are reported separately from the main match
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

NOT_BOWLER_WICKETS = {
    "run out",
    "retired hurt",
    "retired out",
    "retired not out",
    "obstructing the field",
    "handled the ball",
    "timed out",
}
# Dismissals that don't use up a "wicket" for the batting side's total.
NOT_TEAM_WICKETS = {"retired hurt", "retired not out"}

# Phase definitions by format: list of (name, first_over, last_over), 1-indexed, inclusive.
PHASES = {
    "T20": [("Powerplay", 1, 6), ("Middle", 7, 15), ("Death", 16, 20)],
    "IT20": [("Powerplay", 1, 6), ("Middle", 7, 15), ("Death", 16, 20)],
    "ODI": [("Powerplay", 1, 10), ("Middle", 11, 40), ("Death", 41, 50)],
    "ODM": [("Powerplay", 1, 10), ("Middle", 11, 40), ("Death", 41, 50)],
}


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class BatterLine:
    name: str
    runs: int = 0
    balls: int = 0
    fours: int = 0
    sixes: int = 0
    dismissal: str = "not out"

    @property
    def strike_rate(self) -> float | None:
        return round(100 * self.runs / self.balls, 1) if self.balls else None


@dataclass
class BowlerLine:
    name: str
    legal_balls: int = 0
    runs: int = 0
    wickets: int = 0
    dots: int = 0
    wides: int = 0
    noballs: int = 0
    maidens: int = 0

    @property
    def overs(self) -> str:
        return f"{self.legal_balls // 6}.{self.legal_balls % 6}"

    @property
    def economy(self) -> float | None:
        return round(6 * self.runs / self.legal_balls, 2) if self.legal_balls else None


@dataclass
class Innings:
    number: int
    team: str
    super_over: bool = False
    total: int = 0
    wickets: int = 0
    legal_balls: int = 0
    extras: dict = field(default_factory=lambda: defaultdict(int))
    batting: "OrderedDict[str, BatterLine]" = field(default_factory=OrderedDict)
    bowling: "OrderedDict[str, BowlerLine]" = field(default_factory=OrderedDict)
    over_runs: list = field(default_factory=list)      # [(over_no, runs, wickets, bowler)]
    fall_of_wickets: list = field(default_factory=list)  # dicts
    partnerships: list = field(default_factory=list)     # dicts
    target: int | None = None
    target_overs: float | None = None

    @property
    def overs(self) -> str:
        return f"{self.legal_balls // 6}.{self.legal_balls % 6}"

    @property
    def run_rate(self) -> float | None:
        return round(6 * self.total / self.legal_balls, 2) if self.legal_balls else None


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _bat(inn: Innings, name: str) -> BatterLine:
    if name not in inn.batting:
        inn.batting[name] = BatterLine(name)
    return inn.batting[name]


def _bowl(inn: Innings, name: str) -> BowlerLine:
    if name not in inn.bowling:
        inn.bowling[name] = BowlerLine(name)
    return inn.bowling[name]


def _describe_dismissal(w: dict, bowler: str) -> str:
    kind = w.get("kind", "out")
    fielders = [f.get("name", "sub") for f in w.get("fielders", []) if isinstance(f, dict)]
    if kind == "caught":
        if fielders and fielders[0] == bowler:
            return f"c & b {bowler}"
        return f"c {fielders[0]} b {bowler}" if fielders else f"c ? b {bowler}"
    if kind == "caught and bowled":
        return f"c & b {bowler}"
    if kind == "bowled":
        return f"b {bowler}"
    if kind == "lbw":
        return f"lbw b {bowler}"
    if kind == "stumped":
        return f"st {fielders[0]} b {bowler}" if fielders else f"st ? b {bowler}"
    if kind == "hit wicket":
        return f"hit wicket b {bowler}"
    if kind == "run out":
        return f"run out ({', '.join(fielders)})" if fielders else "run out"
    return kind


def parse_innings(raw: dict, number: int) -> Innings:
    inn = Innings(number=number, team=raw.get("team", f"Innings {number}"),
                  super_over=bool(raw.get("super_over", False)))
    tgt = raw.get("target") or {}
    inn.target = tgt.get("runs")
    inn.target_overs = tgt.get("overs")

    partnership = {"runs": 0, "balls": 0, "batters": OrderedDict()}

    def close_partnership(end_reason: str):
        if partnership["balls"] or partnership["runs"]:
            inn.partnerships.append({
                "wicket": len(inn.partnerships) + 1,
                "runs": partnership["runs"],
                "balls": partnership["balls"],
                "batters": list(partnership["batters"].keys()),
                "contributions": dict(partnership["batters"]),
                "ended_by": end_reason,
            })

    for over in raw.get("overs", []):
        over_no = over.get("over", 0) + 1
        over_total = 0
        over_wkts = 0
        over_bowler_wkts = 0
        over_bowler = None
        over_bowler_conceded = 0
        over_legal = 0

        for d in over.get("deliveries", []):
            batter = d["batter"]
            bowler = d["bowler"]
            non_striker = d.get("non_striker")
            over_bowler = bowler
            runs = d.get("runs", {})
            r_bat = runs.get("batter", 0)
            r_extras = runs.get("extras", 0)
            r_total = runs.get("total", r_bat + r_extras)
            extras = d.get("extras", {})

            is_wide = "wides" in extras
            is_noball = "noballs" in extras
            legal = not (is_wide or is_noball)

            # Ensure batting order includes both batters in the order they appear.
            b = _bat(inn, batter)
            if non_striker:
                _bat(inn, non_striker)
            bw = _bowl(inn, bowler)

            # Batting
            b.runs += r_bat
            if not is_wide:
                b.balls += 1
            if r_bat == 4 and not d.get("runs", {}).get("non_boundary"):
                b.fours += 1
            elif r_bat == 6 and not d.get("runs", {}).get("non_boundary"):
                b.sixes += 1

            # Bowling: bowler is charged batter runs + wides + no-balls (not byes/leg-byes/penalty)
            charged = r_bat + extras.get("wides", 0) + extras.get("noballs", 0)
            bw.runs += charged
            over_bowler_conceded += charged
            if legal:
                bw.legal_balls += 1
                over_legal += 1
                inn.legal_balls += 1
                if r_total == 0:
                    bw.dots += 1
            if is_wide:
                bw.wides += extras.get("wides", 0)
            if is_noball:
                bw.noballs += extras.get("noballs", 0)

            # Team
            inn.total += r_total
            over_total += r_total
            for k, v in extras.items():
                inn.extras[k] += v

            # Partnership
            partnership["runs"] += r_total
            if legal:
                partnership["balls"] += 1
            for name in (batter, non_striker):
                if name:
                    partnership["batters"].setdefault(name, 0)
            partnership["batters"][batter] += r_bat

            # Wickets
            for w in d.get("wickets", []):
                kind = w.get("kind", "")
                out = w.get("player_out", batter)
                _bat(inn, out).dismissal = _describe_dismissal(w, bowler)
                if kind not in NOT_BOWLER_WICKETS:
                    bw.wickets += 1
                    over_bowler_wkts += 1
                if kind not in NOT_TEAM_WICKETS:
                    inn.wickets += 1
                    over_wkts += 1
                    inn.fall_of_wickets.append({
                        "wicket": inn.wickets,
                        "score": inn.total,
                        "player": out,
                        "over": f"{inn.legal_balls // 6}.{inn.legal_balls % 6}",
                        "kind": kind,
                    })
                close_partnership(f"{out} ({kind})")
                partnership = {"runs": 0, "balls": 0, "batters": OrderedDict()}

        if over_bowler and over_legal == 6 and over_bowler_conceded == 0:
            inn.bowling[over_bowler].maidens += 1
        inn.over_runs.append({"over": over_no, "runs": over_total, "wickets": over_wkts,
                              "bowler_wickets": over_bowler_wkts, "bowler": over_bowler, "legal_balls": over_legal})

    close_partnership("unbroken")
    return inn


def parse_match(data: dict) -> dict:
    info = data.get("info", {})
    innings = [parse_innings(raw, i + 1) for i, raw in enumerate(data.get("innings", []))]
    return {"info": info, "innings": innings}


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
def result_text(info: dict) -> str:
    out = info.get("outcome", {})
    if "winner" in out:
        by = out.get("by", {})
        if "runs" in by:
            margin = f"{by['runs']} run{'s' if by['runs'] != 1 else ''}"
        elif "wickets" in by:
            margin = f"{by['wickets']} wicket{'s' if by['wickets'] != 1 else ''}"
        elif "innings" in by:
            margin = f"an innings and {by.get('runs', 0)} runs"
        else:
            margin = ""
        method = f" ({out['method']})" if out.get("method") else ""
        eliminator = f" via {out['eliminator']}" if out.get("eliminator") else ""
        return f"{out['winner']} won" + (f" by {margin}" if margin else "") + method + eliminator
    if out.get("result"):
        return out["result"].capitalize()
    return "Result not recorded"


def phase_breakdown(inn: Innings, match_type: str) -> list[dict]:
    phases = PHASES.get(match_type)
    if not phases:
        return []
    rows = []
    for name, lo, hi in phases:
        overs = [o for o in inn.over_runs if lo <= o["over"] <= hi]
        if not overs:
            continue
        runs = sum(o["runs"] for o in overs)
        wkts = sum(o["wickets"] for o in overs)
        balls = sum(o["legal_balls"] for o in overs)
        rows.append({"phase": name, "overs": f"{lo}-{hi}",
                     "overs_bowled": f"{balls // 6}.{balls % 6}",
                     "runs": runs, "wickets": wkts,
                     "run_rate": round(6 * runs / balls, 2) if balls else None})
    return rows


def turning_points(innings: list[Innings], match_type: str) -> list[dict]:
    """Heuristic turning points. Each item is a fact with its evidence, not an opinion."""
    points = []
    main = [i for i in innings if not i.super_over]

    for inn in main:
        # Most expensive overs (top 2, at least 15 in limited overs)
        threshold = 15 if match_type in PHASES else 12
        costly = sorted(inn.over_runs, key=lambda o: o["runs"], reverse=True)[:2]
        for o in costly:
            if o["runs"] >= threshold:
                points.append({"innings": inn.number, "team": inn.team, "type": "big_over",
                               "over": o["over"], "detail":
                               f"Over {o['over']} ({o['bowler']}) went for {o['runs']} runs"})

        # Wicket clusters: 2+ wickets in a single over, or 3+ wickets across 3 overs
        for o in inn.over_runs:
            if o["wickets"] >= 2:
                credit = (f", {o['bowler_wickets']} credited to {o['bowler']}"
                          if o["bowler_wickets"] else "")
                points.append({"innings": inn.number, "team": inn.team, "type": "wicket_burst",
                               "over": o["over"], "detail":
                               f"{o['wickets']} wickets fell in over {o['over']} "
                               f"(bowled by {o['bowler']}{credit})"})
        ors = inn.over_runs
        for i in range(len(ors) - 2):
            window = ors[i:i + 3]
            w = sum(o["wickets"] for o in window)
            if w >= 3 and not any(o["wickets"] >= 2 for o in window):  # avoid double-reporting
                points.append({"innings": inn.number, "team": inn.team, "type": "collapse",
                               "over": window[0]["over"], "detail":
                               f"{w} wickets fell between overs {window[0]['over']} and {window[-1]['over']}"})
                break

    # Chase pressure: required rate at the start of each phase of the 2nd innings
    if len(main) >= 2 and main[1].target and match_type in PHASES:
        chase = main[1]
        target = chase.target
        total_overs = chase.target_overs or (20 if match_type in ("T20", "IT20") else 50)
        cum = 0
        for o in chase.over_runs:
            cum += o["runs"]
            if o["over"] in (6, 10, 15, 40):
                left_overs = total_overs - o["over"]
                if left_overs > 0:
                    req = round((target - cum) / left_overs, 2)
                    points.append({"innings": chase.number, "team": chase.team,
                                   "type": "required_rate", "over": o["over"],
                                   "detail": f"After {o['over']} overs: {cum} scored, "
                                             f"needed {target - cum} from {left_overs} overs "
                                             f"(RRR {req})"})
    return sorted(points, key=lambda p: (p["innings"], p["over"]))


def top_performers(innings: list[Innings], n: int = 3) -> dict:
    bats = [b for i in innings if not i.super_over for b in i.batting.values() if b.balls or b.runs]
    bowls = [b for i in innings if not i.super_over for b in i.bowling.values()]
    bats.sort(key=lambda b: (-b.runs, b.balls))
    bowls.sort(key=lambda b: (-b.wickets, b.runs))
    return {
        "batters": [{"name": b.name, "runs": b.runs, "balls": b.balls,
                     "strike_rate": b.strike_rate} for b in bats[:n]],
        "bowlers": [{"name": b.name, "figures": f"{b.wickets}/{b.runs}", "overs": b.overs,
                     "economy": b.economy} for b in bowls[:n]],
    }


def build_report(data: dict) -> dict:
    parsed = parse_match(data)
    info, innings = parsed["info"], parsed["innings"]
    match_type = info.get("match_type", "")
    event = info.get("event", {})
    report = {
        "match": {
            "teams": info.get("teams", []),
            "dates": info.get("dates", []),
            "venue": info.get("venue"),
            "city": info.get("city"),
            "event": event.get("name") if isinstance(event, dict) else event,
            "match_number": event.get("match_number") if isinstance(event, dict) else None,
            "match_type": match_type,
            "overs": info.get("overs"),
            "toss": info.get("toss", {}),
            "player_of_match": info.get("player_of_match", []),
            "result": result_text(info),
        },
        "innings": [],
        "top_performers": top_performers(innings),
        "turning_points": turning_points(innings, match_type),
        "checks": [],
    }
    for inn in innings:
        report["innings"].append({
            "number": inn.number,
            "team": inn.team,
            "super_over": inn.super_over,
            "total": inn.total,
            "wickets": inn.wickets,
            "overs": inn.overs,
            "run_rate": inn.run_rate,
            "target": inn.target,
            "extras": dict(inn.extras),
            "batting": [{"name": b.name, "dismissal": b.dismissal, "runs": b.runs,
                         "balls": b.balls, "fours": b.fours, "sixes": b.sixes,
                         "strike_rate": b.strike_rate}
                        for b in inn.batting.values()],
            "bowling": [{"name": b.name, "overs": b.overs, "maidens": b.maidens,
                         "runs": b.runs, "wickets": b.wickets, "economy": b.economy,
                         "dots": b.dots, "wides": b.wides, "noballs": b.noballs}
                        for b in inn.bowling.values()],
            "phases": [] if inn.super_over else phase_breakdown(inn, match_type),
            "fall_of_wickets": inn.fall_of_wickets,
            "partnerships": inn.partnerships,
            "over_by_over": inn.over_runs,
        })
        # Internal consistency checks: these should always hold. A failure means
        # the data or the parser is wrong, and the narrative must not be written.
        bat_runs = sum(b.runs for b in inn.batting.values())
        extras_total = sum(inn.extras.values())
        if bat_runs + extras_total != inn.total:
            report["checks"].append(
                f"Innings {inn.number}: batter runs ({bat_runs}) + extras ({extras_total}) "
                f"!= total ({inn.total})")
        bowl_runs = sum(b.runs for b in inn.bowling.values())
        expected_bowl = inn.total - inn.extras.get("byes", 0) - inn.extras.get("legbyes", 0) \
            - inn.extras.get("penalty", 0)
        if bowl_runs != expected_bowl:
            report["checks"].append(
                f"Innings {inn.number}: bowler runs ({bowl_runs}) != total minus byes/leg-byes "
                f"({expected_bowl})")
        if sum(p["runs"] for p in inn.partnerships) != inn.total:
            report["checks"].append(f"Innings {inn.number}: partnerships don't sum to total")
    return report


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _fmt(v):
    return "-" if v is None else v


def to_markdown(r: dict) -> str:
    m = r["match"]
    L = []
    title = " vs ".join(m["teams"]) or "Match report"
    L.append(f"# {title}")
    meta = [x for x in [m.get("event") and (m["event"] + (f", match {m['match_number']}"
                                                          if m.get("match_number") else "")),
                        m.get("match_type"), m.get("venue"),
                        ", ".join(m.get("dates", []))] if x]
    if meta:
        L.append("*" + " · ".join(str(x) for x in meta) + "*")
    L.append("")
    L.append(f"**Result:** {m['result']}  ")
    toss = m.get("toss") or {}
    if toss:
        L.append(f"**Toss:** {toss.get('winner')} chose to {toss.get('decision')}  ")
    if m.get("player_of_match"):
        L.append(f"**Player of the match:** {', '.join(m['player_of_match'])}  ")
    L.append("")

    L.append("## Summary")
    for inn in r["innings"]:
        so = " (super over)" if inn["super_over"] else ""
        tgt = f", chasing {inn['target']}" if inn.get("target") else ""
        L.append(f"- **{inn['team']}**{so}: {inn['total']}/{inn['wickets']} "
                 f"in {inn['overs']} overs (RR {_fmt(inn['run_rate'])}{tgt})")
    L.append("")

    tp = r["top_performers"]
    L.append("## Top performers")
    L.append("| Batter | R | B | SR |\n|---|--:|--:|--:|")
    for b in tp["batters"]:
        L.append(f"| {b['name']} | {b['runs']} | {b['balls']} | {_fmt(b['strike_rate'])} |")
    L.append("")
    L.append("| Bowler | Figures | Overs | Econ |\n|---|--:|--:|--:|")
    for b in tp["bowlers"]:
        L.append(f"| {b['name']} | {b['figures']} | {b['overs']} | {_fmt(b['economy'])} |")
    L.append("")

    if r["turning_points"]:
        L.append("## Key moments (computed)")
        for p in r["turning_points"]:
            L.append(f"- *{p['team']}, {p['type'].replace('_', ' ')}:* {p['detail']}")
        L.append("")

    for inn in r["innings"]:
        so = " — super over" if inn["super_over"] else ""
        L.append(f"## {inn['team']} innings{so}: {inn['total']}/{inn['wickets']} ({inn['overs']} ov)")
        L.append("| Batter | Dismissal | R | B | 4s | 6s | SR |\n|---|---|--:|--:|--:|--:|--:|")
        for b in inn["batting"]:
            if b["balls"] == 0 and b["runs"] == 0 and b["dismissal"] == "not out":
                continue  # did not face
            L.append(f"| {b['name']} | {b['dismissal']} | {b['runs']} | {b['balls']} | "
                     f"{b['fours']} | {b['sixes']} | {_fmt(b['strike_rate'])} |")
        ex = inn["extras"]
        ex_str = ", ".join(f"{k} {v}" for k, v in ex.items()) or "none"
        L.append(f"\nExtras: {sum(ex.values())} ({ex_str})\n")
        L.append("| Bowler | O | M | R | W | Econ | Dots |\n|---|--:|--:|--:|--:|--:|--:|")
        for b in inn["bowling"]:
            L.append(f"| {b['name']} | {b['overs']} | {b['maidens']} | {b['runs']} | "
                     f"{b['wickets']} | {_fmt(b['economy'])} | {b['dots']} |")
        L.append("")
        if inn["phases"]:
            L.append("| Phase | Overs | Bowled | Runs | Wkts | Run rate |\n"
                     "|---|---|--:|--:|--:|--:|")
            for p in inn["phases"]:
                L.append(f"| {p['phase']} | {p['overs']} | {p['overs_bowled']} | {p['runs']} | "
                         f"{p['wickets']} | {_fmt(p['run_rate'])} |")
            L.append("")
        if inn["fall_of_wickets"]:
            fow = ", ".join(f"{f['score']}-{f['wicket']} ({f['player']}, {f['over']})"
                            for f in inn["fall_of_wickets"])
            L.append(f"**Fall of wickets:** {fow}\n")
        if inn["partnerships"]:
            best = max(inn["partnerships"], key=lambda p: p["runs"])
            L.append(f"**Best partnership:** {best['runs']} off {best['balls']} balls "
                     f"({' & '.join(best['batters'])})\n")

    if r["checks"]:
        L.append("## ⚠ Data consistency warnings")
        for c in r["checks"]:
            L.append(f"- {c}")
        L.append("")
    L.append("---\n*Data: [Cricsheet](https://cricsheet.org), ball-by-ball. "
             "Stats computed by cricket-match-report.*")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("match", type=Path, help="Cricsheet JSON match file")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("-o", "--output", type=Path, help="write to file instead of stdout")
    args = ap.parse_args(argv)

    try:
        data = json.loads(args.match.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"Could not read {args.match}: {e}", file=sys.stderr)
        return 2
    if "innings" not in data or "info" not in data:
        print(f"{args.match} doesn't look like a Cricsheet JSON match file "
              "(expected 'info' and 'innings' keys).", file=sys.stderr)
        return 2

    report = build_report(data)
    out = json.dumps(report, indent=2) if args.format == "json" else to_markdown(report)
    if args.output:
        args.output.write_text(out, encoding="utf-8")
    else:
        print(out)
    return 1 if report["checks"] else 0


if __name__ == "__main__":
    sys.exit(main())
