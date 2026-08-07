#!/usr/bin/env python3
"""
Plot per-network TPS-latency comparisons across workloads using consensus metrics.

Outputs are saved under each network directory, for example:
- decouple/80ms/tps_latency_compare_balanced_custom-high-3_custom-high-5_consensus.png
- decouple/geo/tps_latency_compare_balanced_custom-high-3_custom-high-5_consensus.png
- decouple/geo_uniform/tps_latency_compare_balanced_custom-high-3_custom-high-5_consensus.png
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev

import matplotlib
import numpy as np

if not hasattr(np, "Inf"):
    np.Inf = np.inf

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import StrMethodFormatter


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_ROOT = SCRIPT_DIR.parent
MAX_RATE = 140_000
OUTPUT_STEM = "tps_latency_compare_balanced_custom-high-3_custom-high-5_consensus"
NETWORK_ORDER = {"80ms": 0, "geo": 1, "geo_uniform": 2, "no-delay": 3}

RUN_DIR_PATTERN = re.compile(
    r"(?:(?P<prefix>.+?)_)?(?P<timestamp>\d{8}_\d{6})_n(?P<nodes>\d+)_r(?P<rate>\d+)_run(?P<run>\d+)$"
)
TPS_PATTERN = re.compile(r"Consensus TPS: ([\d,]+) tx/s")
LATENCY_PATTERN = re.compile(r"Consensus latency: ([\d,]+) ms")

WORKLOADS = [
    {
        "label": "balanced",
        "dir_name": "balanced",
        "color": "#1f77b4",
        "marker": "o",
        "annotate_dx": -14,
        "annotate_dy": 5,
    },
    {
        "label": "custom-high-3",
        "dir_name": "custom-high-3",
        "color": "#ff7f0e",
        "marker": "s",
        "annotate_dx": 6,
        "annotate_dy": 5,
    },
    {
        "label": "custom-high-5",
        "dir_name": "custom-high-5",
        "color": "#2ca02c",
        "marker": "^",
        "annotate_dx": 10,
        "annotate_dy": 5,
    },
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
    path: Path


def _parse_int(match: re.Match[str] | None) -> int | None:
    if match is None:
        return None
    return int(match.group(1).replace(",", ""))


def _set_plot_style():
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"],
            "mathtext.fontset": "dejavusans",
            "font.size": 8,
            "axes.labelsize": 10,
            "axes.titlesize": 11,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 9,
            "axes.spines.top": True,
            "axes.spines.right": True,
            "axes.edgecolor": "#000000",
            "axes.linewidth": 0.8,
            "xtick.direction": "in",
            "ytick.direction": "in",
        }
    )


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
        path=path,
    )


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


def _save_png_and_pdf(fig, output_stem: Path):
    png_path = output_stem.with_suffix(".png")
    pdf_path = output_stem.with_suffix(".pdf")
    fig.savefig(png_path, dpi=260, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    return png_path, pdf_path


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


def plot_network(network_spec: dict):
    network_dir = DATA_ROOT / network_spec["dir_name"]
    fig, ax = plt.subplots(figsize=(7.0, 4.8))
    series_rows = []
    plotted_workloads = 0

    for workload in WORKLOADS:
        points = _collect_latest_valid_points(network_dir, workload["dir_name"])
        aggregated = _aggregate(points)
        if not aggregated:
            continue

        plotted_workloads += 1

        ax.scatter(
            [point.tps / 1000.0 for point in points],
            [point.latency_ms / 1000.0 for point in points],
            color=workload["color"],
            alpha=0.16,
            s=28,
        )

        ax.errorbar(
            [row["mean_tps"] / 1000.0 for row in aggregated],
            [row["mean_latency_ms"] / 1000.0 for row in aggregated],
            xerr=[row["std_tps"] / 1000.0 for row in aggregated],
            yerr=[row["std_latency_ms"] / 1000.0 for row in aggregated],
            fmt=f"-{workload['marker']}",
            linewidth=2.0,
            markersize=6,
            capsize=3.5,
            color=workload["color"],
            ecolor=workload["color"],
            label=workload["label"],
        )

        for row in aggregated:
            ax.annotate(
                f"{row['rate'] // 1000}k",
                (row["mean_tps"] / 1000.0, row["mean_latency_ms"] / 1000.0),
                textcoords="offset points",
                xytext=(workload["annotate_dx"], workload["annotate_dy"]),
                fontsize=8,
                color=workload["color"],
            )

        series_rows.append((workload["label"], aggregated))

    if plotted_workloads < len(WORKLOADS):
        plt.close(fig)
        return (), series_rows

    ax.set_title(f"{network_spec['label']} Workload Comparison (Consensus)", pad=8)
    ax.set_xlabel("Consensus Throughput (KTps)")
    ax.set_ylabel("Consensus Latency (s)")
    ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.1f}"))
    ax.grid(True, axis="both", linestyle=(0, (2.2, 2.2)), alpha=0.28, color="#9a9a9a")
    ax.tick_params(direction="in", top=True, right=True)
    ax.legend(frameon=True, facecolor="white", edgecolor="#d0d0d0")
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.8)

    output_stem = network_dir / OUTPUT_STEM
    output_stem.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    saved_paths = _save_png_and_pdf(fig, output_stem)
    plt.close(fig)
    return saved_paths, series_rows


def main():
    _set_plot_style()
    for network_spec in _discover_networks():
        saved_paths, series_rows = plot_network(network_spec)
        if not saved_paths:
            print(
                f"Skipped {network_spec['label']}: missing one or more workloads "
                f"from {', '.join(item['label'] for item in WORKLOADS)}"
            )
            continue
        for output_path in saved_paths:
            print(f"Saved workload TPS-latency consensus plot to: {output_path}")
        for label, rows in series_rows:
            for row in rows:
                print(
                    f"  {network_spec['label']} / {label}: "
                    f"rate={row['rate']}, runs={row['runs_used']}, "
                    f"mean_tps={row['mean_tps']:.1f}, "
                    f"mean_latency_ms={row['mean_latency_ms']:.1f}"
                )


if __name__ == "__main__":
    main()
