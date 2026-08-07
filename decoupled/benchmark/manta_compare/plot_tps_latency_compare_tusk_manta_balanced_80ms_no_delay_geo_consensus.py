#!/usr/bin/env python3
"""
Plot balanced-workload consensus TPS-latency curves for:
- Tusk (from benchmark/decouple): 80ms / no-delay / geo
- Manta (from benchmark/manta_final_*): 80ms / no-delay / geo

Outputs are saved under:
- benchmark/manta_compare/
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import StrMethodFormatter

SCRIPT_DIR = Path(__file__).resolve().parent
BENCHMARK_ROOT = SCRIPT_DIR.parent
OUTPUT_STEM = (
    SCRIPT_DIR
    / "tps_latency_compare_tusk_manta_balanced_80ms_no_delay_geo_consensus"
)
Y_AXIS_MAX_S = 6.0

METRIC_LABEL = "Consensus"
TPS_PATTERN = re.compile(r"Consensus TPS: ([\d,]+) tx/s")
LATENCY_PATTERN = re.compile(r"Consensus latency: ([\d,]+) ms")

if not hasattr(np, "Inf"):
    np.Inf = np.inf

TUSK_RUN_DIR_PATTERN = re.compile(
    r"(?:(?P<prefix>.+?)_)?(?P<timestamp>\d{8}_\d{6})_n(?P<nodes>\d+)_r(?P<rate>\d+)_run(?P<run>\d+)$"
)
MANTA_RUN_DIR_PATTERN = re.compile(
    r"(?P<timestamp>\d{8}_\d{6}_\d{6})_cloudlab-n(?P<nodes>\d+)-r(?P<rate>\d+)-run(?P<run>\d+)-tag-(?P<tag>.+)$"
)

SERIES = [
    {
        "label": "Tusk 80ms",
        "root": BENCHMARK_ROOT / "decouple" / "80ms" / "balanced",
        "color": "#1f77b4",
        "marker": "o",
        "linestyle": "-",
        "network_label": "80ms",
        "dir_pattern": TUSK_RUN_DIR_PATTERN,
    },
    {
        "label": "Manta 80ms",
        "root": BENCHMARK_ROOT / "manta_final_80ms",
        "color": "#1f77b4",
        "marker": "s",
        "linestyle": "--",
        "network_label": "80ms",
        "dir_pattern": MANTA_RUN_DIR_PATTERN,
    },
    {
        "label": "Tusk no-delay",
        "root": BENCHMARK_ROOT / "decouple" / "no-delay" / "balanced",
        "color": "#2ca02c",
        "marker": "o",
        "linestyle": "-",
        "network_label": "no-delay",
        "dir_pattern": TUSK_RUN_DIR_PATTERN,
    },
    {
        "label": "Manta no-delay",
        "root": BENCHMARK_ROOT / "manta_final_no_delay",
        "color": "#2ca02c",
        "marker": "o",
        "linestyle": "--",
        "network_label": "no-delay",
        "dir_pattern": MANTA_RUN_DIR_PATTERN,
    },
    {
        "label": "Tusk geo",
        "root": BENCHMARK_ROOT / "decouple" / "geo" / "balanced",
        "color": "#ff7f0e",
        "marker": "^",
        "linestyle": "-",
        "network_label": "geo",
        "dir_pattern": TUSK_RUN_DIR_PATTERN,
    },
    {
        "label": "Manta geo",
        "root": BENCHMARK_ROOT / "manta_final_geo",
        "color": "#ff7f0e",
        "marker": "^",
        "linestyle": "--",
        "network_label": "geo",
        "dir_pattern": MANTA_RUN_DIR_PATTERN,
    },
]


@dataclass
class SummaryPoint:
    rate: int
    run: int
    timestamp: str
    tps: int
    latency_ms: int
    path: Path


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
            "legend.fontsize": 8,
            "axes.spines.top": True,
            "axes.spines.right": True,
            "axes.edgecolor": "#000000",
            "axes.linewidth": 0.8,
            "xtick.direction": "in",
            "ytick.direction": "in",
        }
    )


def _parse_int(match: re.Match[str] | None) -> int | None:
    if match is None:
        return None
    return int(match.group(1).replace(",", ""))


def _parse_summary(path: Path, dir_pattern: re.Pattern[str]) -> SummaryPoint | None:
    match = dir_pattern.match(path.parent.name)
    if match is None:
        return None

    raw = path.read_text(errors="replace")
    tps = _parse_int(TPS_PATTERN.search(raw))
    latency_ms = _parse_int(LATENCY_PATTERN.search(raw))
    if tps is None or latency_ms is None or tps <= 0 or latency_ms <= 0:
        return None

    return SummaryPoint(
        rate=int(match.group("rate")),
        run=int(match.group("run")),
        timestamp=match.group("timestamp"),
        tps=tps,
        latency_ms=latency_ms,
        path=path,
    )


def _collect_latest_points(series_spec: dict) -> list[SummaryPoint]:
    latest_by_rate_run: dict[tuple[int, int], SummaryPoint] = {}

    for path in sorted(series_spec["root"].glob("*/summary.txt")):
        point = _parse_summary(path, series_spec["dir_pattern"])
        if point is None:
            continue

        key = (point.rate, point.run)
        existing = latest_by_rate_run.get(key)
        if existing is None or point.timestamp > existing.timestamp:
            latest_by_rate_run[key] = point

    points = sorted(
        latest_by_rate_run.values(),
        key=lambda item: (item.rate, item.run),
    )
    if not points:
        raise FileNotFoundError(f"No valid summaries found under {series_spec['root']}")
    return points


def _aggregate(points: list[SummaryPoint]):
    grouped: dict[int, list[SummaryPoint]] = {}
    for point in points:
        grouped.setdefault(point.rate, []).append(point)

    rows = []
    for rate in sorted(grouped):
        rate_points = grouped[rate]
        tps_values = [point.tps for point in rate_points]
        latency_values = [point.latency_ms for point in rate_points]
        rows.append(
            {
                "rate": rate,
                "runs_used": len(rate_points),
                "mean_tps": mean(tps_values),
                "mean_latency_ms": mean(latency_values),
                "std_tps": stdev(tps_values) if len(tps_values) > 1 else 0.0,
                "std_latency_ms": stdev(latency_values) if len(latency_values) > 1 else 0.0,
            }
        )
    return rows


def _save_png_and_pdf(fig, output_stem: Path):
    output_stem.parent.mkdir(parents=True, exist_ok=True)
    png_path = output_stem.with_suffix(".png")
    pdf_path = output_stem.with_suffix(".pdf")
    fig.savefig(png_path, dpi=260, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    return png_path, pdf_path


def plot_comparison():
    fig, ax = plt.subplots(figsize=(8.2, 5.1))
    series_rows = []

    for series in SERIES:
        points = _collect_latest_points(series)
        aggregated = _aggregate(points)

        ax.scatter(
            [point.tps / 1000.0 for point in points],
            [point.latency_ms / 1000.0 for point in points],
            color=series["color"],
            marker=series["marker"],
            alpha=0.14,
            s=26,
        )

        ax.errorbar(
            [row["mean_tps"] / 1000.0 for row in aggregated],
            [row["mean_latency_ms"] / 1000.0 for row in aggregated],
            xerr=[row["std_tps"] / 1000.0 for row in aggregated],
            yerr=[row["std_latency_ms"] / 1000.0 for row in aggregated],
            fmt=series["marker"],
            linestyle=series["linestyle"],
            linewidth=1.9,
            markersize=5.5,
            capsize=2.8,
            color=series["color"],
            ecolor=series["color"],
            label=series["label"],
        )

        series_rows.append((series["label"], aggregated))

    ax.set_title("Balanced TPS-Latency: Tusk vs Manta", pad=8)
    ax.set_xlabel(f"{METRIC_LABEL} Throughput (KTps)")
    ax.set_ylabel(f"{METRIC_LABEL} Latency (s)")
    ax.set_ylim(0, Y_AXIS_MAX_S)
    ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.1f}"))
    ax.grid(True, axis="both", linestyle=(0, (2.2, 2.2)), alpha=0.28, color="#9a9a9a")
    ax.tick_params(direction="in", top=True, right=True)
    ax.legend(
        frameon=True,
        facecolor="white",
        edgecolor="#d0d0d0",
        ncol=2,
        columnspacing=1.0,
        handletextpad=0.6,
    )
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.8)

    fig.tight_layout()
    saved_paths = _save_png_and_pdf(fig, OUTPUT_STEM)
    plt.close(fig)
    return saved_paths, series_rows


def main():
    _set_plot_style()
    saved_paths, series_rows = plot_comparison()
    for output_path in saved_paths:
        print(f"Saved {METRIC_LABEL.lower()} TPS-latency plot to: {output_path}")
    for label, rows in series_rows:
        for row in rows:
            print(
                f"  {label}: rate={row['rate']}, runs={row['runs_used']}, "
                f"mean_tps={row['mean_tps']:.1f}, "
                f"mean_latency_ms={row['mean_latency_ms']:.1f}"
            )


if __name__ == "__main__":
    main()
