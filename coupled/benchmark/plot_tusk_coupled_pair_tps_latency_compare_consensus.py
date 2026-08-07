#!/usr/bin/env python3
"""
Generate a consensus TPS-latency comparison for the selected pair:
80ms balanced vs geo custom-high-5 across all available offered loads.

Outputs are written under benchmark/tusk_coupled/comparison/consensus/.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MaxNLocator, StrMethodFormatter

# Compatibility shim for older matplotlib on NumPy 2.x.
if not hasattr(np, "Inf"):
    np.Inf = np.inf

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_ROOT = SCRIPT_DIR / "tusk_coupled"
OUTPUT_DIR = DATA_ROOT / "comparison" / "consensus"

LOW_HETEROGENEITY_COLOR = "#F18F01"
HIGH_HETEROGENEITY_COLOR = "#A23B72"
GRID_COLOR = "#9a9a9a"
FRAME_COLOR = "black"
LEGEND_EDGE_COLOR = "#cfcfcf"

TPS_SERIES = [
    {
        "label": "Low heterogeneity",
        "root": DATA_ROOT / "80ms" / "balanced",
        "summary_name": "80ms_balanced_summary.txt",
        "color": LOW_HETEROGENEITY_COLOR,
        "marker": "o",
        "linestyle": "-",
        "annotate_dx": 8,
        "max_rate": 120_000,
    },
    {
        "label": "High heterogeneity",
        "root": DATA_ROOT / "geo" / "custom-high-5",
        "summary_name": "geo_custom-high-5_summary.txt",
        "color": HIGH_HETEROGENEITY_COLOR,
        "marker": "s",
        "linestyle": "--",
        "annotate_dx": -20,
        "max_rate": None,
    },
]

RUN_DIR_PATTERN = re.compile(
    r"(?P<timestamp>\d{8}_\d{6})_n(?P<nodes>\d+)_r(?P<rate>\d+)_run(?P<run>\d+)$"
)
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


def configure_plot_style():
    for style_name in ("seaborn-v0_8-whitegrid", "seaborn-whitegrid", "default"):
        try:
            plt.style.use(style_name)
            break
        except OSError:
            continue
    plt.rcParams.update(
        {
            "font.size": 20,
            "axes.titlesize": 33,
            "axes.labelsize": 33,
            "legend.fontsize": 26,
            "xtick.labelsize": 30,
            "ytick.labelsize": 30,
        }
    )


def _parse_int(match: re.Match[str] | None) -> int | None:
    if match is None:
        return None
    return int(match.group(1).replace(",", ""))


def _save_figure_png_and_pdf(fig, output_path: Path):
    png_path = output_path.with_suffix(".png")
    pdf_path = output_path.with_suffix(".pdf")
    fig.savefig(png_path, dpi=220, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    return png_path, pdf_path


def _parse_summary(path: Path) -> SummaryPoint | None:
    match = RUN_DIR_PATTERN.match(path.parent.name)
    if match is None:
        return None

    rate = int(match.group("rate"))
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


def _filter_points_by_rate(points: list[SummaryPoint], max_rate: int | None) -> list[SummaryPoint]:
    if max_rate is None:
        return points
    return [point for point in points if point.rate <= max_rate]


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
        points = _collect_workload_points(spec["root"], spec["summary_name"])
        points = _filter_points_by_rate(points, spec.get("max_rate"))
        aggregated = _aggregate(points)
        if not aggregated:
            continue

        ax.plot(
            [row["mean_tps"] / 1000.0 for row in aggregated],
            [row["mean_latency_ms"] / 1000.0 for row in aggregated],
            linestyle=spec["linestyle"],
            marker=spec["marker"],
            linewidth=2.2,
            markersize=6,
            color=spec["color"],
            markerfacecolor="white",
            markeredgewidth=1.1,
            label=spec["label"],
        )

        series_rows.append((spec["label"], aggregated))

    if not series_rows:
        raise ValueError("No consensus TPS-latency data found for the selected workloads.")

    ax.set_xlabel("Throughput (KTps)", fontsize=30)
    ax.set_ylabel("Latency (s)", fontsize=30)
    ax.set_ylim(bottom=1)
    ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.0f}"))
    ax.grid(True, axis="both", linestyle=(0, (2.2, 2.2)), alpha=0.28, color=GRID_COLOR)
    ax.tick_params(direction="in", top=True, right=True)
    ax.legend(frameon=True, facecolor="white", edgecolor=LEGEND_EDGE_COLOR, fontsize=25)
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
    tps_output = OUTPUT_DIR / (
        "tps_latency_compare_80ms_balanced_vs_geo_custom-high-5"
        "_all_rates_consensus.png"
    )

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
