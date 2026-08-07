#!/usr/bin/env python3
"""
Plot consensus TPS-latency curves from benchmark/tusk_coupled data.

For each network folder (`80ms` and `geo`), the script overlays three workload
categories (`balanced`, `custom-high-3`, `custom-high-5`) in a single figure and
stores the output PNG inside that network folder.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev

import matplotlib.pyplot as plt

DATA_ROOT = Path(__file__).resolve().parent / "tusk_coupled"
NETWORK_SPECS = [
    {"key": "80ms", "title": "80ms", "output": "consensus_tps_latency_by_workload.png"},
    {"key": "geo", "title": "geo", "output": "consensus_tps_latency_by_workload.png"},
]
WORKLOAD_SPECS = [
    {
        "key": "balanced",
        "label": "balanced",
        "color": "#1f77b4",
        "marker": "o",
        "annotation_dx": -14,
    },
    {
        "key": "custom-high-3",
        "label": "custom-high-3",
        "color": "#d95f02",
        "marker": "s",
        "annotation_dx": 6,
    },
    {
        "key": "custom-high-5",
        "label": "custom-high-5",
        "color": "#2a9d8f",
        "marker": "^",
        "annotation_dx": 10,
    },
]

RUN_DIR_PATTERN = re.compile(
    r"(?P<timestamp>\d{8}_\d{6})_n(?P<nodes>\d+)_r(?P<rate>\d+)_run(?P<run>\d+)$"
)
CONS_TPS_PATTERN = re.compile(r"Consensus TPS: ([\d,]+) tx/s")
CONS_LATENCY_PATTERN = re.compile(r"Consensus latency: ([\d,]+) ms")


@dataclass
class SummaryPoint:
    rate: int
    timestamp: str
    run: int
    tps: int
    latency_ms: int
    path: Path


def configure_plot_style():
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update(
        {
            "font.size": 11,
            "axes.titlesize": 14,
            "axes.labelsize": 12,
            "legend.fontsize": 10,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
        }
    )


def _parse_int(match: re.Match[str] | None) -> int | None:
    if match is None:
        return None
    return int(match.group(1).replace(",", ""))


def _parse_summary(path: Path) -> SummaryPoint | None:
    match = RUN_DIR_PATTERN.match(path.parent.name)
    if match is None:
        return None

    raw = path.read_text(errors="replace")
    tps = _parse_int(CONS_TPS_PATTERN.search(raw))
    latency_ms = _parse_int(CONS_LATENCY_PATTERN.search(raw))
    if tps is None or latency_ms is None:
        return None

    return SummaryPoint(
        rate=int(match.group("rate")),
        timestamp=match.group("timestamp"),
        run=int(match.group("run")),
        tps=tps,
        latency_ms=latency_ms,
        path=path,
    )


def _collect_latest_valid_points(network_key: str, workload_key: str) -> tuple[list[SummaryPoint], list[str]]:
    workload_root = DATA_ROOT / network_key / workload_key
    summary_name = f"{network_key}_{workload_key}_summary.txt"
    all_points: list[SummaryPoint] = []
    skipped: list[str] = []

    for path in sorted(workload_root.glob(f"**/{summary_name}")):
        point = _parse_summary(path)
        if point is None:
            skipped.append(f"skip unparsable summary: {path}")
            continue
        all_points.append(point)

    filtered: list[SummaryPoint] = []
    for rate in sorted({point.rate for point in all_points}):
        rate_points = [point for point in all_points if point.rate == rate]
        latest_timestamp = max((point.timestamp for point in rate_points), default=None)
        if latest_timestamp is None:
            continue
        for point in rate_points:
            if point.timestamp != latest_timestamp:
                continue
            if point.tps <= 0 or point.latency_ms <= 0:
                skipped.append(f"skip invalid zero-value run: {point.path}")
                continue
            filtered.append(point)

    filtered.sort(key=lambda item: (item.rate, item.run))
    return filtered, skipped


def _aggregate(points: list[SummaryPoint]) -> list[dict[str, float | int | str]]:
    grouped: dict[int, list[SummaryPoint]] = {}
    for point in points:
        grouped.setdefault(point.rate, []).append(point)

    rows: list[dict[str, float | int | str]] = []
    for rate in sorted(grouped):
        runs = grouped[rate]
        tps_values = [point.tps for point in runs]
        latency_values = [point.latency_ms for point in runs]
        rows.append(
            {
                "rate": rate,
                "runs_used": len(runs),
                "mean_tps": mean(tps_values),
                "mean_latency_ms": mean(latency_values),
                "std_tps": stdev(tps_values) if len(tps_values) > 1 else 0.0,
                "std_latency_ms": stdev(latency_values) if len(latency_values) > 1 else 0.0,
                "timestamp": runs[0].timestamp,
            }
        )
    return rows


def _scatter_runs(ax, points: list[SummaryPoint], *, color: str):
    ax.scatter(
        [point.tps for point in points],
        [point.latency_ms for point in points],
        color=color,
        alpha=0.18,
        s=32,
    )


def _plot_series(ax, rows, *, color: str, marker: str, label: str, annotation_dx: int):
    ax.errorbar(
        [row["mean_tps"] for row in rows],
        [row["mean_latency_ms"] for row in rows],
        xerr=[row["std_tps"] for row in rows],
        yerr=[row["std_latency_ms"] for row in rows],
        fmt=f"-{marker}",
        linewidth=2,
        markersize=6,
        capsize=4,
        color=color,
        ecolor=color,
        label=label,
    )

    for row in rows:
        ax.annotate(
            f"{int(row['rate']) // 1000}k",
            (row["mean_tps"], row["mean_latency_ms"]),
            textcoords="offset points",
            xytext=(annotation_dx, 6),
            fontsize=9,
            color=color,
        )


def _nice_step(span: float, target_ticks: int = 6) -> float:
    if span <= 0:
        return 1.0

    rough = span / target_ticks
    magnitude = 10 ** math.floor(math.log10(rough))
    for multiplier in (1, 2, 5, 10):
        step = magnitude * multiplier
        if step >= rough:
            return step
    return magnitude * 10


def _compute_axis_limits(values: list[float], *, lower_floor: float = 0.0) -> tuple[float, float]:
    if not values:
        return lower_floor, lower_floor + 1

    raw_min = min(values)
    raw_max = max(values)
    span = raw_max - raw_min
    if span == 0:
        span = max(abs(raw_max) * 0.1, 1.0)

    padding = span * 0.12
    lower = max(lower_floor, raw_min - padding)
    upper = raw_max + padding

    step = _nice_step(upper - lower)
    lower = math.floor(lower / step) * step
    upper = math.ceil(upper / step) * step

    if upper <= lower:
        upper = lower + step

    return lower, upper


def _set_smart_limits(ax, all_points: list[SummaryPoint], all_rows: list[dict[str, float | int | str]]):
    x_values = [point.tps for point in all_points]
    y_values = [point.latency_ms for point in all_points]
    x_values.extend(float(row["mean_tps"]) for row in all_rows)
    y_values.extend(float(row["mean_latency_ms"]) for row in all_rows)

    x_min, x_max = _compute_axis_limits(x_values, lower_floor=0.0)
    y_min, y_max = _compute_axis_limits(y_values, lower_floor=0.0)
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)


def plot_network(network_spec: dict[str, str]) -> tuple[Path, list[str]]:
    fig, ax = plt.subplots(figsize=(9.2, 5.8))
    skipped: list[str] = []
    all_points: list[SummaryPoint] = []
    all_rows: list[dict[str, float | int | str]] = []

    for workload_spec in WORKLOAD_SPECS:
        points, workload_skipped = _collect_latest_valid_points(
            network_spec["key"], workload_spec["key"]
        )
        rows = _aggregate(points)
        if not rows:
            raise ValueError(
                f"Missing valid points for {network_spec['key']} / {workload_spec['key']}."
            )
        skipped.extend(workload_skipped)
        all_points.extend(points)
        all_rows.extend(rows)

        _scatter_runs(ax, points, color=workload_spec["color"])
        _plot_series(
            ax,
            rows,
            color=workload_spec["color"],
            marker=workload_spec["marker"],
            label=workload_spec["label"],
            annotation_dx=workload_spec["annotation_dx"],
        )

    _set_smart_limits(ax, all_points, all_rows)
    ax.set_title(f"{network_spec['title']} Consensus Latency vs TPS by Workload")
    ax.set_xlabel("Consensus TPS (tx/s)")
    ax.set_ylabel("Consensus latency (ms)")
    ax.grid(True, linestyle="--", alpha=0.35)
    ax.legend()
    fig.tight_layout()

    output_path = DATA_ROOT / network_spec["key"] / network_spec["output"]
    fig.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return output_path, skipped


def main():
    configure_plot_style()

    for network_spec in NETWORK_SPECS:
        output_path, skipped = plot_network(network_spec)
        print(f"Saved plot to: {output_path}")
        if skipped:
            print("Skipped summaries:")
            for item in skipped:
                print(f"  {item}")


if __name__ == "__main__":
    main()
