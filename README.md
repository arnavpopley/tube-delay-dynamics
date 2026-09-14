# Tube Delay Dynamics

Research dataset and pipeline for two linked questions about the London
Underground:

1. **Line ranking.** Which of the eleven lines absorb and recover from delay,
   and which amplify it.
2. **Forecasting TfL's error.** At what horizon TfL's own live arrival
   predictions stop being unbeatable, using line-state features their model
   appears to ignore.

The standing brief — non-negotiables, schema, phase order — is in
[`PROJECT.md`](PROJECT.md). Read that before changing anything.

**This repo is at Phase 1.** The collector and the ingest/quality scripts
exist. Ground-truth reconstruction does not, on purpose: that logic written
before a full day of real sequences is in hand will be wrong in ways that are
hard to spot later.

## Non-negotiables (short)

- Predictions are logged **point-in-time** with *our* clock. Never back-filled.
- `data/raw/` is append-only. Cleaning writes `data/processed/` only.
- Failed polls get their own records. Silence is not "no trains".
- Time-ordered / blocked splits only, when modelling starts.
- Ignore forecast horizons under ~2 minutes.

## Setup

Python 3.11+. The collector is stdlib-only. Ingest needs DuckDB:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Put a TfL app key in .env (https://api-portal.tfl.gov.uk/).
```

## Collect

One process, all 11 lines in **one** Arrivals request and **one** Status
request per cycle (default every 30s):

```bash
python collector/tfl_collector.py           # until Ctrl-C
python collector/tfl_collector.py --once    # single cycle
python collector/healthcheck.py             # exit 1 if no success in 10 min
```

Writes:

- `data/raw/arrivals/YYYY-MM-DD/arrivals_HH00.jsonl`
- `data/raw/status/YYYY-MM-DD/status_HH00.jsonl`
- `data/raw/failures/YYYY-MM-DD/failures_HH00.jsonl`
- `data/raw/heartbeat.json`

Always-on **collection** cannot run on Vercel or GitHub Pages. Those are
the public site (`web/`). The poller needs a machine that stays on —
Oracle Always Free or a VPS. See
[`collector/deploy/DEPLOY.md`](collector/deploy/DEPLOY.md).

Publish to GitHub from your laptop (this agent has no GitHub login):

```bash
gh auth login
scripts/publish-github.sh
```

A weekday poll is typically **1.5k–4k prediction rows**, not 400–700. Unique
vehicles are a few hundred. The quality report prints both.

## Ingest and sanity

```bash
python src/ingest.py compact      # JSONL -> Parquet, partitioned by date + line
python src/ingest.py quality      # results/quality_latest.md
python src/ingest.py sequences    # 20 (vehicle, station) series to eyeball
python src/ingest.py all          # all three
# or
python run_all.py
```

The **manual check** (Phase 1, still required): open
`results/sequence_samples.md`, pick 20 series, confirm `time_to_station`
mostly decays toward zero. Increases between polls are normal TfL revisions.
Do not start reconstruction until that check has been done on a full day of
data.

## Tests

```bash
pytest -q
```

## Layout

Matches `PROJECT.md` §3. `src/reconstruct.py` and everything downstream are
deliberately absent.
