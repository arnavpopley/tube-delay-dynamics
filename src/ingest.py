"""Compact append-only JSONL into daily Parquet and emit a data-quality report.

Never writes to data/raw/. Re-running rebuilds data/processed/ and
results/ from the immutable raw logs.

    python src/ingest.py compact
    python src/ingest.py quality
    python src/ingest.py sequences
    python src/ingest.py all
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent


def default_raw_dir() -> Path:
    return REPO_ROOT / "data" / "raw"


def default_processed_dir() -> Path:
    return REPO_ROOT / "data" / "processed"


def default_results_dir() -> Path:
    return REPO_ROOT / "results"


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    # json is bundled in current DuckDB; INSTALL needs a network round-trip.
    try:
        con.execute("LOAD json")
    except duckdb.Error:
        con.execute("INSTALL json")
        con.execute("LOAD json")
    return con


def glob_or_none(directory: Path, pattern: str) -> str | None:
    matches = sorted(directory.glob(pattern))
    if not matches:
        return None
    # DuckDB needs a glob string, not a Python list, to read many files.
    return str(directory / pattern)


def jsonl_read_sql(raw_dir: Path, kind: str) -> str | None:
    """DuckDB scan of uncompressed and gzipped hourly JSONL."""
    parts: list[str] = []
    for pattern in (f"{kind}/*/*.jsonl", f"{kind}/*/*.jsonl.gz"):
        if glob_or_none(raw_dir, pattern):
            parts.append(
                f"read_json_auto({sql_lit(str(raw_dir / pattern))}, "
                "format := 'newline_delimited', ignore_errors := false)"
            )
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    selects = " UNION ALL BY NAME ".join(f"SELECT * FROM {part}" for part in parts)
    return f"({selects})"


def sql_lit(value: str) -> str:
    """Quote a filesystem path for interpolation into DuckDB SQL.

    CREATE VIEW / COPY cannot take prepared parameters for file paths.
    """
    return "'" + value.replace("'", "''") + "'"


def compact(raw_dir: Path, processed_dir: Path) -> dict[str, int]:
    """Read hourly JSONL, write Parquet partitioned by date and line.

    Processed output is derived and may be replaced. Raw is never touched.
    """
    raw_dir = raw_dir.resolve()
    processed_dir = processed_dir.resolve()
    if processed_dir.is_relative_to(raw_dir):
        raise ValueError("processed_dir must not sit inside raw_dir")

    arrivals_sql = jsonl_read_sql(raw_dir, "arrivals")
    status_sql = jsonl_read_sql(raw_dir, "status")
    failures_sql = jsonl_read_sql(raw_dir, "failures")

    con = connect()
    counts = {"arrivals": 0, "status": 0, "failures": 0}

    if arrivals_sql:
        arrivals_out = processed_dir / "arrivals"
        arrivals_out.mkdir(parents=True, exist_ok=True)
        con.execute(
            f"""
            CREATE OR REPLACE VIEW arrivals_src AS
            SELECT
                request_ts::TIMESTAMPTZ AS request_ts,
                response_ts::TIMESTAMPTZ AS response_ts,
                poll_id,
                tflPredictionId,
                "timestamp"::TIMESTAMPTZ AS tfl_timestamp,
                timeToLive::TIMESTAMPTZ AS time_to_live,
                vehicleId AS vehicle_id,
                naptanId AS naptan_id,
                stationName AS station_name,
                lineId AS line_id,
                lineName AS line_name,
                direction,
                platformName AS platform_name,
                destinationName AS destination_name,
                destinationNaptanId AS destination_naptan_id,
                towards,
                currentLocation AS current_location,
                timeToStation::INTEGER AS time_to_station,
                expectedArrival::TIMESTAMPTZ AS expected_arrival,
                modeName AS mode_name,
                timingRead::TIMESTAMPTZ AS timing_read,
                timingSent::TIMESTAMPTZ AS timing_sent,
                CAST(request_ts AS DATE) AS poll_date
            FROM {arrivals_sql}
            """
        )
        counts["arrivals"] = con.execute("SELECT COUNT(*) FROM arrivals_src").fetchone()[0]
        # OVERWRITE replaces derived parquet only. Raw JSONL is not on this path.
        con.execute(
            f"""
            COPY (
                SELECT * FROM arrivals_src
            ) TO {sql_lit(str(arrivals_out))} (
                FORMAT PARQUET,
                PARTITION_BY (poll_date, line_id),
                OVERWRITE,
                COMPRESSION ZSTD
            )
            """
        )

    if status_sql:
        status_out = processed_dir / "status"
        status_out.mkdir(parents=True, exist_ok=True)
        con.execute(
            f"""
            CREATE OR REPLACE VIEW status_src AS
            SELECT
                request_ts::TIMESTAMPTZ AS request_ts,
                response_ts::TIMESTAMPTZ AS response_ts,
                poll_id,
                lineId AS line_id,
                lineName AS line_name,
                statusSeverity AS status_severity,
                statusSeverityDescription AS status_severity_description,
                reason,
                created,
                CAST(request_ts AS DATE) AS poll_date
            FROM {status_sql}
            """
        )
        counts["status"] = con.execute("SELECT COUNT(*) FROM status_src").fetchone()[0]
        con.execute(
            f"""
            COPY (
                SELECT * FROM status_src
            ) TO {sql_lit(str(status_out))} (
                FORMAT PARQUET,
                PARTITION_BY (poll_date, line_id),
                OVERWRITE,
                COMPRESSION ZSTD
            )
            """
        )

    if failures_sql:
        failures_out = processed_dir / "failures"
        failures_out.mkdir(parents=True, exist_ok=True)
        con.execute(
            f"""
            CREATE OR REPLACE VIEW failures_src AS
            SELECT
                request_ts::TIMESTAMPTZ AS request_ts,
                response_ts::TIMESTAMPTZ AS response_ts,
                poll_id,
                endpoint,
                error_type,
                error_message,
                http_status,
                CAST(request_ts AS DATE) AS poll_date
            FROM {failures_sql}
            """
        )
        counts["failures"] = con.execute("SELECT COUNT(*) FROM failures_src").fetchone()[0]
        con.execute(
            f"""
            COPY (
                SELECT * FROM failures_src
            ) TO {sql_lit(str(failures_out))} (
                FORMAT PARQUET,
                PARTITION_BY (poll_date),
                OVERWRITE,
                COMPRESSION ZSTD
            )
            """
        )

    con.close()
    return counts


def _arrivals_relation_sql(processed_dir: Path, raw_dir: Path) -> str:
    """Prefer compacted Parquet; fall back to raw JSONL so quality can run
    before the first compact."""
    parquet = processed_dir / "arrivals"
    if any(parquet.rglob("*.parquet")):
        return f"read_parquet({sql_lit(str(parquet / '**/*.parquet'))}, hive_partitioning := true)"
    arrivals_sql = jsonl_read_sql(raw_dir, "arrivals")
    if arrivals_sql is None:
        raise FileNotFoundError(f"no arrivals data under {raw_dir} or {processed_dir}")
    return f"""(
            SELECT
                request_ts::TIMESTAMPTZ AS request_ts,
                poll_id,
                vehicleId AS vehicle_id,
                naptanId AS naptan_id,
                stationName AS station_name,
                lineId AS line_id,
                platformName AS platform_name,
                timeToStation::INTEGER AS time_to_station,
                expectedArrival::TIMESTAMPTZ AS expected_arrival,
                "timestamp"::TIMESTAMPTZ AS tfl_timestamp,
                CAST(request_ts AS DATE) AS poll_date
            FROM {arrivals_sql}
        )"""


def _failures_relation_sql(processed_dir: Path, raw_dir: Path) -> str | None:
    parquet = processed_dir / "failures"
    if any(parquet.rglob("*.parquet")):
        return f"read_parquet({sql_lit(str(parquet / '**/*.parquet'))}, hive_partitioning := true)"
    failures_sql = jsonl_read_sql(raw_dir, "failures")
    if failures_sql is None:
        return None
    return f"""(
            SELECT
                request_ts::TIMESTAMPTZ AS request_ts,
                poll_id,
                endpoint,
                error_message,
                CAST(request_ts AS DATE) AS poll_date
            FROM {failures_sql}
        )"""


def quality_report(raw_dir: Path, processed_dir: Path, results_dir: Path) -> Path:
    """Polls per hour, records per poll, gaps, failure rate, vehicles per line/day."""
    con = connect()
    rel_sql = _arrivals_relation_sql(processed_dir, raw_dir)
    con.execute(f"CREATE OR REPLACE VIEW arrivals AS SELECT * FROM {rel_sql}")

    fail_sql = _failures_relation_sql(processed_dir, raw_dir)
    if fail_sql:
        con.execute(f"CREATE OR REPLACE VIEW failures AS SELECT * FROM {fail_sql}")
    else:
        con.execute(
            """
            CREATE OR REPLACE VIEW failures AS
            SELECT
                NULL::TIMESTAMPTZ AS request_ts,
                NULL::VARCHAR AS poll_id,
                NULL::VARCHAR AS endpoint,
                NULL::VARCHAR AS error_message,
                NULL::DATE AS poll_date
            WHERE 1 = 0
            """
        )

    poll_summary = con.execute(
        """
        SELECT
            COUNT(DISTINCT poll_id) AS n_polls,
            COUNT(*) AS n_records,
            COUNT(DISTINCT vehicle_id) AS n_vehicles,
            COUNT(DISTINCT line_id) AS n_lines,
            MIN(request_ts) AS first_ts,
            MAX(request_ts) AS last_ts
        FROM arrivals
        """
    ).fetchone()

    per_poll = con.execute(
        """
        SELECT
            MIN(n) AS min_records,
            quantile_cont(n, 0.5) AS median_records,
            MAX(n) AS max_records,
            AVG(n) AS mean_records
        FROM (
            SELECT poll_id, COUNT(*) AS n
            FROM arrivals
            GROUP BY poll_id
        )
        """
    ).fetchone()

    polls_per_hour = con.execute(
        """
        SELECT
            date_trunc('hour', request_ts) AS hour_utc,
            COUNT(DISTINCT poll_id) AS n_polls,
            COUNT(*) AS n_records,
            COUNT(DISTINCT vehicle_id) AS n_vehicles
        FROM arrivals
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchall()

    vehicles_per_line_day = con.execute(
        """
        SELECT
            poll_date,
            line_id,
            COUNT(DISTINCT vehicle_id) AS n_vehicles,
            COUNT(DISTINCT poll_id) AS n_polls,
            COUNT(*) AS n_records
        FROM arrivals
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).fetchall()

    poll_times = con.execute(
        """
        SELECT DISTINCT poll_id, MIN(request_ts) AS request_ts
        FROM arrivals
        GROUP BY poll_id
        ORDER BY 2
        """
    ).fetchall()

    failure_rows = con.execute(
        """
        SELECT
            COUNT(*) AS n_failure_records,
            COUNT(DISTINCT poll_id) AS n_failed_polls
        FROM failures
        """
    ).fetchone()

    gaps = _gap_list(poll_times)
    n_polls = poll_summary[0] or 0
    n_failed_polls = failure_rows[1] or 0
    denom = n_polls + n_failed_polls
    failure_rate = (n_failed_polls / denom) if denom else None

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines: list[str] = [
        "# Data quality report",
        "",
        f"Generated (UTC): {generated}",
        "",
        "This report is derived from collected data. It does not reconstruct",
        "arrivals. A gap here is only a gap if `data/raw/failures/` does not",
        "already explain it.",
        "",
        "## Coverage",
        "",
        f"- Distinct successful polls: **{n_polls}**",
        f"- Arrival prediction rows: **{poll_summary[1]}**",
        f"- Distinct vehicles: **{poll_summary[2]}**",
        f"- Distinct lines observed: **{poll_summary[3]}**",
        f"- First `request_ts`: {poll_summary[4]}",
        f"- Last `request_ts`: {poll_summary[5]}",
        "",
        "## Records per poll",
        "",
        f"- min: {per_poll[0]}",
        f"- median: {per_poll[1]}",
        f"- mean: {None if per_poll[3] is None else round(float(per_poll[3]), 1)}",
        f"- max: {per_poll[2]}",
        "",
        "A full network poll is typically 1.5k–4k prediction rows (one per",
        "currently published train–station pair). Unique vehicles are a few",
        "hundred. Counts below ~400 rows on a weekday daytime poll are a smell.",
        "",
        "## Polls per hour (UTC)",
        "",
        "| hour_utc | polls | records | vehicles |",
        "|---|---:|---:|---:|",
    ]
    for hour_utc, n_hour_polls, n_records, n_vehicles in polls_per_hour:
        lines.append(f"| {hour_utc} | {n_hour_polls} | {n_records} | {n_vehicles} |")

    lines.extend(
        [
            "",
            "## Gaps between successful polls",
            "",
        ]
    )
    if not gaps:
        lines.append("No inter-poll gaps could be computed (need at least two polls).")
    else:
        median_gap = sorted(g["gap_seconds"] for g in gaps)[len(gaps) // 2]
        # A gap is notable if it is > 2.5× the median cadence (e.g. missed polls).
        notable = [g for g in gaps if g["gap_seconds"] > max(90.0, 2.5 * median_gap)]
        lines.append(f"- Median cadence: **{median_gap:.1f}s**")
        lines.append(f"- Notable gaps (> 2.5× median, and > 90s): **{len(notable)}**")
        lines.append("")
        if notable:
            lines.extend(
                [
                    "| from_utc | to_utc | gap_seconds |",
                    "|---|---|---:|",
                ]
            )
            for gap in notable[:50]:
                lines.append(
                    f"| {gap['from_ts']} | {gap['to_ts']} | {gap['gap_seconds']:.1f} |"
                )
            if len(notable) > 50:
                lines.append(f"| … | {len(notable) - 50} more | |")
        else:
            lines.append("No notable gaps.")

    lines.extend(
        [
            "",
            "## Failures",
            "",
            f"- Failure records: **{failure_rows[0]}**",
            f"- Distinct poll_ids in failures: **{n_failed_polls}**",
            f"- Failure rate (failed polls / (successful + failed)): **{_pct(failure_rate)}**",
            "",
            "Zero failure records and a large timestamp hole means the collector",
            "was down — that hole is unrecoverable and is *not* 'no trains ran'.",
            "",
            "## Distinct vehicles per line per day",
            "",
            "| poll_date | line_id | vehicles | polls | records |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for poll_date, line_id, n_vehicles, n_line_polls, n_records in vehicles_per_line_day:
        lines.append(f"| {poll_date} | {line_id} | {n_vehicles} | {n_line_polls} | {n_records} |")

    lines.extend(["", "## Staleness (TfL clock vs our `request_ts`)", ""])
    try:
        stale = con.execute(
            """
            SELECT
                quantile_cont(epoch(request_ts) - epoch(tfl_timestamp), 0.5) AS median_lag_s,
                quantile_cont(epoch(request_ts) - epoch(tfl_timestamp), 0.9) AS p90_lag_s,
                MAX(epoch(request_ts) - epoch(tfl_timestamp)) AS max_lag_s
            FROM arrivals
            WHERE tfl_timestamp IS NOT NULL
            """
        ).fetchone()
        lines.append(f"- median `request_ts - tfl_timestamp`: {stale[0]:.2f}s")
        lines.append(f"- p90: {stale[1]:.2f}s")
        lines.append(f"- max: {stale[2]:.2f}s")
        lines.append("")
        lines.append("This lag is cache staleness. Treat it as a measured quantity.")
    except duckdb.Error:
        lines.append("Staleness columns not available in this source.")

    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = results_dir / f"quality_{stamp}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    latest = results_dir / "quality_latest.md"
    latest.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    con.close()
    return out


def _gap_list(poll_times: list[tuple[Any, Any]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    previous = None
    for _poll_id, request_ts in poll_times:
        if previous is not None:
            gap = (request_ts - previous).total_seconds()
            gaps.append({"from_ts": previous, "to_ts": request_ts, "gap_seconds": gap})
        previous = request_ts
    return gaps


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{100.0 * value:.2f}%"


def dump_sequences(
    raw_dir: Path,
    processed_dir: Path,
    results_dir: Path,
    n_pairs: int = 20,
    min_points: int = 5,
) -> Path:
    """Pull N (vehicle, station, platform) series for the Phase 1 eyeball check.

    Does not estimate arrival. Sorts by request_ts and prints time_to_station
    so a human can confirm it decays (mostly) toward zero.

    Placeholder vehicleId `000` is excluded from the sample (it is still in
    raw data). Live polls often attach many parallel predictions to that id,
    which is not a usable single-train series.
    """
    con = connect()
    rel_sql = _arrivals_relation_sql(processed_dir, raw_dir)
    con.execute(f"CREATE OR REPLACE VIEW arrivals AS SELECT * FROM {rel_sql}")

    pairs = con.execute(
        """
        SELECT vehicle_id, naptan_id, station_name, line_id, platform_name,
               COUNT(DISTINCT poll_id) AS n_polls,
               COUNT(*) AS n_obs,
               MIN(request_ts) AS first_ts, MAX(request_ts) AS last_ts
        FROM arrivals
        WHERE vehicle_id IS NOT NULL
          AND vehicle_id NOT IN ('', '000')
          AND naptan_id IS NOT NULL
        GROUP BY 1, 2, 3, 4, 5
        HAVING COUNT(DISTINCT poll_id) >= ?
        ORDER BY n_polls DESC, n_obs DESC
        LIMIT ?
        """,
        [min_points, n_pairs],
    ).fetchall()

    lines: list[str] = [
        "# Sequence samples for manual check",
        "",
        "Phase 1 eyeball test: `time_to_station` should generally decay toward",
        "zero along each (vehicleId, naptanId, platform) series. Increases",
        "between polls are normal (TfL revising) and are themselves data.",
        "This file does **not** infer an arrival time.",
        "",
        "Placeholder `vehicleId=000` is omitted from this sample; it remains",
        "in `data/raw/`. Do not treat it as one train.",
        "",
        f"Pairs listed: {len(pairs)} (requested {n_pairs}, min {min_points} polls).",
        "",
    ]

    if not pairs:
        lines.append("No pairs with enough observations yet. Collect longer.")
    else:
        for (
            vehicle_id,
            naptan_id,
            station_name,
            line_id,
            platform_name,
            n_polls,
            n_obs,
            first_ts,
            last_ts,
        ) in pairs:
            series = con.execute(
                """
                SELECT request_ts, time_to_station, expected_arrival
                FROM arrivals
                WHERE vehicle_id = ?
                  AND naptan_id = ?
                  AND platform_name IS NOT DISTINCT FROM ?
                ORDER BY request_ts
                """,
                [vehicle_id, naptan_id, platform_name],
            ).fetchall()
            lines.append(
                f"## {line_id} / {vehicle_id} → {station_name} (`{naptan_id}`)"
            )
            lines.append("")
            lines.append(f"Platform: {platform_name}")
            lines.append(f"{n_polls} polls, {n_obs} rows, {first_ts} → {last_ts}")
            lines.append("")
            lines.append("| request_ts | time_to_station_s | expected_arrival |")
            lines.append("|---|---:|---|")
            for request_ts, tts, expected in series:
                lines.append(f"| {request_ts} | {tts} | {expected} |")
            lines.append("")

    results_dir.mkdir(parents=True, exist_ok=True)
    out = results_dir / "sequence_samples.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    con.close()
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compact TfL JSONL and report data quality")
    parser.add_argument(
        "command",
        choices=("compact", "quality", "sequences", "all"),
        help="compact: JSONL -> Parquet; quality: write results/quality_*.md; "
        "sequences: dump 20 series for the manual check; all: compact then both reports",
    )
    parser.add_argument("--raw", type=Path, default=default_raw_dir())
    parser.add_argument("--processed", type=Path, default=default_processed_dir())
    parser.add_argument("--results", type=Path, default=default_results_dir())
    parser.add_argument("--pairs", type=int, default=20, help="sequence pairs to dump")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command in {"compact", "all"}:
        counts = compact(args.raw, args.processed)
        print(f"compacted {counts}", file=sys.stderr)
        if args.command == "compact" and counts["arrivals"] == 0:
            print("no arrivals JSONL found; nothing to compact", file=sys.stderr)
    if args.command in {"quality", "all"}:
        path = quality_report(args.raw, args.processed, args.results)
        print(path)
    if args.command in {"sequences", "all"}:
        path = dump_sequences(args.raw, args.processed, args.results, n_pairs=args.pairs)
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
