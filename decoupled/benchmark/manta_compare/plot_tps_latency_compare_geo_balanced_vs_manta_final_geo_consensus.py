#!/usr/bin/env python3
"""
Plot consensus TPS-latency comparison between decouple geo/balanced
and manta_final_geo (excluding archived runs under old/).

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
DECOUPLE_ROOT = BENCHMARK_ROOT / "decouple" / "geo" / "balanced"
MANTA_ROOT = BENCHMARK_ROOT / "manta_final_geo"
OUTPUT_STEM = SCRIPT_DIR / "tps_latency_compare_geo_balanced_vs_manta_final_geo_consensus"

if not hasattr(np, "Inf"):
    np.Inf = np.inf

DECOUPLE_RUN_DIR_PATTERN = re.compile(
    r"(?:(?P<prefix>.+?)_)?(?P<timestamp>\d{8}_\d{6})_n(?P<nodes>\d+)_r(?P<rate>\d+)_run(?P<run>\d+)$"
)
MANTA_RUN_DIR_PATTERN = re.compile(
    r"(?P<timestamp>\d{8}_\d{6}_\d{6})_cloudlab-n(?P<nodes>\d+)-r(?P<rate>\d+)-run(?P<run>\d+)-tag-manta_final_geo$"
)
CONSENSUS_TPS_PATTERN = re.compile(r"Consensus TPS: ([\d,]+) tx/s")
CONSENSUS_LATENCY_PATTERN = re.compile(r"Consensus latency: ([\d,]+) ms")

SERIES = [
    {
        "label": "decouple geo/balanced",
        "root": DECOUPLE_ROOT,
        "color": "#1f77b4",
        "marker": "o",
        "annotate_dx": -18,
        "dir_pattern": DECOUPLE_RUN_DIR_PATTERN,
    },
    {
        "label": "manta_final_geo",
        "root": MANTA_ROOT,
        "color": "#ff7f0e",
        "marker": "s",
        "annotate_dx": 6,
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
            "legend.fontsize": 9,
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
    tps = _parse_int(CONSENSUS_TPS_PATTERN.search(raw))
    latency_ms = _parse_int(CONSENSUS_LATENCY_PATTERN.search(raw))
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
    fig, ax = plt.subplots(figsize=(7.2, 4.9))
    series_rows = []

    for series in SERIES:
        points = _collect_latest_points(series)
        aggregated = _aggregate(points)

        ax.scatter(
            [point.tps / 1000.0 for point in points],
            [point.latency_ms / 1000.0 for point in points],
            color=series["color"],
            alpha=0.18,
            s=30,
        )

        ax.errorbar(
            [row["mean_tps"] / 1000.0 for row in aggregated],
            [row["mean_latency_ms"] / 1000.0 for row in aggregated],
            xerr=[row["std_tps"] / 1000.0 for row in aggregated],
            yerr=[row["std_latency_ms"] / 1000.0 for row in aggregated],
            fmt=f"-{series['marker']}",
            linewidth=2.0,
            markersize=6,
            capsize=3.5,
            color=series["color"],
            ecolor=series["color"],
            label=series["label"],
        )

        for row in aggregated:
            ax.annotate(
                f"{row['rate'] // 1000}k",
                (row["mean_tps"] / 1000.0, row["mean_latency_ms"] / 1000.0),
                textcoords="offset points",
                xytext=(series["annotate_dx"], 5),
                fontsize=8,
                color=series["color"],
            )

        series_rows.append((series["label"], aggregated))

    max_latency_s = max(
        row["mean_latency_ms"] / 1000.0
        for _, rows in series_rows
        for row in rows
    )

    ax.set_title("Geo Balanced Consensus TPS-Latency", pad=8)
    ax.set_xlabel("Consensus Throughput (KTps)")
    ax.set_ylabel("Consensus Latency (s)")
    ax.set_ylim(0, max_latency_s * 1.06)
    ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.1f}"))
    ax.grid(True, axis="both", linestyle=(0, (2.2, 2.2)), alpha=0.28, color="#9a9a9a")
    ax.tick_params(direction="in", top=True, right=True)
    ax.legend(frameon=True, facecolor="white", edgecolor="#d0d0d0")
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
        print(f"Saved consensus TPS-latency plot to: {output_path}")
    for label, rows in series_rows:
        for row in rows:
            print(
                f"  {label}: rate={row['rate']}, runs={row['runs_used']}, "
                f"mean_tps={row['mean_tps']:.1f}, "
                f"mean_latency_ms={row['mean_latency_ms']:.1f}"
            )


if __name__ == "__main__":
    main()
