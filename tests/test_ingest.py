"""Ingest compaction and quality-report tests on synthetic JSONL.

These tests do not hit TfL and do not infer arrival times.
"""

from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import ingest  # noqa: E402


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _arrival(
    *,
    poll_id: str,
    request: datetime,
    vehicle: str,
    naptan: str,
    line: str,
    tts: int,
    station: str = "Test Station",
) -> dict:
    tfl_ts = request - timedelta(seconds=4)
    expected = request + timedelta(seconds=tts)
    return {
        "request_ts": _ts(request),
        "response_ts": _ts(request + timedelta(milliseconds=200)),
        "poll_id": poll_id,
        "tflPredictionId": f"{vehicle}-{naptan}-{poll_id}",
        "timestamp": _ts(tfl_ts),
        "timeToLive": _ts(expected),
        "vehicleId": vehicle,
        "naptanId": naptan,
        "stationName": station,
        "lineId": line,
        "lineName": line.title(),
        "direction": "inbound",
        "platformName": "Eastbound - Platform 1",
        "destinationName": "Somewhere",
        "destinationNaptanId": "940GZZLUXXX",
        "towards": "Somewhere",
        "currentLocation": "Between A and B",
        "timeToStation": tts,
        "expectedArrival": _ts(expected),
        "modeName": "tube",
        "timingRead": _ts(tfl_ts),
        "timingSent": _ts(tfl_ts + timedelta(seconds=1)),
    }


@pytest.fixture
def raw_tree(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    t0 = datetime(2026, 9, 13, 10, 0, 0, tzinfo=timezone.utc)
    arrivals: list[dict] = []
    # 6 polls at 30s, then a 5-minute hole, then 2 more polls.
    offsets_s = [0, 30, 60, 90, 120, 150, 150 + 300, 150 + 330]
    for i, offset in enumerate(offsets_s):
        request = t0 + timedelta(seconds=offset)
        poll_id = f"poll-{i:02d}"
        arrivals.append(
            _arrival(
                poll_id=poll_id,
                request=request,
                vehicle="V001",
                naptan="940GZZLUVIC",
                line="victoria",
                tts=max(0, 180 - offset),
                station="Victoria",
            )
        )
        arrivals.append(
            _arrival(
                poll_id=poll_id,
                request=request,
                vehicle="V002",
                naptan="940GZZLUOXC",
                line="victoria",
                tts=max(0, 90 - offset),
                station="Oxford Circus",
            )
        )
        arrivals.append(
            _arrival(
                poll_id=poll_id,
                request=request,
                vehicle="N010",
                naptan="940GZZLUEUS",
                line="northern",
                tts=max(0, 120 - offset),
                station="Euston",
            )
        )
    day = raw / "arrivals" / "2026-09-13"
    _write_jsonl(day / "arrivals_1000.jsonl", arrivals)

    failures = [
        {
            "request_ts": _ts(t0 + timedelta(seconds=180)),
            "response_ts": None,
            "poll_id": "poll-fail-01",
            "endpoint": "/Line/bakerloo,central/Arrivals",
            "error_type": "PollError",
            "error_message": "HTTP 503: backend",
            "http_status": 503,
        }
    ]
    _write_jsonl(raw / "failures" / "2026-09-13" / "failures_1000.jsonl", failures)

    status_rows = [
        {
            "request_ts": _ts(t0),
            "response_ts": _ts(t0 + timedelta(milliseconds=80)),
            "poll_id": "poll-00",
            "lineId": "victoria",
            "lineName": "Victoria",
            "statusSeverity": 10,
            "statusSeverityDescription": "Good Service",
            "reason": None,
            "created": "0001-01-01T00:00:00",
        }
    ]
    _write_jsonl(raw / "status" / "2026-09-13" / "status_1000.jsonl", status_rows)
    return raw


def test_compact_partitions_by_date_and_line(raw_tree: Path, tmp_path: Path) -> None:
    processed = tmp_path / "processed"
    counts = ingest.compact(raw_tree, processed)
    assert counts["arrivals"] == 24  # 8 polls × 3 trains
    assert counts["failures"] == 1
    assert counts["status"] == 1
    victoria = list((processed / "arrivals").glob("poll_date=2026-09-13/line_id=victoria/*.parquet"))
    northern = list((processed / "arrivals").glob("poll_date=2026-09-13/line_id=northern/*.parquet"))
    assert victoria, "expected victoria partition"
    assert northern, "expected northern partition"
    raw_arrivals = raw_tree / "arrivals" / "2026-09-13" / "arrivals_1000.jsonl"
    assert raw_arrivals.is_file()
    lines = raw_arrivals.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 24


def test_compact_reads_gzipped_hourly_files(raw_tree: Path, tmp_path: Path) -> None:
    src = raw_tree / "arrivals" / "2026-09-13" / "arrivals_1000.jsonl"
    gz = src.with_name(src.name + ".gz")
    with src.open("rb") as f_in, gzip.open(gz, "wb") as f_out:
        f_out.write(f_in.read())
    src.unlink()
    counts = ingest.compact(raw_tree, tmp_path / "processed")
    assert counts["arrivals"] == 24


def test_compact_refuses_processed_inside_raw(raw_tree: Path) -> None:
    with pytest.raises(ValueError, match="inside raw_dir"):
        ingest.compact(raw_tree, raw_tree / "processed")


def test_quality_report_flags_gap_and_failure(raw_tree: Path, tmp_path: Path) -> None:
    processed = tmp_path / "processed"
    results = tmp_path / "results"
    ingest.compact(raw_tree, processed)
    report_path = ingest.quality_report(raw_tree, processed, results)
    text = report_path.read_text(encoding="utf-8")
    assert "Distinct successful polls: **8**" in text
    assert "Failure records: **1**" in text
    assert "Notable gaps" in text
    assert "300" in text  # the 5-minute hole
    assert "victoria" in text
    assert "northern" in text
    latest = results / "quality_latest.md"
    assert latest.is_file()


def test_sequences_are_sorted_and_not_reconstructed(raw_tree: Path, tmp_path: Path) -> None:
    processed = tmp_path / "processed"
    results = tmp_path / "results"
    ingest.compact(raw_tree, processed)
    path = ingest.dump_sequences(raw_tree, processed, results, n_pairs=5, min_points=3)
    text = path.read_text(encoding="utf-8")
    assert "does **not** infer an arrival time" in text
    assert "V001" in text
    assert "time_to_station_s" in text
