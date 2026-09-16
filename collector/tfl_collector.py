"""Live TfL Unified API poller for London Underground arrivals and line status.

Point-in-time logging: every record stores what TfL returned at the moment
we asked, stamped with *our* clocks (`request_ts` is canonical). Raw files
under data/raw/ are append-only. Failed requests are written to
data/raw/failures/ so a gap is never silent.

All 11 Underground lines are requested in a single Arrivals call and a
single Status call. Do not fan out per line — that burns quota for no gain.

Usage:
    python collector/tfl_collector.py           # run until SIGINT/SIGTERM
    python collector/tfl_collector.py --once    # one poll cycle, then exit
    python collector/healthcheck.py             # exit 1 if stale
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import shutil
import signal
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# The eleven London Underground lines. Changing this set is a collection
# config change and must be committed — it affects what the dataset covers.
TUBE_LINE_IDS: tuple[str, ...] = (
    "bakerloo",
    "central",
    "circle",
    "district",
    "hammersmith-city",
    "jubilee",
    "metropolitan",
    "northern",
    "piccadilly",
    "victoria",
    "waterloo-city",
)

API_BASE = "https://api.tfl.gov.uk"
USER_AGENT = "tube-delay-dynamics/0.1 (research collector; point-in-time logging)"
DEFAULT_POLL_INTERVAL_SECONDS = 30
DEFAULT_HEALTH_STALE_SECONDS = 600
DEFAULT_HTTP_TIMEOUT_SECONDS = 30

# Schema fields stored from each TfL prediction. Extra TfL keys are dropped
# so the raw log stays stable. Staleness lives in timestamp / timeToLive /
# timingRead / timingSent — it is measured, not assumed zero.
ARRIVAL_TFL_FIELDS: tuple[str, ...] = (
    "timestamp",
    "timeToLive",
    "vehicleId",
    "naptanId",
    "stationName",
    "lineId",
    "lineName",
    "direction",
    "platformName",
    "destinationName",
    "destinationNaptanId",
    "towards",
    "currentLocation",
    "timeToStation",
    "expectedArrival",
    "modeName",
)

log = logging.getLogger("tfl_collector")

_shutdown = False


def _request_shutdown(signum: int, _frame: Any) -> None:
    global _shutdown
    log.info("received signal %s; shutting down after this cycle", signum)
    _shutdown = True


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ts_iso(dt: datetime) -> str:
    """UTC ISO-8601 with millisecond precision and Z suffix."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """Minimal .env loader. Does not overwrite variables already in the environment."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def data_dir() -> Path:
    configured = os.environ.get("DATA_DIR", "data")
    path = Path(configured)
    if not path.is_absolute():
        path = repo_root() / path
    return path


def raw_dir() -> Path:
    return data_dir() / "raw"


def heartbeat_path() -> Path:
    return raw_dir() / "heartbeat.json"


def public_status() -> dict[str, Any]:
    """Heartbeat only. Never includes prediction rows or the API key."""
    path = heartbeat_path()
    if not path.is_file():
        return {
            "healthy": False,
            "error": "no heartbeat yet",
            "last_success_ts": None,
            "last_arrival_count": None,
            "seconds_since_success": None,
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "healthy": bool(payload.get("healthy")),
        "last_success_ts": payload.get("last_success_ts"),
        "last_attempt_ts": payload.get("last_attempt_ts"),
        "last_arrival_count": payload.get("last_arrival_count"),
        "seconds_since_success": payload.get("seconds_since_success"),
        "stale_after_seconds": payload.get("stale_after_seconds"),
        "last_error": payload.get("last_error"),
        "written_ts": payload.get("written_ts"),
    }


