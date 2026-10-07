# Metric definitions

How `match_report.py` counts everything. These follow standard scoring
conventions so the output matches official scorecards.

## Deliveries
| Delivery | Legal ball? | Counts as ball faced? | Charged to bowler |
|---|---|---|---|
| Normal | yes | yes | batter runs |
| Wide | no | no | all wide runs |
| No-ball | no | yes | no-ball penalty + batter runs |
| Bye / leg-bye | yes | yes | nothing |
| Penalty runs | — | — | nothing |

## Batting
- **Runs**: runs off the bat only (no extras).
- **Balls**: every delivery faced except wides.
- **Strike rate**: 100 × runs ÷ balls.
- **4s / 6s**: boundaries off the bat; Cricsheet's `non_boundary` flag (all-run
  fours) is excluded.

## Bowling
- **Overs**: legal balls ÷ 6, written `overs.balls` (e.g. `3.4`).
- **Wickets**: all dismissals except run out, retired (hurt / out / not out),
  obstructing the field, handled the ball and timed out.
- **Economy**: 6 × runs conceded ÷ legal balls.
- **Dots**: legal balls from which the batting side scored nothing at all
  (byes and leg-byes are not dots).
- **Maidens**: a completed six-ball over in which the bowler conceded nothing.

## Team and innings
- **Total**: all runs including extras.
- **Wickets**: all dismissals except "retired hurt" / "retired not out".
- **Run rate**: 6 × total ÷ legal balls.

## Phases (limited overs)
| Format | Powerplay | Middle | Death |
|---|---|---|---|
| T20 | 1–6 | 7–15 | 16–20 |
| ODI | 1–10 | 11–40 | 41–50 |

Phase run rates use legal balls actually bowled, so a chase that finishes in
the 18th over isn't penalised for overs that never happened. Tests have no
phases.

## Partnerships
Runs (including extras) and legal balls from one wicket to the next. The final
partnership is marked `unbroken`.

## Turning points (heuristics)
- **big_over**: one of the two costliest overs of an innings, if it cost ≥ 15
  (limited overs) or ≥ 12 (Tests).
- **wicket_burst**: 2+ wickets in one over (run outs included; bowler credit
  shown separately).
- **collapse**: 3+ wickets across three consecutive overs, none of which was
  already a wicket burst.
- **required_rate**: the chasing side's position after overs 6, 10, 15 and 40.

## Consistency checks
Every report verifies, per innings:
1. batter runs + extras = total
2. bowler runs = total − byes − leg-byes − penalty runs
3. partnerships sum to the total

A failure is surfaced as a warning and the script exits with code 1.
