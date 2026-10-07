#!/usr/bin/env python3
"""
Generate a synthetic T20 match in Cricsheet JSON format, for tests and demos.

Teams and players are fictional. The point is to exercise every edge case the
report engine handles: wides, no-balls, byes, leg-byes, run outs, caught,
stumped, maidens, a chase with a target.

  python tests/make_sample_match.py > examples/sample_match.json
"""
import json
import random

import sys
rng = random.Random(int(sys.argv[1]) if len(sys.argv) > 1 else 25)

TEAMS = {
    "Monsoon Mavericks": ["A Rao", "K Mehta", "S Iyer", "R Bhat", "P Nair", "V Kulkarni",
                          "D Joshi", "H Sethi", "M Khan", "T Pillai", "N Desai"],
    "Deccan Comets": ["J Fernandes", "L Reddy", "Y Saxena", "G Menon", "B Chawla", "O Tiwari",
                      "C Gill", "E Bose", "F Shetty", "I Varma", "U Dutta"],
}


def innings(team, opp, target=None):
    batters = TEAMS[team][:]
    bowlers = TEAMS[opp][6:]  # last five bowl
    fielders = TEAMS[opp]
    striker, non_striker = batters[0], batters[1]
    next_in = 2
    total, wkts = 0, 0
    overs = []
    order = bowlers[:]
    rng.shuffle(order)
    for o in range(20):
        bowler = order[o % 5]  # 4 overs each, never consecutive
        dels, legal = [], 0
        death = o >= 15
        while legal < 6:
            d = {"batter": striker, "bowler": bowler, "non_striker": non_striker}
            roll = rng.random()
            extras = {}
            r_bat = 0
            wicket = None
            if roll < 0.03:
                extras = {"wides": 1}
            elif roll < 0.045:
                extras = {"noballs": 1}
                r_bat = rng.choice([0, 1, 4])
            elif roll < 0.06:
                extras = {"legbyes": rng.choice([1, 2])}
            elif roll < 0.07:
                extras = {"byes": 1}
            elif roll < (0.15 if death else 0.115):
                wicket = rng.choices(["caught", "bowled", "lbw", "run out", "stumped"], [55, 18, 14, 9, 4])[0]
            else:
                weights = [30, 34, 9, 1, 14, 9] if death else [40, 36, 8, 1, 11, 4]
                r_bat = rng.choices([0, 1, 2, 3, 4, 6], weights)[0]
            r_extras = sum(extras.values())
            d["runs"] = {"batter": r_bat, "extras": r_extras, "total": r_bat + r_extras}
            if extras:
                d["extras"] = extras
            if wicket:
                w = {"player_out": striker, "kind": wicket}
                if wicket in ("caught", "run out"):
                    w["fielders"] = [{"name": rng.choice(fielders)}]
                if wicket == "stumped":
                    w["fielders"] = [{"name": fielders[3]}]  # wicketkeeper (doesn't bowl)
                d["wickets"] = [w]
            dels.append(d)
            total += r_bat + r_extras
            if "wides" not in extras and "noballs" not in extras:
                legal += 1
            if wicket:
                wkts += 1
                if wkts == 10 or next_in >= len(batters):
                    overs.append({"over": o, "deliveries": dels})
                    return {"team": team, "overs": overs}, total
                striker = batters[next_in]
                next_in += 1
            elif (r_bat + extras.get("legbyes", 0) + extras.get("byes", 0)) % 2 == 1:
                striker, non_striker = non_striker, striker
            if target and total >= target:
                overs.append({"over": o, "deliveries": dels})
                return {"team": team, "overs": overs, "target": {"overs": 20, "runs": target}}, total
        overs.append({"over": o, "deliveries": dels})
        striker, non_striker = non_striker, striker
    out = {"team": team, "overs": overs}
    if target:
        out["target"] = {"overs": 20, "runs": target}
    return out, total


first, t1 = innings("Monsoon Mavericks", "Deccan Comets")
second, t2 = innings("Deccan Comets", "Monsoon Mavericks", target=t1 + 1)
wk2 = sum(len(d.get("wickets", [])) for o in second["overs"] for d in o["deliveries"])
if t2 > t1:
    outcome = {"winner": "Deccan Comets", "by": {"wickets": 10 - wk2}}
elif t1 > t2:
    outcome = {"winner": "Monsoon Mavericks", "by": {"runs": t1 - t2}}
else:
    outcome = {"result": "tie"}

match = {
    "meta": {"data_version": "1.1.0", "created": "2026-10-07", "revision": 1},
    "info": {
        "balls_per_over": 6,
        "city": "Example City",
        "dates": ["2026-04-12"],
        "event": {"name": "Synthetic Premier League (demo data)", "match_number": 14},
        "gender": "male",
        "match_type": "T20",
        "outcome": outcome,
        "overs": 20,
        "player_of_match": [],
        "players": TEAMS,
        "season": "2026",
        "team_type": "club",
        "teams": list(TEAMS),
        "toss": {"decision": "field", "winner": "Deccan Comets"},
        "venue": "Example Stadium",
    },
    "innings": [first, second],
}
print(json.dumps(match, indent=1))
