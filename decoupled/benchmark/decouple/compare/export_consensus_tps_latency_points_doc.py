#!/usr/bin/env python3
"""
Export the consensus TPS-latency points used by the per-network comparison plots.

The generated Markdown document lists one row per plotted point:
- network
- workload
- offered load
- runs used
- mean/std consensus TPS
- mean/std consensus latency
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_ROOT = SCRIPT_DIR.parent
OUTPUT_PATH = DATA_ROOT / "consensus_tps_latency_points.md"
MAX_RATE = 140_000
NETWORK_ORDER = {"80ms": 0, "geo": 1, "geo_uniform": 2, "no-delay": 3}

RUN_DIR_PATTERN = re.compile(
    r"(?:(?P<prefix>.+?)_)?(?P<timestamp>\d{8}_\d{6})_n(?P<nodes>\d+)_r(?P<rate>\d+)_run(?P<run>\d+)$"
)
TPS_PATTERN = re.compile(r"Consensus TPS: ([\d,]+) tx/s")
LATENCY_PATTERN = re.compile(r"Consensus latency: ([\d,]+) ms")

WORKLOADS = [
    {"label": "balanced", "dir_name": "balanced"},
    {"label": "custom-high-3", "dir_name": "custom-high-3"},
    {"label": "custom-high-5", "dir_name": "custom-high-5"},
]
WORKLOAD_DIRS = {item["dir_name"] for item in WORKLOADS}


@dataclass
class SummaryPoint:
    rate: int
    timestamp: str
    run: int
    tps: int
    latency_ms: int
    workload_dir: str


def _parse_int(match: re.Match[str] | None) -> int | None:
    if match is None:
        return None
    return int(match.group(1).replace(",", ""))


def _detect_workload(path: Path, network_dir: Path) -> str | None:
    try:
        relative_parts = path.relative_to(network_dir).parts
    except ValueError:
        return None

    for part in relative_parts:
        if part in WORKLOAD_DIRS:
            return part
    return None


def _parse_summary(path: Path, network_dir: Path) -> SummaryPoint | None:
    match = RUN_DIR_PATTERN.match(path.parent.name)
    if match is None:
        return None

    rate = int(match.group("rate"))
    if rate > MAX_RATE:
        return None

    workload_dir = _detect_workload(path, network_dir)
    if workload_dir is None:
        return None

    raw = path.read_text(errors="replace")
    tps = _parse_int(TPS_PATTERN.search(raw))
    latency_ms = _parse_int(LATENCY_PATTERN.search(raw))
    if tps is None or latency_ms is None or tps <= 0 or latency_ms <= 0:
        return None

    return SummaryPoint(
        rate=rate,
        timestamp=match.group("timestamp"),
        run=int(match.group("run")),
        tps=tps,
        latency_ms=latency_ms,
        workload_dir=workload_dir,
    )


def _discover_networks() -> list[dict[str, str]]:
    network_specs = []
    for path in DATA_ROOT.iterdir():
        if not path.is_dir():
            continue
        if path.name == "compare":
            continue
        network_specs.append({"label": path.name, "dir_name": path.name})

    network_specs.sort(key=lambda item: (NETWORK_ORDER.get(item["dir_name"], 999), item["dir_name"]))
    return network_specs


def _collect_latest_valid_points(network_dir: Path, workload_dir: str) -> list[SummaryPoint]:
    all_points = []
    for path in sorted(network_dir.glob("**/summary.txt")):
        point = _parse_summary(path, network_dir)
        if point is not None and point.workload_dir == workload_dir:
            all_points.append(point)

    filtered = []
    for rate in sorted({point.rate for point in all_points}):
        rate_points = [point for point in all_points if point.rate == rate]
        latest_timestamp = max((point.timestamp for point in rate_points), default=None)
        if latest_timestamp is None:
            continue
        filtered.extend(
            point for point in rate_points if point.timestamp == latest_timestamp
        )

    filtered.sort(key=lambda item: (item.rate, item.run))
    return filtered


def _aggregate(points: list[SummaryPoint]) -> list[dict[str, float]]:
    grouped: dict[int, list[SummaryPoint]] = {}
    for point in points:
        grouped.setdefault(point.rate, []).append(point)

    rows = []
    for rate in sorted(grouped):
        runs = grouped[rate]
        tps_values = [point.tps for point in runs]
        latency_values = [point.latency_ms for point in runs]
        rows.append(
            {
                "rate": rate,
                "runs_used": len(runs),
                "mean_tps": mean(tps_values),
                "std_tps": stdev(tps_values) if len(tps_values) > 1 else 0.0,
                "mean_latency_ms": mean(latency_values),
                "std_latency_ms": stdev(latency_values) if len(latency_values) > 1 else 0.0,
            }
        )
    return rows


def _format_number(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.1f}"


def _build_document() -> str:
    lines = [
        "# Decouple Consensus TPS-Latency Points",
        "",
        "This document lists the aggregated consensus TPS-latency points used by the per-network comparison plots.",
        "",
        "Aggregation rule:",
        "- For each network/workload/offered-load combination, use the latest timestamp batch only.",
        "- If multiple runs exist in that batch, report the mean and standard deviation across runs.",
        "",
    ]

    for network_spec in _discover_networks():
        network_dir = DATA_ROOT / network_spec["dir_name"]
        workload_rows = []
        missing_workloads = []

        for workload in WORKLOADS:
            points = _collect_latest_valid_points(network_dir, workload["dir_name"])
            aggregated = _aggregate(points)
            if aggregated:
                workload_rows.append((workload["label"], aggregated))
            else:
                missing_workloads.append(workload["label"])

        if not workload_rows:
            continue

        lines.append(f"## {network_spec['label']}")
        lines.append("")
        if missing_workloads:
            lines.append(
                f"Missing workloads in current data: {', '.join(missing_workloads)}."
            )
            lines.append("")

        for workload_label, rows in workload_rows:
            lines.append(f"### {workload_label}")
            lines.append("")
            lines.append("| Offered Load (tx/s) | Runs Used | Mean Consensus TPS (tx/s) | Std Consensus TPS | Mean Consensus Latency (ms) | Std Consensus Latency |")
            lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
            for row in rows:
                lines.append(
                    "| "
                    f"{row['rate']:,} | "
                    f"{int(row['runs_used'])} | "
                    f"{_format_number(row['mean_tps'])} | "
                    f"{_format_number(row['std_tps'])} | "
                    f"{_format_number(row['mean_latency_ms'])} | "
                    f"{_format_number(row['std_latency_ms'])} |"
                )
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main():
    OUTPUT_PATH.write_text(_build_document())
    print(f"Saved consensus point document to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
