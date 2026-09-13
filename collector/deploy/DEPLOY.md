# Deploying the collector

Every day this isn't running is a day of data that cannot be recovered.
Pick one host that stays on (Pi, VPS, spare machine) and leave it there.

The collector is stdlib-only. Ingest (`src/ingest.py`) is a separate,
occasional job and does not need to run on the always-on box.

## Before start

1. Clone this repo to `/opt/tube-delay-dynamics` (or equivalent).
2. Copy `.env.example` to `.env` and set `TFL_APP_KEY` from
   [the TfL API portal](https://api-portal.tfl.gov.uk/). Unauthenticated
   requests currently succeed at a lower allowance; do not rely on that
   for months of collection.
3. Create the data directory on a disk that survives redeploys:

   ```bash
   sudo mkdir -p /opt/tube-delay-dynamics/data
   sudo chown -R tfl:tfl /opt/tube-delay-dynamics
   ```

4. Optional venv (matches the systemd `ExecStart` path):

   ```bash
   python3 -m venv /opt/tube-delay-dynamics/.venv
   ```

   The collector does not install `requirements.txt`. That file is for
   ingest and tests on a machine that will compact the JSONL.

## systemd (preferred on a Pi / VPS)

The unit sets `Restart=always`. That is not optional.

```bash
sudo cp /opt/tube-delay-dynamics/collector/deploy/tfl-collector.service \
        /etc/systemd/system/
# Edit User=, WorkingDirectory=, ExecStart= if your paths differ.
sudo systemctl daemon-reload
sudo systemctl enable --now tfl-collector.service
journalctl -u tfl-collector.service -f
```

Health: the process logs `HEALTH CHECK FAILED` at CRITICAL if no
successful poll lands in 10 minutes. To turn that into a systemd-visible
failure as well:

```bash
sudo cp /opt/tube-delay-dynamics/collector/deploy/tfl-collector-health.service \
        /etc/systemd/system/
sudo cp /opt/tube-delay-dynamics/collector/deploy/tfl-collector-health.timer \
        /etc/systemd/system/
sudo systemctl enable --now tfl-collector-health.timer
```

## Docker

```bash
cd /opt/tube-delay-dynamics/collector/deploy
docker compose up -d --build
docker compose logs -f
```

`restart: always` is set in `docker-compose.yml`. The `data/` directory
is bind-mounted from the host so `docker compose down` cannot delete raw
JSONL.

## Verify (20 minutes)

After 20 minutes:

```bash
ls data/raw/arrivals/
python collector/healthcheck.py
python src/ingest.py quality --raw data/raw
```

Expect:

- `data/raw/arrivals/YYYY-MM-DD/arrivals_HH00.jsonl` growing
- roughly 1.5k–4k prediction rows per poll network-wide (the brief's
  400–700 figure is a sanity *floor*; unique vehicles are typically a
  few hundred)
- `data/raw/failures/` empty or sparse; any outage must show up there,
  never as a silent hole
- heartbeat `healthy: true`

If you change `POLL_INTERVAL_SECONDS`, commit that change. Later
analysis of gaps needs to know when the cadence changed.

## What this host must never do

- Overwrite or "clean up" files under `data/raw/`
- Back-fill predictions from a later snapshot
- Fan the 11 lines out into 11 HTTP requests
