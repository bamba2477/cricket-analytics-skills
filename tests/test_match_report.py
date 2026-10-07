"""
Hand-checked tests for the scoring rules. Each test builds a tiny innings
whose correct scorecard is worked out by hand in the comments.

  python -m unittest discover tests
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "cricket-analytics" / "skills" /
                       "cricket-match-report" / "scripts"))
from match_report import build_report, parse_innings, result_text  # noqa: E402


def ball(batter="A", bowler="X", non_striker="B", bat=0, extras=None, wicket=None, **runs_flags):
    extras = extras or {}
    e = sum(extras.values())
    d = {"batter": batter, "bowler": bowler, "non_striker": non_striker,
         "runs": {"batter": bat, "extras": e, "total": bat + e, **runs_flags}}
    if extras:
        d["extras"] = extras
    if wicket:
        d["wickets"] = [wicket]
    return d


class ScoringRules(unittest.TestCase):
    def test_extras_balls_and_bowler_charges(self):
        # Over: 1, wide, nb+4, legbye 2, bye 1, 0, 6  -> 7 deliveries, 5 legal
        # plus one more legal 0 to complete the over.
        over = [ball(bat=1, batter="A"),
                ball(batter="B", non_striker="A", extras={"wides": 1}),
                ball(batter="B", non_striker="A", bat=4, extras={"noballs": 1}),
                ball(batter="B", non_striker="A", extras={"legbyes": 2}),
                ball(batter="B", non_striker="A", extras={"byes": 1}),
                ball(batter="A", non_striker="B"),
                ball(batter="A", non_striker="B", bat=6)]
        inn = parse_innings({"team": "T", "overs": [{"over": 0, "deliveries": over}]}, 1)
        # Team total: 1 + 1 + 5 + 2 + 1 + 0 + 6 = 16
        self.assertEqual(inn.total, 16)
        # Legal balls: 7 deliveries minus wide and no-ball = 5
        self.assertEqual(inn.legal_balls, 5)
        a, b = inn.batting["A"], inn.batting["B"]
        # A faced 3 (1, 0, 6) = 7 runs; B faced nb, lb, bye = 3 balls, 4 runs
        self.assertEqual((a.runs, a.balls, a.sixes), (7, 3, 1))
        self.assertEqual((b.runs, b.balls, b.fours), (4, 3, 1))
        # Batter dots: balls faced with nothing off the bat.
        # A: the 0 ball. B: the leg-bye and the bye (the no-ball went for 4).
        self.assertEqual(a.dots, 1)
        self.assertEqual(b.dots, 2)
        x = inn.bowling["X"]
        # Bowler charged everything except leg-byes and byes: 16 - 3 = 13
        self.assertEqual(x.runs, 13)
        self.assertEqual(x.overs, "0.5")
        # Dots: legal balls where the bowler conceded nothing: the leg-bye,
        # the bye and the 0 (wide and no-ball aren't legal, so never dots)
        self.assertEqual(x.dots, 3)
        self.assertEqual((x.wides, x.noballs), (1, 1))

    def test_run_out_not_credited_to_bowler(self):
        over = [ball(wicket={"player_out": "B", "kind": "run out",
                             "fielders": [{"name": "F"}]})]
        inn = parse_innings({"team": "T", "overs": [{"over": 0, "deliveries": over}]}, 1)
        self.assertEqual(inn.wickets, 1)
        self.assertEqual(inn.bowling["X"].wickets, 0)
        self.assertEqual(inn.batting["B"].dismissal, "run out (F)")
        self.assertEqual(inn.batting["A"].dismissal, "not out")

    def test_retired_hurt_is_not_a_team_wicket(self):
        over = [ball(wicket={"player_out": "A", "kind": "retired hurt"})]
        inn = parse_innings({"team": "T", "overs": [{"over": 0, "deliveries": over}]}, 1)
        self.assertEqual(inn.wickets, 0)
        self.assertEqual(inn.bowling["X"].wickets, 0)

    def test_caught_and_bowled_and_maiden(self):
        over = [ball() for _ in range(5)] + [
            ball(wicket={"player_out": "A", "kind": "caught", "fielders": [{"name": "X"}]})]
        inn = parse_innings({"team": "T", "overs": [{"over": 0, "deliveries": over}]}, 1)
        self.assertEqual(inn.batting["A"].dismissal, "c & b X")
        self.assertEqual(inn.bowling["X"].maidens, 1)
        self.assertEqual(inn.bowling["X"].wickets, 1)

    def test_bye_is_a_dot_and_keeps_maiden(self):
        over = [ball(extras={"byes": 4})] + [ball() for _ in range(5)]
        inn = parse_innings({"team": "T", "overs": [{"over": 0, "deliveries": over}]}, 1)
        x = inn.bowling["X"]
        self.assertEqual(x.maidens, 1)   # bowler conceded nothing
        self.assertEqual(x.dots, 6)      # byes aren't charged to the bowler

    def test_fall_of_wicket_ball_notation(self):
        # Wicket on the last ball of the 2nd over is 1.6, not 2.0
        o1 = [ball() for _ in range(6)]
        o2 = [ball() for _ in range(5)] + [ball(wicket={"player_out": "A", "kind": "bowled"})]
        inn = parse_innings({"team": "T", "overs": [{"over": 0, "deliveries": o1},
                                                     {"over": 1, "deliveries": o2}]}, 1)
        self.assertEqual(inn.fall_of_wickets[0]["over"], "1.6")
        # First ball of the match
        inn = parse_innings({"team": "T", "overs": [{"over": 0, "deliveries": [
            ball(wicket={"player_out": "A", "kind": "bowled"})]}]}, 1)
        self.assertEqual(inn.fall_of_wickets[0]["over"], "0.1")

    def test_all_run_four_is_not_a_boundary(self):
        over = [ball(bat=4, non_boundary=True)]
        inn = parse_innings({"team": "T", "overs": [{"over": 0, "deliveries": over}]}, 1)
        self.assertEqual(inn.batting["A"].fours, 0)
        self.assertEqual(inn.batting["A"].runs, 4)

    def test_result_text(self):
        self.assertEqual(result_text({"outcome": {"winner": "T", "by": {"wickets": 1}}}),
                         "T won by 1 wicket")
        self.assertEqual(result_text({"outcome": {"winner": "T", "by": {"runs": 12},
                                                  "method": "D/L"}}),
                         "T won by 12 runs (D/L)")
        self.assertEqual(result_text({"outcome": {"result": "no result"}}), "No result")


class SampleMatch(unittest.TestCase):
    def test_sample_reconciles(self):
        data = json.loads((ROOT / "examples" / "sample_match.json").read_text())
        r = build_report(data)
        self.assertEqual(r["checks"], [])
        self.assertEqual(r["match"]["result"], "Deccan Comets won by 2 wickets")
        for inn in r["innings"]:
            bowler_w = sum(b["wickets"] for b in inn["bowling"])
            run_outs = sum(1 for b in inn["batting"] if b["dismissal"].startswith("run out"))
            self.assertEqual(bowler_w + run_outs, inn["wickets"])


if __name__ == "__main__":
    unittest.main()
