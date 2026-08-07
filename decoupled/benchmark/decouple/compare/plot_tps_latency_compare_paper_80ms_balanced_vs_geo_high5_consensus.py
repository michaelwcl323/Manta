#!/usr/bin/env python3
"""
Plot a paper-style TPS-latency comparison for two decoupled configurations:
- 80ms + balanced
- geo + high5 (mapped to custom-high-5)

Outputs are saved to:
- benchmark/decouple/paper/tps_latency_compare_80ms_balanced_vs_geo_high5_consensus.png
- benchmark/decouple/paper/tps_latency_compare_80ms_balanced_vs_geo_high5_consensus.pdf
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
OUTPUT_DIR = DATA_ROOT / "paper"
OUTPUT_STEM = OUTPUT_DIR / "tps_latency_compare_80ms_balanced_vs_geo_high5_consensus"

RUN_DIR_PATTERN = re.compile(
    r"(?:(?P<prefix>.+?)_)?(?P<timestamp>\d{8}_\d{6})_n(?P<nodes>\d+)_r(?P<rate>\d+)_run(?P<run>\d+)$"
)
TPS_PATTERN = re.compile(r"Consensus TPS: ([\d,]+) tx/s")
LATENCY_PATTERN = re.compile(r"Consensus latency: ([\d,]+) ms")

SERIES = [
    {
        "label": "80ms + balanced",
        "network_dir": "80ms",
        "workload_dir": "balanced",
        "color": "#1f77b4",
        "marker": "o",
        "annotate_dx": -14,
        "annotate_dy": 6,
    },
    {
        "label": "geo + high5",
        "network_dir": "geo",
        "workload_dir": "custom-high-5",
        "color": "#d62728",
        "marker": "^",
        "annotate_dx": 6,
        "annotate_dy": 6,
    },
]


@dataclass(frozen=True)
class SummaryPoint:
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
            "axes.titlesize": 10,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 9,
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
        rate=int(match.group("rate")),
        timestamp=match.group("timestamp"),
        run=int(match.group("run")),
        tps=tps,
        latency_ms=latency_ms,
        path=path,
    )


def _collect_latest_points(config_root: Path) -> list[SummaryPoint]:
    all_points = []
    for path in sorted(config_root.glob("**/summary.txt")):
        point = _parse_summary(path)
        if point is not None:
            all_points.append(point)

    if not all_points:
        raise FileNotFoundError(f"No valid summaries found under {config_root}")

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
    png_path = output_stem.with_suffix(".png")
    pdf_path = output_stem.with_suffix(".pdf")
    fig.savefig(png_path, dpi=300, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    return png_path, pdf_path


def main():
    _set_plot_style()

    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    printed_rows = []

    for series in SERIES:
        config_root = DATA_ROOT / series["network_dir"] / series["workload_dir"]
        points = _collect_latest_points(config_root)
        aggregated = _aggregate(points)

        ax.scatter(
            [point.tps / 1000.0 for point in points],
            [point.latency_ms / 1000.0 for point in points],
            color=series["color"],
            alpha=0.18,
            s=28,
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
                f"{int(row['rate']) // 1000}k",
                (row["mean_tps"] / 1000.0, row["mean_latency_ms"] / 1000.0),
                textcoords="offset points",
                xytext=(series["annotate_dx"], series["annotate_dy"]),
                fontsize=8,
                color=series["color"],
            )

        printed_rows.append((series["label"], aggregated))

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

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    saved_paths = _save_png_and_pdf(fig, OUTPUT_STEM)
    plt.close(fig)

    for output_path in saved_paths:
        print(f"Saved paper TPS-latency comparison to: {output_path}")
    for label, rows in printed_rows:
        for row in rows:
            print(
                f"  {label}: rate={row['rate']}, runs={row['runs_used']}, "
                f"mean_tps={row['mean_tps']:.1f}, "
                f"mean_latency_ms={row['mean_latency_ms']:.1f}"
            )


if __name__ == "__main__":
    main()
