"""Collector flattening keeps point-in-time fields and does not invent predictions."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collector"))

import tfl_collector as collector  # noqa: E402


def test_flatten_arrival_stamps_our_clock_not_tfls() -> None:
    request = datetime(2026, 9, 13, 21, 0, 0, tzinfo=timezone.utc)
    response = datetime(2026, 9, 13, 21, 0, 0, 250000, tzinfo=timezone.utc)
    prediction = {
        "id": "abc",
        "vehicleId": "223",
        "naptanId": "940GZZLURGP",
        "stationName": "Regent's Park Underground Station",
        "lineId": "bakerloo",
        "lineName": "Bakerloo",
        "direction": "inbound",
        "platformName": "Southbound - Platform 2",
        "destinationName": "Elephant & Castle Underground Station",
        "destinationNaptanId": "940GZZLUEAC",
        "towards": "Elephant and Castle",
        "currentLocation": "Between Paddington and Edgware Road",
        "timeToStation": 280,
        "expectedArrival": "2026-09-13T21:06:16Z",
        "timestamp": "2026-09-13T21:01:36Z",
        "timeToLive": "2026-09-13T21:06:16Z",
        "modeName": "tube",
        "timing": {"read": "2026-09-13T21:01:32Z", "sent": "2026-09-13T21:01:36Z"},
    }
    record = collector.flatten_arrival(prediction, "poll-1", request, response)
    assert record["poll_id"] == "poll-1"
    assert record["request_ts"] == "2026-09-13T21:00:00.000Z"
    assert record["response_ts"] == "2026-09-13T21:00:00.250Z"
    assert record["timeToStation"] == 280
    assert record["expectedArrival"] == "2026-09-13T21:06:16Z"
    assert record["vehicleId"] == "223"
    assert record["timingRead"] == "2026-09-13T21:01:32Z"
    # Must not overwrite TfL's prediction with anything we computed.
    assert record["timeToStation"] == prediction["timeToStation"]


def test_eleven_lines_one_comma_separated_set() -> None:
    assert len(collector.TUBE_LINE_IDS) == 11
    assert "circle" in collector.TUBE_LINE_IDS
    assert "waterloo-city" in collector.TUBE_LINE_IDS


def test_append_jsonl_never_truncates(tmp_path: Path) -> None:
    path = tmp_path / "arrivals.jsonl"
    collector.append_jsonl(path, [{"n": 1}])
    collector.append_jsonl(path, [{"n": 2}])
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert '"n":1' in lines[0]
    assert '"n":2' in lines[1]
