#!/usr/bin/env python3
"""
Plot per-configuration TPS-latency curves using consensus metrics.

The script discovers every configuration directory under `benchmark/decouple`
that contains benchmark run folders with `summary.txt`, then saves:
- tps_latency_consensus.png
- tps_latency_consensus.pdf

Each plot is written into the same configuration directory as its source data.
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
DATA_ROOT = SCRIPT_DIR
OUTPUT_STEM = "tps_latency_consensus"

RUN_DIR_PATTERN = re.compile(
    r"(?:(?P<prefix>.+?)_)?(?P<timestamp>\d{8}_\d{6})_n(?P<nodes>\d+)_r(?P<rate>\d+)_run(?P<run>\d+)$"
)
TPS_PATTERN = re.compile(r"Consensus TPS: ([\d,]+) tx/s")
LATENCY_PATTERN = re.compile(r"Consensus latency: ([\d,]+) ms")


@dataclass(frozen=True)
class SummaryPoint:
    config_dir: Path
    rate: int
    timestamp: str
    run: int
    tps: int
    latency_ms: int
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
            "legend.fontsize": 8,
            "axes.spines.top": True,
            "axes.spines.right": True,
            "axes.edgecolor": "#000000",
            "axes.linewidth": 0.8,
            "xtick.direction": "in",
            "ytick.direction": "in",
            "savefig.pad_inches": 0.03,
        }
    )


def _parse_summary(path: Path) -> SummaryPoint | None:
    match = RUN_DIR_PATTERN.match(path.parent.name)
    if match is None:
        return None

    raw = path.read_text(errors="replace")
    tps = _parse_int(TPS_PATTERN.search(raw))
    latency_ms = _parse_int(LATENCY_PATTERN.search(raw))
    if tps is None or latency_ms is None or tps <= 0 or latency_ms <= 0:
        return None

    return SummaryPoint(
        config_dir=path.parent.parent,
        rate=int(match.group("rate")),
        timestamp=match.group("timestamp"),
        run=int(match.group("run")),
        tps=tps,
        latency_ms=latency_ms,
        path=path,
    )


def _collect_points_by_config() -> dict[Path, list[SummaryPoint]]:
    grouped: dict[Path, list[SummaryPoint]] = {}
    for path in sorted(DATA_ROOT.glob("**/summary.txt")):
        point = _parse_summary(path)
        if point is None:
            continue
        grouped.setdefault(point.config_dir, []).append(point)
    return grouped


def _select_latest_runs_per_rate(points: list[SummaryPoint]) -> list[SummaryPoint]:
    filtered = []
    for rate in sorted({point.rate for point in points}):
        rate_points = [point for point in points if point.rate == rate]
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
                "mean_latency_ms": mean(latency_values),
                "std_tps": stdev(tps_values) if len(tps_values) > 1 else 0.0,
                "std_latency_ms": stdev(latency_values) if len(latency_values) > 1 else 0.0,
            }
        )
    return rows


def _save_png_and_pdf(fig, output_stem: Path):
    png_path = output_stem.with_suffix(".png")
    pdf_path = output_stem.with_suffix(".pdf")
    fig.savefig(png_path, dpi=260, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    return png_path, pdf_path


def _plot_config(config_dir: Path, points: list[SummaryPoint]):
    selected_points = _select_latest_runs_per_rate(points)
    aggregated = _aggregate(selected_points)
    if not aggregated:
        return ()

    fig, ax = plt.subplots(figsize=(6.6, 4.4))
    accent = "#1f77b4"

    ax.scatter(
        [point.tps / 1000.0 for point in selected_points],
        [point.latency_ms / 1000.0 for point in selected_points],
        color=accent,
        alpha=0.18,
        s=30,
        label="runs",
    )

    ax.errorbar(
        [row["mean_tps"] / 1000.0 for row in aggregated],
        [row["mean_latency_ms"] / 1000.0 for row in aggregated],
        xerr=[row["std_tps"] / 1000.0 for row in aggregated],
        yerr=[row["std_latency_ms"] / 1000.0 for row in aggregated],
        fmt="-o",
        linewidth=2.0,
        markersize=5.8,
        capsize=3.5,
        color=accent,
        ecolor=accent,
        label="mean +/- std",
    )

    for row in aggregated:
        ax.annotate(
            f"{int(row['rate']) // 1000}k",
            (row["mean_tps"] / 1000.0, row["mean_latency_ms"] / 1000.0),
            textcoords="offset points",
            xytext=(6, 5),
            fontsize=8,
            color=accent,
        )

    title = config_dir.relative_to(DATA_ROOT).as_posix()
    ax.set_title(f"{title} Consensus TPS-Latency", pad=8)
    ax.set_xlabel("Consensus Throughput (KTps)")
    ax.set_ylabel("Consensus Latency (s)")
    ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.1f}"))
    ax.grid(True, axis="both", linestyle=(0, (2.2, 2.2)), alpha=0.28, color="#9a9a9a")
    ax.tick_params(direction="in", top=True, right=True)
    ax.legend(frameon=True, facecolor="white", edgecolor="#d0d0d0", loc="best")

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.8)

    fig.tight_layout()
    saved_paths = _save_png_and_pdf(fig, config_dir / OUTPUT_STEM)
    plt.close(fig)
    return saved_paths


def main():
    _set_plot_style()
    points_by_config = _collect_points_by_config()
    if not points_by_config:
        raise SystemExit("No valid consensus summaries found under benchmark/decouple")

    saved_count = 0
    for config_dir in sorted(points_by_config):
        saved_paths = _plot_config(config_dir, points_by_config[config_dir])
        if not saved_paths:
            continue
        saved_count += 1
        for output_path in saved_paths:
            print(f"Saved consensus TPS-latency plot to: {output_path}")

    print(f"Generated plots for {saved_count} configuration directories.")


if __name__ == "__main__":
    main()
