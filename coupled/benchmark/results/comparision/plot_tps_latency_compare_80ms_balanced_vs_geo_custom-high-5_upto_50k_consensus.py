#!/usr/bin/env python3
"""
Generate the consensus TPS-latency comparison for the selected pair:
80ms balanced vs geo custom-high-5, up to 50k offered load.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev

import matplotlib.pyplot as plt
from matplotlib.ticker import StrMethodFormatter

SCRIPT_DIR = Path(__file__).resolve().parent
BENCHMARK_DIR = SCRIPT_DIR.parent.parent
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

from plot_certificate_progress import configure_plot_style
from plot_baseline_vs_heterogeneous_pair import (
    FRAME_COLOR,
    GRID_COLOR,
    LEGEND_EDGE_COLOR,
    MAX_RATE,
    RUN_DIR_PATTERN,
    TPS_SERIES,
    _parse_int,
    _save_figure_png_and_pdf,
)


OUTPUT_DIR = SCRIPT_DIR / "consensus"
TPS_PATTERN = re.compile(r"Consensus TPS: ([\d,]+) tx/s")
LATENCY_PATTERN = re.compile(r"Consensus latency: ([\d,]+) ms")


@dataclass
class SummaryPoint:
    rate: int
    timestamp: str
    run: int
    tps: int
    latency_ms: int
    path: Path


def _parse_summary(path: Path) -> SummaryPoint | None:
    match = RUN_DIR_PATTERN.match(path.parent.name)
    if match is None:
        return None

    rate = int(match.group("rate"))
    if rate > MAX_RATE:
        return None

    raw = path.read_text(errors="replace")
    tps = _parse_int(TPS_PATTERN.search(raw))
    latency_ms = _parse_int(LATENCY_PATTERN.search(raw))
    if tps is None or latency_ms is None:
        return None

    return SummaryPoint(
        rate=rate,
        timestamp=match.group("timestamp"),
        run=int(match.group("run")),
        tps=tps,
        latency_ms=latency_ms,
        path=path,
    )


def _collect_workload_points(root: Path, summary_name: str) -> list[SummaryPoint]:
    all_points = []
    for path in sorted(root.glob(f"**/{summary_name}")):
        point = _parse_summary(path)
        if point is not None:
            all_points.append(point)

    filtered = []
    for rate in sorted({point.rate for point in all_points}):
        rate_points = [point for point in all_points if point.rate == rate]
        latest_timestamp = max((point.timestamp for point in rate_points), default=None)
        if latest_timestamp is None:
            continue
        for point in rate_points:
            if point.timestamp != latest_timestamp:
                continue
            if point.tps <= 0 or point.latency_ms <= 0:
                continue
            filtered.append(point)

    filtered.sort(key=lambda item: (item.rate, item.run))
    return filtered


def _aggregate(points: list[SummaryPoint]):
    grouped: dict[int, list[SummaryPoint]] = {}
    for point in points:
        grouped.setdefault(point.rate, []).append(point)

    aggregated = []
    for rate in sorted(grouped):
        runs = grouped[rate]
        tps_values = [point.tps for point in runs]
        latency_values = [point.latency_ms for point in runs]
        aggregated.append(
            {
                "rate": rate,
                "runs_used": len(runs),
                "mean_tps": mean(tps_values),
                "mean_latency_ms": mean(latency_values),
                "std_tps": stdev(tps_values) if len(tps_values) > 1 else 0.0,
                "std_latency_ms": stdev(latency_values) if len(latency_values) > 1 else 0.0,
            }
        )
    return aggregated


def plot_tps_latency_comparison(output_path: Path):
    fig, ax = plt.subplots(figsize=(8.6, 5.8))
    series_rows = []

    for spec in TPS_SERIES:
        root = spec["root"]
        if not root.is_absolute():
            root = BENCHMARK_DIR / root
        points = _collect_workload_points(root, spec["summary_name"])
        aggregated = _aggregate(points)
        if not aggregated:
            continue

        ax.scatter(
            [point.tps / 1000.0 for point in points],
            [point.latency_ms / 1000.0 for point in points],
            color=spec["color"],
            alpha=0.16,
            s=30,
        )

        ax.errorbar(
            [row["mean_tps"] / 1000.0 for row in aggregated],
            [row["mean_latency_ms"] / 1000.0 for row in aggregated],
            xerr=[row["std_tps"] / 1000.0 for row in aggregated],
            yerr=[row["std_latency_ms"] / 1000.0 for row in aggregated],
            fmt=f"-{spec['marker']}",
            linewidth=2.2,
            markersize=6,
            capsize=4,
            color=spec["color"],
            ecolor=spec["color"],
            label=spec["label"],
        )

        for row in aggregated:
            ax.annotate(
                f"{row['rate'] // 1000}k",
                (row["mean_tps"] / 1000.0, row["mean_latency_ms"] / 1000.0),
                textcoords="offset points",
                xytext=(spec["annotate_dx"], 6),
                fontsize=9,
                color=spec["color"],
            )

        series_rows.append((spec["label"], aggregated))

    if not series_rows:
        raise ValueError("No consensus TPS-latency data found for the selected workloads.")

    ax.set_title("Consensus Latency vs Throughput", fontsize=13, pad=10)
    ax.set_xlabel("Throughput (KTps)", fontsize=11)
    ax.set_ylabel("Consensus Latency (s)", fontsize=11)
    ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.1f}"))
    ax.grid(True, axis="both", linestyle=(0, (2.2, 2.2)), alpha=0.28, color=GRID_COLOR)
    ax.tick_params(direction="in", top=True, right=True)
    ax.legend(frameon=True, facecolor="white", edgecolor=LEGEND_EDGE_COLOR, fontsize=10)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color(FRAME_COLOR)
        spine.set_linewidth(0.9)
    fig.tight_layout()
    saved_paths = _save_figure_png_and_pdf(fig, output_path)
    plt.close(fig)

    return series_rows, saved_paths


def main():
    configure_plot_style()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tps_output = OUTPUT_DIR / "tps_latency_compare_80ms_balanced_vs_geo_custom-high-5_upto_50k.png"

    series_rows, tps_saved_paths = plot_tps_latency_comparison(tps_output)

    print(f"Saved consensus TPS-latency comparison to: {tps_saved_paths[0]}")
    print(f"Saved consensus TPS-latency comparison to: {tps_saved_paths[1]}")
    for label, rows in series_rows:
        for row in rows:
            print(
                f"  {label}: rate={row['rate']}, runs={row['runs_used']}, "
                f"mean_tps={row['mean_tps']:.1f}, mean_latency_ms={row['mean_latency_ms']:.1f}"
            )


if __name__ == "__main__":
    main()
