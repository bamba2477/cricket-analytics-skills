# Cricket Analytics Skills

Claude skills and automations that turn free ball-by-ball cricket data from
[Cricsheet](https://cricsheet.org) into accurate match analysis.

The idea: **code does the counting, AI does the storytelling.** A
deterministic engine computes every number and checks that the totals
reconcile, and the skill tells Claude to write only from those numbers. You
get readable match reports without invented stats.

## What's inside

| | |
|---|---|
| **`cricket-match-report` skill** | Ask Claude for a match report and get scorecards, phase analysis, partnerships and the moments that decided the game. |
| **Weekly automation** | A GitHub Action downloads last week's matches every Monday, generates a report for each, and commits them to `reports/`. |
| **AI narratives (optional)** | Add an API key and each report gets a short Claude-written match story. |

See [`examples/sample_report.md`](examples/sample_report.md) for the computed
report and [`examples/sample_story.md`](examples/sample_story.md) for the
narrative written on top of it.

## Install the skill

**Claude Code**
```
/plugin marketplace add bamba2477/cricket-analytics-skills
/plugin install cricket-analytics@cricket-analytics-skills
```
Then try: *"Download the latest IPL matches and write a report on the final."*

**Claude app (claude.ai / desktop)**
Zip the `plugins/cricket-analytics/skills/cricket-match-report` folder and
upload it in your skills settings. Then attach a Cricsheet JSON file and ask
for a match report.

## Use it without Claude

Everything runs on the Python standard library.

```bash
# Get data
python plugins/cricket-analytics/skills/cricket-match-report/scripts/fetch_cricsheet.py --list
python plugins/cricket-analytics/skills/cricket-match-report/scripts/fetch_cricsheet.py ipl -o data/

# One match
python plugins/cricket-analytics/skills/cricket-match-report/scripts/match_report.py data/1234567.json

# A whole folder, with an index
python automation/batch_reports.py data/ -o reports/ --match-type T20
```

## Turn on the weekly reports

1. Push this repo to GitHub. The workflow in `.github/workflows/weekly-reports.yml`
   runs every Monday at 06:00 IST.
2. *(Optional)* Add an `ANTHROPIC_API_KEY` repository secret
   (Settings → Secrets and variables → Actions) for AI-written stories.
3. Run it now from the **Actions** tab → *Weekly match reports* → *Run workflow*.
   You can pick any Cricsheet dataset (e.g. `ipl`, `t20s`) and format.

## How accuracy is enforced

- Scoring follows standard conventions: wides aren't balls faced, byes aren't
  charged to the bowler, run outs aren't bowler wickets, and so on. The rules
  are written out in [`reference/metrics.md`](plugins/cricket-analytics/skills/cricket-match-report/reference/metrics.md).
- Every report checks that batter runs + extras = total, that bowler runs
  reconcile, and that partnerships sum to the total. If a check fails, the
  skill reports the problem instead of writing a story.
- Unit tests cover each rule with hand-worked examples:
  `python -m unittest discover tests`

## Roadmap

- [x] Match report with turning points
- [ ] Player form tracker (rolling averages, strike rates by phase)
- [ ] Batter vs bowler matchup analyser
- [ ] Ball-by-ball win probability
- [ ] Venue and toss analysis

## Data and licence

Match data comes from [Cricsheet](https://cricsheet.org) and is available
under the [Open Data Commons Attribution License](https://opendatacommons.org/licenses/by/1-0/).
Please credit Cricsheet wherever you publish results. The sample match in
`examples/` is synthetic, with fictional teams and players.

Code in this repository is MIT licensed.
