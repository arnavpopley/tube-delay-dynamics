# Deploying the collector 24/7

This Cursor / cloud-agent VM **sleeps between sessions**. It cannot be the
always-on host. Hours it is asleep are unrecoverable holes with no failure
records — the process was not running to log them.

Pick a machine that stays powered and networked. The collector is
stdlib-only; ingest (`src/ingest.py`) can stay on a laptop.

**Disk:** a busy weekday hour is ~250–280MB of arrival JSONL (~4–6GB/day).
Give the host 40GB+ or pull `data/raw/` off it daily. Never let the disk
fill — writes would stop and look like a quiet network.

**Secrets:** `TFL_APP_KEY` lives in `.env` or the platform secret store.
Do not commit it. Do not paste it into `fly.toml`.

---

## Option A — small VPS (recommended)

A London/EU droplet you SSH into. You control the disk. Hetzner CX22,
DigitalOcean (`lon1`), or any £4–6/mo box is enough.

```bash
# on the VPS
sudo git clone <this-repo> /opt/tube-delay-dynamics
cd /opt/tube-delay-dynamics
sudo cp .env.example .env
sudo nano .env          # set TFL_APP_KEY
sudo mkdir -p data
```

Then either Docker:

```bash
cd /opt/tube-delay-dynamics/collector/deploy
sudo docker compose up -d --build
sudo docker compose logs -f
```

or systemd (`Restart=always` is not optional):

```bash
sudo python3 -m venv /opt/tube-delay-dynamics/.venv
sudo cp /opt/tube-delay-dynamics/collector/deploy/tfl-collector.service \
        /etc/systemd/system/
# Edit User=, paths, ExecStart= if they differ.
sudo systemctl daemon-reload
sudo systemctl enable --now tfl-collector.service
journalctl -u tfl-collector.service -f
```

Pull data onto the machine you analyse on (does not delete remote raw):

```bash
collector/deploy/pull-raw.sh vps user@vps:/opt/tube-delay-dynamics/data/raw/
python src/ingest.py all
```

---

## Option B — Fly.io (git-push, still needs a volume)

Requires a Fly account (`fly auth login`). Region `lhr` (London) keeps
RTT to `api.tfl.gov.uk` short. **Do not** add an HTTP service — Fly
auto-stops Machines that have a proxy and no traffic.

```bash
# from the repo root, once
fly apps create tube-delay-collector
fly volumes create tfl_raw --region lhr --size 40
fly secrets set TFL_APP_KEY=<your-key>
fly deploy
fly machines list
fly machine update <machine-id> --restart always
fly logs
```

Confirm it is not sleeping: `fly status` should show the Machine **started**
overnight, not stopped.

Pull:

```bash
collector/deploy/pull-raw.sh fly tube-delay-collector
python src/ingest.py all
```

---

## What not to use

- This Cursor agent VM (sleeps; already lost 7h and 4h on 14 Sep).
- Render / Railway / Fly **free HTTP** apps that scale to zero.
- GitHub Actions cron (not 24/7, not point-in-time at 30s).
- Any disk that is wiped on deploy. Raw JSONL must be a **volume** or a
  **host bind mount**.

---

## systemd extras (Pi / VPS)

Health: the process logs `HEALTH CHECK FAILED` at CRITICAL if no
successful poll lands in 10 minutes. Optional timer:

```bash
sudo cp collector/deploy/tfl-collector-health.service /etc/systemd/system/
sudo cp collector/deploy/tfl-collector-health.timer /etc/systemd/system/
sudo systemctl enable --now tfl-collector-health.timer
```

## Verify (20 minutes on the always-on host)

```bash
ls data/raw/arrivals/
python collector/healthcheck.py
python src/ingest.py quality --raw data/raw
```

Expect:

- `data/raw/arrivals/YYYY-MM-DD/arrivals_HH00.jsonl` growing every hour
- weekday daytime polls ~1.5k–4k prediction rows
- `data/raw/failures/` empty or sparse; a timestamp hole with **no**
  failure records means the host was down
- heartbeat `healthy: true`

If you change `POLL_INTERVAL_SECONDS`, commit that change.

## What this host must never do

- Overwrite or "clean up" files under `data/raw/`
- Back-fill predictions from a later snapshot
- Fan the 11 lines out into 11 HTTP requests