def start_status_http(port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            route = self.path.split("?", 1)[0]
            if route not in {"/", "/status", "/health"}:
                self.send_error(404)
                return
            body = json.dumps(public_status()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            log.debug("status-http " + format, *args)

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=server.serve_forever, name="status-http", daemon=True)
    thread.start()
    log.info("public status HTTP on 0.0.0.0:%s (heartbeat only, no raw data)", port)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """Append records to an existing JSONL file. Never truncates."""
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Open for append only. Do not use a write that could truncate raw data.
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def hourly_jsonl(kind: str, when: datetime) -> Path:
    """data/raw/<kind>/YYYY-MM-DD/<kind>_HH00.jsonl"""
    day = when.strftime("%Y-%m-%d")
    hour = when.strftime("%H00")
    return raw_dir() / kind / day / f"{kind}_{hour}.jsonl"


def seal_completed_hours(now: datetime, base: Path | None = None) -> int:
    """Gzip hourly JSONL that is no longer being appended.

    The current UTC hour's file stays uncompressed so append + fsync
    keep working. Completed hours are replaced with `.jsonl.gz` (same
    bytes, ~20× smaller on this feed). The uncompressed file is removed
    only after the gzip exists and is non-empty.
    """
    root = base if base is not None else raw_dir()
    current_day = now.strftime("%Y-%m-%d")
    current_hour = now.strftime("%H00")
    sealed = 0
    for kind in ("arrivals", "status", "failures"):
        kind_root = root / kind
        if not kind_root.is_dir():
            continue
        for path in sorted(kind_root.glob("*/*.jsonl")):
            if path.parent.name == current_day and path.name == f"{kind}_{current_hour}.jsonl":
                continue
            gz = path.with_name(path.name + ".gz")
            if gz.exists() and gz.stat().st_size > 0:
                path.unlink(missing_ok=True)
                sealed += 1
                continue
            tmp = gz.with_name(gz.name + ".partial")
            try:
                with path.open("rb") as src, gzip.open(tmp, "wb", compresslevel=6) as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                tmp.replace(gz)
            except Exception:
                log.exception("gzip failed for %s; leaving uncompressed", path)
                tmp.unlink(missing_ok=True)
                continue
            if gz.stat().st_size > 0:
                path.unlink()
                sealed += 1
                log.info("sealed %s (%s bytes gzip)", gz, gz.stat().st_size)
    return sealed


def api_url(path: str, app_key: str) -> str:
    url = f"{API_BASE}{path}"
    if app_key:
        url = f"{url}?{urlencode({'app_key': app_key})}"
    return url


class PollError(Exception):
    def __init__(
        self,
        endpoint: str,
        message: str,
        http_status: int | None = None,
        request_ts: datetime | None = None,
        response_ts: datetime | None = None,
    ) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.http_status = http_status
        self.message = message
        self.request_ts = request_ts
        self.response_ts = response_ts


def http_get_json(path: str, app_key: str, timeout: float) -> tuple[datetime, datetime, Any]:
    """GET JSON. Returns (request_ts, response_ts, parsed body).

    request_ts is taken immediately before the socket call — our clock, not TfL's.
    """
    url = api_url(path, app_key)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    request_ts = utc_now()
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
            http_status = getattr(response, "status", None) or response.getcode()
        response_ts = utc_now()
    except HTTPError as exc:
        response_ts = utc_now()
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise PollError(
            path,
            f"HTTP {exc.code}: {detail or exc.reason}",
            http_status=exc.code,
            request_ts=request_ts,
            response_ts=response_ts,
        ) from exc
    except URLError as exc:
        response_ts = utc_now()
        raise PollError(
            path,
            f"URL error: {exc.reason}",
            http_status=None,
            request_ts=request_ts,
            response_ts=response_ts,
        ) from exc
    except TimeoutError as exc:
        response_ts = utc_now()
        raise PollError(
            path,
            f"timeout after {timeout}s",
            http_status=None,
            request_ts=request_ts,
            response_ts=response_ts,
        ) from exc
    except OSError as exc:
        response_ts = utc_now()
        raise PollError(
            path,
            f"os error: {exc}",
            http_status=None,
            request_ts=request_ts,
            response_ts=response_ts,
        ) from exc

    if http_status != 200:
        raise PollError(
            path,
            f"HTTP {http_status}",
            http_status=http_status,
            request_ts=request_ts,
            response_ts=response_ts,
        )

    try:
        parsed = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise PollError(
            path,
            f"invalid JSON: {exc}",
            http_status=http_status,
            request_ts=request_ts,
            response_ts=response_ts,
        ) from exc
    return request_ts, response_ts, parsed


def failure_record(
    *,
    poll_id: str,
    endpoint: str,
    request_ts: datetime,
    response_ts: datetime | None,
    error: PollError,
) -> dict[str, Any]:
    return {
        "request_ts": ts_iso(request_ts),
        "response_ts": ts_iso(response_ts) if response_ts is not None else None,
        "poll_id": poll_id,
        "endpoint": endpoint,
        "error_type": "PollError",
        "error_message": error.message,
        "http_status": error.http_status,
    }


def flatten_arrival(prediction: dict[str, Any], poll_id: str, request_ts: datetime, response_ts: datetime) -> dict[str, Any]:
    record: dict[str, Any] = {
        "request_ts": ts_iso(request_ts),
        "response_ts": ts_iso(response_ts),
        "poll_id": poll_id,
        "tflPredictionId": prediction.get("id"),
    }
    for field in ARRIVAL_TFL_FIELDS:
        record[field] = prediction.get(field)
    timing = prediction.get("timing") or {}
    record["timingRead"] = timing.get("read")
    record["timingSent"] = timing.get("sent")
    return record


def flatten_status(line: dict[str, Any], poll_id: str, request_ts: datetime, response_ts: datetime) -> list[dict[str, Any]]:
    statuses = line.get("lineStatuses") or [{}]
    records: list[dict[str, Any]] = []
    for status in statuses:
        records.append(
            {
                "request_ts": ts_iso(request_ts),
                "response_ts": ts_iso(response_ts),
                "poll_id": poll_id,
                "lineId": line.get("id"),
                "lineName": line.get("name"),
                "statusSeverity": status.get("statusSeverity"),
                "statusSeverityDescription": status.get("statusSeverityDescription"),
                "reason": status.get("reason"),
                "created": status.get("created"),
            }
        )
    return records


def write_heartbeat(
    *,
    last_success_ts: datetime | None,
    last_attempt_ts: datetime,
    last_poll_id: str | None,
    last_arrival_count: int | None,
    last_error: str | None,
    stale_seconds: int,
) -> None:
    now = utc_now()
    seconds_since_success: float | None
    if last_success_ts is None:
        seconds_since_success = None
        healthy = False
    else:
        seconds_since_success = (now - last_success_ts).total_seconds()
        healthy = seconds_since_success <= stale_seconds
    atomic_write_json(
        heartbeat_path(),
        {
            "written_ts": ts_iso(now),
            "last_success_ts": ts_iso(last_success_ts) if last_success_ts else None,
            "last_attempt_ts": ts_iso(last_attempt_ts),
            "last_poll_id": last_poll_id,
            "last_arrival_count": last_arrival_count,
            "last_error": last_error,
            "seconds_since_success": seconds_since_success,
            "stale_after_seconds": stale_seconds,
            "healthy": healthy,
        },
    )


def log_health(last_success_ts: datetime | None, stale_seconds: int) -> None:
    if last_success_ts is None:
        log.critical(
            "HEALTH CHECK FAILED: collector has never completed a successful poll "
            "(threshold %s seconds). Failures must be in data/raw/failures/ — "
            "do not treat silence as 'no trains'.",
            stale_seconds,
        )
        return
    age = (utc_now() - last_success_ts).total_seconds()
    if age > stale_seconds:
        log.critical(
            "HEALTH CHECK FAILED: no successful poll for %.1f minutes "
            "(threshold %.1f minutes). Last success at %s. "
            "This is a data gap, not an empty network.",
            age / 60.0,
            stale_seconds / 60.0,
            ts_iso(last_success_ts),
        )


def poll_once(app_key: str, timeout: float) -> tuple[bool, int, str | None, str]:
    """Run one arrivals+status cycle.

    Returns (success, arrival_count, error_message, poll_id). Success means
    the Arrivals request succeeded. Status failures are logged but do not
    by themselves mark the cycle as a total failure — arrivals are the
    dataset. An arrivals failure *is* a failed poll.
    """
    poll_id = str(uuid.uuid4())
    line_ids = ",".join(TUBE_LINE_IDS)
    arrivals_path = f"/Line/{line_ids}/Arrivals"
    status_path = f"/Line/{line_ids}/Status"

    arrival_count = 0
    arrivals_ok = False
    error_message: str | None = None

    try:
        req_ts, resp_ts, payload = http_get_json(arrivals_path, app_key, timeout)
        if not isinstance(payload, list):
            raise PollError(arrivals_path, f"expected list, got {type(payload).__name__}", http_status=200)
        records = [flatten_arrival(item, poll_id, req_ts, resp_ts) for item in payload if isinstance(item, dict)]
        append_jsonl(hourly_jsonl("arrivals", req_ts), records)
        arrival_count = len(records)
        arrivals_ok = True
        log.info(
            "poll %s arrivals: %s prediction rows in %.2fs",
            poll_id,
            arrival_count,
            (resp_ts - req_ts).total_seconds(),
        )
    except PollError as exc:
        error_message = f"{exc.endpoint}: {exc.message}"
        log.error("poll %s arrivals FAILED: %s", poll_id, error_message)
        fail_ts = exc.request_ts or utc_now()
        append_jsonl(
            hourly_jsonl("failures", fail_ts),
            [
                failure_record(
                    poll_id=poll_id,
                    endpoint=exc.endpoint,
                    request_ts=fail_ts,
                    response_ts=exc.response_ts,
                    error=exc,
                )
            ],
        )

    try:
        req_ts, resp_ts, payload = http_get_json(status_path, app_key, timeout)
        if not isinstance(payload, list):
            raise PollError(status_path, f"expected list, got {type(payload).__name__}", http_status=200)
        records: list[dict[str, Any]] = []
        for item in payload:
            if isinstance(item, dict):
                records.extend(flatten_status(item, poll_id, req_ts, resp_ts))
        append_jsonl(hourly_jsonl("status", req_ts), records)
        log.info("poll %s status: %s line-status rows", poll_id, len(records))
    except PollError as exc:
        status_error = f"{exc.endpoint}: {exc.message}"
        log.error("poll %s status FAILED: %s", poll_id, status_error)
        fail_ts = exc.request_ts or utc_now()
        append_jsonl(
            hourly_jsonl("failures", fail_ts),
            [
                failure_record(
                    poll_id=poll_id,
                    endpoint=exc.endpoint,
                    request_ts=fail_ts,
                    response_ts=exc.response_ts,
                    error=exc,
                )
            ],
        )
        if error_message is None:
            error_message = status_error

    return arrivals_ok, arrival_count, error_message, poll_id


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        stream=sys.stdout,
    )
    logging.Formatter.converter = time.gmtime


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TfL Underground point-in-time collector")
    parser.add_argument("--once", action="store_true", help="run a single poll cycle and exit")
    parser.add_argument(
        "--seal-only",
        action="store_true",
        help="gzip completed hourly JSONL and exit (does not poll)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="seconds between polls (default: POLL_INTERVAL_SECONDS or 30)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv(repo_root() / ".env")
    args = parse_args(argv)
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))

    app_key = os.environ.get("TFL_APP_KEY", "").strip()
    if args.seal_only:
        n = seal_completed_hours(utc_now())
        log.info("sealed %s completed hourly files", n)
        return 0
    if not app_key:
        log.warning(
            "TFL_APP_KEY is empty. Unauthenticated requests may be rate-limited "
            "or rejected. Set it in .env for always-on collection "
            "(https://api-portal.tfl.gov.uk/)."
        )

    interval = args.interval if args.interval is not None else env_int("POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS)
    stale_seconds = env_int("HEALTH_STALE_SECONDS", DEFAULT_HEALTH_STALE_SECONDS)
    timeout = float(env_int("HTTP_TIMEOUT_SECONDS", DEFAULT_HTTP_TIMEOUT_SECONDS))

    raw_dir().mkdir(parents=True, exist_ok=True)
    status_port = env_int("HEALTH_HTTP_PORT", 0)
    if status_port > 0:
        start_status_http(status_port)
    log.info(
        "collector starting: interval=%ss stale_after=%ss lines=%s data=%s",
        interval,
        stale_seconds,
        ",".join(TUBE_LINE_IDS),
        raw_dir(),
    )

    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    last_success_ts: datetime | None = None
    last_arrival_count: int | None = None

    while not _shutdown:
        attempt_ts = utc_now()
        poll_id: str | None = None
        try:
            ok, arrival_count, error_message, poll_id = poll_once(app_key, timeout)
        except Exception:
            log.exception("unexpected error during poll cycle")
            ok, arrival_count, error_message = False, 0, "unexpected exception"
            poll_id = str(uuid.uuid4())
            append_jsonl(
                hourly_jsonl("failures", attempt_ts),
                [
                    {
                        "request_ts": ts_iso(attempt_ts),
                        "response_ts": ts_iso(utc_now()),
                        "poll_id": poll_id,
                        "endpoint": "cycle",
                        "error_type": "unexpected",
                        "error_message": "unexpected exception; see collector logs",
                        "http_status": None,
                    }
                ],
            )

        if ok:
            last_success_ts = utc_now()
            last_arrival_count = arrival_count
        write_heartbeat(
            last_success_ts=last_success_ts,
            last_attempt_ts=attempt_ts,
            last_poll_id=poll_id,
            last_arrival_count=last_arrival_count,
            last_error=None if ok else error_message,
            stale_seconds=stale_seconds,
        )
        log_health(last_success_ts, stale_seconds)
        try:
            seal_completed_hours(utc_now())
        except Exception:
            log.exception("hourly gzip seal failed; will retry next cycle")

        if args.once:
            return 0 if ok else 1

        # Sleep in short slices so SIGTERM is noticed quickly.
        deadline = time.monotonic() + max(1, interval)
        while not _shutdown and time.monotonic() < deadline:
            time.sleep(0.25)

    log.info("collector stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
