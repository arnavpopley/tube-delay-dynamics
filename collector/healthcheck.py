"""Exit 0 if the collector heartbeat is fresh, 1 if stale or missing.

Intended for cron, systemd timers, or a process supervisor. The collector
also logs CRITICAL itself when no successful poll lands within
HEALTH_STALE_SECONDS (default 10 minutes).

    python collector/healthcheck.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STALE_SECONDS = 600


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def heartbeat_path() -> Path:
    configured = os.environ.get("DATA_DIR", "data")
    path = Path(configured)
    if not path.is_absolute():
        path = repo_root() / path
    return path / "raw" / "heartbeat.json"


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def main() -> int:
    path = heartbeat_path()
    stale_after = int(os.environ.get("HEALTH_STALE_SECONDS", DEFAULT_STALE_SECONDS))
    if not path.is_file():
        print(f"CRITICAL: no heartbeat file at {path}", file=sys.stderr)
        return 1
    payload = json.loads(path.read_text(encoding="utf-8"))
    last_success = parse_ts(payload.get("last_success_ts"))
    if last_success is None:
        print("CRITICAL: heartbeat has never recorded a successful poll", file=sys.stderr)
        return 1
    age = (datetime.now(timezone.utc) - last_success).total_seconds()
    if age > stale_after:
        print(
            f"CRITICAL: last successful poll was {age / 60:.1f} minutes ago "
            f"(threshold {stale_after / 60:.1f} min) at {payload.get('last_success_ts')}",
            file=sys.stderr,
        )
        return 1
    print(
        f"OK: last success {payload.get('last_success_ts')} "
        f"({age:.0f}s ago), {payload.get('last_arrival_count')} arrival rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
