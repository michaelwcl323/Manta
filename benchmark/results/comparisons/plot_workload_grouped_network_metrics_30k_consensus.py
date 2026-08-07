#!/usr/bin/env python3
"""
Plot consensus latency and TPS for all workload/network combinations at 30k,
grouped by workload with networks inside each group.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev

import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.ticker import MultipleLocator, StrMethodFormatter

SCRIPT_DIR = Path(__file__).resolve().parent
BENCHMARK_DIR = SCRIPT_DIR.parent.parent
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

from plot_certificate_progress import configure_plot_style


TARGET_RATE = 30_000
OUTPUT_PATHS = [
    SCRIPT_DIR / "consensusm" / "workload_grouped_network_metrics_30k.pdf",
    SCRIPT_DIR / "consensusm" / "workload_grouped_network_metrics_30k.png",
]
FIXED_BATCHES = {
    ("geo_uniform", "custom-high-5"): "20260402_091731",
}

RUN_DIR_PATTERN = re.compile(
    r"(?P<timestamp>\d{8}_\d{6})_n(?P<nodes>\d+)_r(?P<rate>\d+)_run(?P<run>\d+)$"
)
TPS_PATTERN = re.compile(r"Consensus TPS: ([\d,]+) tx/s")
LATENCY_PATTERN = re.compile(r"Consensus latency: ([\d,]+) ms")

NETWORKS = [
    {
        "code": "N1",
        "label": "80ms",
        "root": Path("results/80ms"),
        "hatch": "",
    },
    {
        "code": "N2",
        "label": "geo",
        "root": Path("results/geo"),
        "hatch": "///",
    },
    {
        "code": "N3",
        "label": "geo_uniform",
        "root": Path("results/geo_uniform"),
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
    timestamp: str
    run: int
    rate: int
    tps: int
    latency_ms: int
    path: Path


def _parse_int(match: re.Match[str] | None) -> int | None:
    if match is None:
        return None
    return int(match.group(1).replace(",", ""))


def _summary_name(network_label: str, workload_label: str) -> str:
    return f"{network_label}_{workload_label}_summary.txt"


def _parse_summary(path: Path) -> SummaryPoint | None:
    match = RUN_DIR_PATTERN.match(path.parent.name)
    if match is None:
        return None

    rate = int(match.group("rate"))
    if rate != TARGET_RATE:
        return None

    raw = path.read_text(errors="replace")
    tps = _parse_int(TPS_PATTERN.search(raw))
    latency_ms = _parse_int(LATENCY_PATTERN.search(raw))
    if tps is None or latency_ms is None or tps <= 0 or latency_ms <= 0:
        return None

    return SummaryPoint(
        timestamp=match.group("timestamp"),
        run=int(match.group("run")),
        rate=rate,
        tps=tps,
        latency_ms=latency_ms,
        path=path,
    )


def _collect_points(network_label: str, workload_dir: str, workload_label: str) -> list[SummaryPoint]:
    root = BENCHMARK_DIR / "results" / network_label / workload_dir
    summary_name = _summary_name(network_label, workload_label)
    points = []
    for path in sorted(root.glob(f"**/{summary_name}")):
        point = _parse_summary(path)
        if point is not None:
            points.append(point)

    if not points:
        raise FileNotFoundError(
            f"No valid {TARGET_RATE // 1000}k summaries found for {network_label}/{workload_dir}"
        )

    fixed_timestamp = FIXED_BATCHES.get((network_label, workload_dir))
    selected_timestamp = fixed_timestamp or max(point.timestamp for point in points)
    selected_points = [point for point in points if point.timestamp == selected_timestamp]
    if not selected_points:
        raise FileNotFoundError(
            f"No summaries matched batch {selected_timestamp} for {network_label}/{workload_dir}"
        )

    selected_points.sort(key=lambda item: item.run)
    return selected_points


def _build_rows():
    rows = []
    for workload in WORKLOADS:
        for network in NETWORKS:
            points = _collect_points(
                network_label=network["label"],
                workload_dir=workload["dir_name"],
                workload_label=workload["label"],
            )
            tps_values = [point.tps for point in points]
            latency_values = [point.latency_ms for point in points]
            rows.append(
                {
                    "combo": f"{workload['code']}+{network['code']}",
                    "network_code": network["code"],
                    "network_label": network["label"],
                    "workload_code": workload["code"],
                    "workload_label": workload["label"],
                    "color": workload["color"],
                    "hatch": network["hatch"],
                    "runs_used": len(points),
                    "mean_tps": mean(tps_values),
                    "mean_latency_ms": mean(latency_values),
                    "std_tps": stdev(tps_values) if len(tps_values) > 1 else 0.0,
                    "std_latency_ms": stdev(latency_values) if len(latency_values) > 1 else 0.0,
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
        figsize=(3.8, 3.20),
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
    ax_latency.set_ylim(0, latency_top * 1.1)
    ax_latency.yaxis.set_major_locator(MultipleLocator(0.5))
    ax_tps.set_ylim(0, tps_top * 1.24)

    ax_latency.set_xlim(min(x_positions) - 0.02, max(x_positions) + 0.02)
    ax_latency.tick_params(axis="x", bottom=False, top=False, labelbottom=False)
    ax_tps.tick_params(axis="x", bottom=False, top=False, pad=1)
    ax_tps.set_xticks(x_positions)
    ax_tps.set_xticklabels(x_labels)
    ax_tps.set_xlabel("Coupled Architecture at 30k Offered Load", labelpad=2)

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
    configure_plot_style()
    _set_academic_style()
    rows = plot_combo_metrics(OUTPUT_PATHS)
    for output_path in OUTPUT_PATHS:
        print(f"Saved workload-grouped consensus metrics plot to: {output_path}")
    for row in rows:
        print(
            f"  {row['combo']}: {row['workload_label']} + {row['network_label']}, "
            f"runs={row['runs_used']}, mean_latency_ms={row['mean_latency_ms']:.1f}, "
            f"mean_tps={row['mean_tps']:.1f}"
        )


if __name__ == "__main__":
    main()
