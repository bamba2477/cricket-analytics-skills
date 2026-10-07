---
name: cricket-match-report
description: Turn Cricsheet ball-by-ball cricket data into an accurate match report with scorecards, phase analysis, partnerships and the moments that decided the game. Use when the user shares a Cricsheet JSON file, asks for a match report, post-match analysis, scorecard breakdown or "what decided this match", or wants recent matches downloaded and summarised. Works for T20, ODI and Test data.
---

# Cricket match report

Produce a match report where **every number comes from the data**. The script
does the counting; you do the storytelling. Never estimate, round differently
or "fill in" a stat the script didn't produce.

## Workflow

1. **Get the data.**
   - If the user attached a Cricsheet `.json` file, use it.
   - Otherwise look for match files already on disk first (a `data/` folder,
     or wherever the user keeps them). Find the match by reading each file's
     `info` block (`teams`, `dates`, `event.stage` such as "Final").
   - Only if nothing local fits, download with
     `python scripts/fetch_cricsheet.py <dataset> -o data/` (run with `--list`
     for names; `recently_added_7` covers the last week). Then pick the match
     they mean by teams/date from the files' `info` blocks.
   - Always compute from the source `.json`. Don't reuse an existing `.md`
     report in the folder; regenerate it.
   - If the file isn't Cricsheet JSON (no `info` and `innings` keys), say so and
     ask for the right file rather than guessing at another format.

2. **Compute.** Run
   `python scripts/match_report.py MATCH.json --format json`
   for the structured result (use `--format markdown` for a ready scorecard).
   Exit code 1 means the `checks` list is non-empty: the data failed an
   internal consistency check. Report the warning to the user and do not
   write a narrative on top of numbers that don't reconcile.

3. **Find the story.** Read `turning_points`, the `phases` tables and
   `fall_of_wickets`. Good reports usually turn on one or two of:
   - a phase where one side clearly out-scored the other (compare run rates
     phase by phase across both innings)
   - a wicket burst or collapse (`wicket_burst`, `collapse`)
   - an expensive over at the death (`big_over`)
   - the required rate climbing or falling in a chase (`required_rate`)
   - a match-defining partnership (the largest entry in `partnerships`)

   Each innings is named after its **batting** side (`team` /
   `batting_team`). Its bowling figures belong to `bowling_team`, the other
   side. The same holds in `turning_points`: the bowler named there bowls for
   the team that is *not* `team`.

4. **Write the report** in this order:
   1. Headline: one sentence with the result and the deciding factor.
   2. The story: 2–4 short paragraphs on how the match was won and lost,
      citing specific overs, phases and players.
   3. Top performers (from `top_performers`).
   4. Full scorecards (paste the script's markdown tables unchanged).
   5. Footer crediting Cricsheet as the data source (required by its licence).

## Rules for accuracy

- **Trust the data over your memory.** Matches played after your training
  data ends are real, and squads change every season through auctions and
  transfers. A player appearing for a team you don't expect, or a match you
  don't recognise, is not evidence of fake data. Never call a Cricsheet file
  fabricated because it disagrees with what you remember. Only question a file
  if the script's consistency checks fail or the JSON is malformed, and then
  say exactly which check failed.
- Before reporting any other mismatch (e.g. a dismissal's bowler missing from
  a bowling table), confirm you're comparing within the same innings: a
  dismissal in Team A's innings is credited to a bowler in Team B's bowling
  figures for that innings.
- Don't add facts from memory either (career records, previous meetings,
  who "usually" plays where). Everything in the report comes from the file.
- Quote figures exactly as computed: `4/31`, `46 (34)`, `RR 8.81`.
- Don't claim things the data can't show: shot types, field placings,
  pitch conditions, DRS, injuries, crowd or player emotions.
- "Turning point" is a judgement; frame it as one ("the match swung in the
  16th over, when…") and back it with a number.
- Cricsheet uses players' registered names (e.g. "V Kohli"). Keep them as-is
  unless the user asks for full names.
- Super overs appear as separate innings with `super_over: true`; describe
  them after the main match.
- Rain-affected results show a `method` (e.g. D/L) in the result line; mention
  that targets were revised and don't recompute them.

## Reference

`reference/metrics.md` defines every metric (how wides, no-balls, byes and
run outs are counted) — read it if a user questions a number.
