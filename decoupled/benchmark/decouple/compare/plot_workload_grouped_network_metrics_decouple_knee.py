#!/usr/bin/env python3
"""
Plot latency and TPS for all workload/network combinations using a knee-style rule,
grouped by workload with networks inside each group.

Knee-point rule:
- aggregate runs at each offered load using the latest batch for that load
- define the knee as the point whose *next* load causes the largest latency jump
- TPS uses the chosen knee point
- latency uses the load immediately before that knee point
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch
from matplotlib.ticker import StrMethodFormatter

SCRIPT_DIR = Path(__file__).resolve().parent

if not hasattr(np, "Inf"):
    np.Inf = np.inf


OUTPUT_PATHS = [
    SCRIPT_DIR / "workload_grouped_network_metrics_decouple_knee.pdf",
    SCRIPT_DIR / "workload_grouped_network_metrics_decouple_knee.png",
]
DATA_ROOT = SCRIPT_DIR.parent
MAX_RATE = 140_000
RUN_DIR_PATTERN = re.compile(
    r"(?:(?P<prefix>.+?)_)?(?P<timestamp>\d{8}_\d{6})_n(?P<nodes>\d+)_r(?P<rate>\d+)_run(?P<run>\d+)$"
)
TPS_PATTERN = re.compile(r"End-to-end TPS: ([\d,]+) tx/s")
LATENCY_PATTERN = re.compile(r"End-to-end latency: ([\d,]+) ms")

NETWORKS = [
    {
        "code": "N1",
        "label": "80ms",
        "dir_name": "80ms",
        "hatch": "",
    },
    {
        "code": "N2",
        "label": "geo",
        "dir_name": "geo",
        "hatch": "///",
    },
    {
        "code": "N3",
        "label": "geo_uniform",
        "dir_name": "geo_uniform",
        "hatch": "\\\\\\",
    },
]

WORKLOADS = [
    {
        "code": "W1",
        "label": "balanced",
        "dir_name": "balanced",
        "color": "#1399B2",
    },
    {
        "code": "W2",
        "label": "custom-high-3",
        "dir_name": "custom-high-3",
        "color": "#daeaf4",
    },
    {
        "code": "W3",
        "label": "custom-high-5",
        "dir_name": "custom-high-5",
        "color": "#B22222",
    },
]


@dataclass
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
    if tps is None or latency_ms is None or tps <= 0 or latency_ms <= 0:
        return None

    return SummaryPoint(
        rate=rate,
        timestamp=match.group("timestamp"),
        run=int(match.group("run")),
        tps=tps,
        latency_ms=latency_ms,
        path=path,
    )


def _collect_latest_valid_points(network_dir: str, workload_dir: str) -> list[SummaryPoint]:
    root = DATA_ROOT / network_dir / workload_dir
    all_points = []
    for path in sorted(root.glob("**/summary.txt")):
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
            if point.timestamp == latest_timestamp:
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


def _select_knee_rows(rows: list[dict]) -> tuple[dict, dict]:
    if not rows:
        raise ValueError("Cannot select knee from empty rows")

    if len(rows) == 1:
        return rows[0], rows[0]

    best_idx = max(
        range(len(rows) - 1),
        key=lambda idx: rows[idx + 1]["mean_latency_ms"] / rows[idx]["mean_latency_ms"],
    )
    knee_row = rows[best_idx]
    knee_idx = best_idx
    latency_row = rows[knee_idx - 1] if knee_idx > 0 else knee_row
    return knee_row, latency_row


def _build_rows():
    rows = []
    for workload in WORKLOADS:
        for network in NETWORKS:
            points = _collect_latest_valid_points(
                network_dir=network["dir_name"],
                workload_dir=workload["dir_name"],
            )
            aggregated = _aggregate(points)
            if not aggregated:
                raise FileNotFoundError(
                    f"No aggregated points found for {network['label']}/{workload['dir_name']}"
                )
            knee_row, latency_row = _select_knee_rows(aggregated)
            rows.append(
                {
                    "combo": f"{workload['code']}+{network['code']}",
                    "network_code": network["code"],
                    "network_label": network["label"],
                    "workload_code": workload["code"],
                    "workload_label": workload["label"],
                    "color": workload["color"],
                    "hatch": network["hatch"],
                    "knee_rate": knee_row["rate"],
                    "latency_rate": latency_row["rate"],
                    "runs_used": knee_row["runs_used"],
                    "mean_tps": knee_row["mean_tps"],
                    "mean_latency_ms": latency_row["mean_latency_ms"],
                    "std_tps": knee_row["std_tps"],
                    "std_latency_ms": latency_row["std_latency_ms"],
                }
            )
    return rows


def _set_academic_style():
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"],
            "mathtext.fontset": "dejavusans",
            "font.size": 6,
            "axes.labelsize": 7,
            "axes.titlesize": 7,
            "xtick.labelsize": 5.5,
            "ytick.labelsize": 5.5,
            "legend.fontsize": 6.5,
            "legend.title_fontsize": 6.5,
            "axes.spines.top": True,
            "axes.spines.right": True,
            "axes.edgecolor": "#000000",
            "axes.linewidth": 0.6,
            "xtick.color": "#000000",
            "ytick.color": "#000000",
            "xtick.major.width": 0.6,
            "ytick.major.width": 0.6,
            "xtick.major.size": 3.5,
            "ytick.major.size": 3.5,
            "xtick.direction": "in",
            "ytick.direction": "in",
            "grid.linewidth": 0.5,
            "hatch.linewidth": 0.6,
            "savefig.pad_inches": 0.02,
        }
    )


def _build_positions() -> list[float]:
    positions = []

    inner_gap = 0.017
    group_gap = 0.012

    x = 0.0
    for _ in WORKLOADS:
        for j in range(len(NETWORKS)):
            positions.append(x + j * inner_gap)
        x += len(NETWORKS) * inner_gap + group_gap

    return positions


def _style_axis(ax, ylabel: str, tick_format: str = "{x:,.1f}"):
    ax.set_ylabel(ylabel)
    ax.grid(True, axis="y", linestyle=(0, (2.2, 2.2)), alpha=0.28, color="#9a9a9a")
    ax.grid(False, axis="x")
    ax.set_axisbelow(True)
    ax.yaxis.set_major_formatter(StrMethodFormatter(tick_format))


def plot_combo_metrics(output_paths: list[Path]):
    rows = _build_rows()
    x_positions = _build_positions()
    x_labels = [row["network_code"] for row in rows]
    latency_values = [row["mean_latency_ms"] / 1000.0 for row in rows]
    tps_values = [row["mean_tps"] / 1000.0 for row in rows]
    latency_errors = [row["std_latency_ms"] / 1000.0 for row in rows]
    tps_errors = [row["std_tps"] / 1000.0 for row in rows]

    fig, (ax_latency, ax_tps) = plt.subplots(
        2,
        1,
        figsize=(3.8, 3.04),
        sharex=True,
        gridspec_kw={"height_ratios": [1, 1]},
    )

    bar_width = 0.013
    for x_pos, row, latency, latency_err, tps, tps_err in zip(
        x_positions,
        rows,
        latency_values,
        latency_errors,
        tps_values,
        tps_errors,
    ):
        common_kwargs = {
            "width": bar_width,
            "color": row["color"],
            "edgecolor": "#2f2f2f",
            "linewidth": 0.45,
            "alpha": 0.90,
            "hatch": row["hatch"],
        }
        ax_latency.bar(
            x_pos,
            latency,
            yerr=latency_err,
            capsize=1.8,
            error_kw={"elinewidth": 0.65, "capthick": 0.65, "ecolor": "#2f2f2f"},
            **common_kwargs,
        )
        ax_tps.bar(
            x_pos,
            tps,
            yerr=tps_err,
            capsize=1.8,
            error_kw={"elinewidth": 0.65, "capthick": 0.65, "ecolor": "#2f2f2f"},
            **common_kwargs,
        )

    _style_axis(ax_latency, "Latency (s)")
    _style_axis(ax_tps, "Throughput (KTps)")

    latency_top = max(v + e for v, e in zip(latency_values, latency_errors))
    tps_top = max(v + e for v, e in zip(tps_values, tps_errors))
    ax_latency.set_ylim(0, latency_top * 1.28)
    ax_tps.set_ylim(0, tps_top * 1.24)
    ax_latency.tick_params(axis="x", bottom=False, top=False, labelbottom=False)
    ax_tps.tick_params(axis="x", bottom=False, top=False, pad=1)
    ax_tps.set_xticks(x_positions)
    ax_tps.set_xticklabels(x_labels)
    ax_tps.set_xlabel("Decoupled Architecture at Per-config Knee Point", labelpad=2)

    ax_latency.set_xlim(min(x_positions) - 0.02, max(x_positions) + 0.02)

    workload_legend = [
        Patch(
            facecolor=workload["color"],
            edgecolor="#2f2f2f",
            linewidth=0.65,
            label=workload["code"],
        )
        for workload in WORKLOADS
    ]
    network_legend = [
        Patch(
            facecolor="white",
            edgecolor="#2f2f2f",
            linewidth=0.65,
            hatch=network["hatch"],
            label=network["code"],
        )
        for network in NETWORKS
    ]

    legend1 = ax_latency.legend(
        handles=workload_legend,
        loc="upper left",
        bbox_to_anchor=(0.03, 0.97),
        ncol=3,
        frameon=True,
        facecolor="white",
        edgecolor="white",
        framealpha=0.9,
        handlelength=1.2,
        columnspacing=0.7,
        handletextpad=0.4,
        borderaxespad=0.0,
    )
    ax_latency.add_artist(legend1)

    ax_latency.legend(
        handles=network_legend,
        loc="upper right",
        bbox_to_anchor=(0.97, 0.97),
        ncol=3,
        frameon=True,
        facecolor="white",
        edgecolor="white",
        framealpha=0.9,
        handlelength=1.2,
        columnspacing=0.7,
        handletextpad=0.4,
        borderaxespad=0.0,
    )

    output_paths[0].parent.mkdir(parents=True, exist_ok=True)
    fig.subplots_adjust(
        hspace=0.10,
        bottom=0.18,
        top=0.95,
        left=0.12,
        right=0.98,
    )
    for output_path in output_paths:
        fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)

    return rows


def main():
    _set_academic_style()
    rows = plot_combo_metrics(OUTPUT_PATHS)
    for output_path in OUTPUT_PATHS:
        print(f"Saved workload-grouped knee-point metrics plot to: {output_path}")
    for row in rows:
        print(
            f"  {row['combo']}: {row['workload_label']} + {row['network_label']}, "
            f"knee_rate={row['knee_rate']}, latency_rate={row['latency_rate']}, runs={row['runs_used']}, "
            f"mean_latency_ms={row['mean_latency_ms']:.1f}, mean_tps={row['mean_tps']:.1f}"
        )


if __name__ == "__main__":
    main()
