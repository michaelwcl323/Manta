#!/usr/bin/env python3
"""
Plot per-network certificate collection progress comparisons across workloads
for decoupled runs at a fixed offered load.

Outputs are saved under each network directory, for example:
- benchmark/decouple/80ms/cert_progress_compare_balanced_custom-high-3_custom-high-5_node0_rounds_200_350.png
- benchmark/decouple/geo/cert_progress_compare_balanced_custom-high-3_custom-high-5_node0_rounds_200_350.png
- benchmark/decouple/geo_uniform/cert_progress_compare_balanced_custom-high-3_custom-high-5_node0_rounds_200_350.png
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from statistics import mean

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import StrMethodFormatter

SCRIPT_DIR = Path(__file__).resolve().parent

if not hasattr(np, "Inf"):
    np.Inf = np.inf


DATA_ROOT = SCRIPT_DIR.parent
TARGET_RATE = 80_000
NODE_ID = 0
START_ROUND = 200
END_ROUND = 350
OUTPUT_STEM = (
    "cert_progress_compare_balanced_custom-high-3_custom-high-5"
    "_node0_rounds_200_350"
)

RUN_DIR_PATTERN = re.compile(
    r"(?:(?P<prefix>.+?)_)?(?P<timestamp>\d{8}_\d{6})_n(?P<nodes>\d+)_r(?P<rate>\d+)_run(?P<run>\d+)$"
)

NETWORKS = [
    {"label": "80ms", "dir_name": "80ms"},
    {"label": "geo", "dir_name": "geo"},
    {"label": "geo_uniform", "dir_name": "geo_uniform"},
]

WORKLOADS = [
    {
        "label": "balanced",
        "dir_name": "balanced",
        "color": "#1f77b4",
        "marker": "o",
    },
    {
        "label": "custom-high-3",
        "dir_name": "custom-high-3",
        "color": "#ff7f0e",
        "marker": "s",
    },
    {
        "label": "custom-high-5",
        "dir_name": "custom-high-5",
        "color": "#2ca02c",
        "marker": "^",
    },
]


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


def _parse_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _parse_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _collect_latest_csvs(network_dir: str, workload_dir: str) -> list[Path]:
    root = DATA_ROOT / network_dir / workload_dir
    candidates: list[tuple[str, int, Path]] = []
    for path in sorted(root.glob("**/round_certificate_analysis.csv")):
        match = RUN_DIR_PATTERN.match(path.parent.name)
        if match is None:
            continue
        rate = int(match.group("rate"))
        if rate != TARGET_RATE:
            continue
        candidates.append((match.group("timestamp"), int(match.group("run")), path))

    if not candidates:
        raise FileNotFoundError(
            f"No round_certificate_analysis.csv found for {network_dir}/{workload_dir} at {TARGET_RATE}"
        )

    latest_timestamp = max(timestamp for timestamp, _, _ in candidates)
    selected = [path for timestamp, _, path in candidates if timestamp == latest_timestamp]
    selected.sort()
    return selected


def _compute_progress_curve(csv_paths: list[Path]):
    prepared_rows = []

    for csv_path in csv_paths:
        with csv_path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if _parse_int(row.get("Node_ID")) != NODE_ID:
                    continue
                round_value = _parse_int(row.get("Round"))
                if round_value is None or round_value < START_ROUND or round_value > END_ROUND:
                    continue
                if _parse_float(row.get("Round_End_Time_ms")) is None:
                    continue

                cert_values = []
                index = 1
                while True:
                    key = f"Certificate_{index}_Time_Delta_ms"
                    if key not in row:
                        break
                    value = _parse_float(row.get(key))
                    if value is not None:
                        cert_values.append(value)
                    index += 1

                if cert_values:
                    prepared_rows.append(cert_values)

    if not prepared_rows:
        raise ValueError("No completed rounds available in the selected window.")

    common_rank_count = min(len(values) for values in prepared_rows)
    if common_rank_count == 0:
        raise ValueError("No certificate latency values available in the selected window.")

    return {
        "progress": list(range(1, common_rank_count + 1)),
        "averages": [
            mean(values[index] for values in prepared_rows)
            for index in range(common_rank_count)
        ],
        "completed_rounds": len(prepared_rows),
    }


def _save_png_and_pdf(fig, output_stem: Path):
    png_path = output_stem.with_suffix(".png")
    pdf_path = output_stem.with_suffix(".pdf")
    fig.savefig(png_path, dpi=260, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    return png_path, pdf_path


def plot_network(network_spec: dict):
    fig, ax = plt.subplots(figsize=(7.0, 4.8))
    curve_summaries = []

    for workload in WORKLOADS:
        csv_paths = _collect_latest_csvs(network_spec["dir_name"], workload["dir_name"])
        curve = _compute_progress_curve(csv_paths)

        ax.plot(
            curve["averages"],
            curve["progress"],
            marker=workload["marker"],
            linewidth=2.0,
            markersize=5.5,
            color=workload["color"],
            label=f"{workload['label']} ({curve['completed_rounds']} rounds)",
        )
        curve_summaries.append((workload["label"], curve))

    ax.set_title(
        f"{network_spec['label']} Certificate Collection Progress",
        pad=8,
    )
    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Collected certificates")
    ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.0f}"))
    ax.set_yticks(
        list(range(1, max(max(curve["progress"]) for _, curve in curve_summaries) + 1))
    )
    ax.grid(True, axis="both", linestyle=(0, (2.2, 2.2)), alpha=0.28, color="#9a9a9a")
    ax.tick_params(direction="in", top=True, right=True)
    ax.legend(frameon=True, facecolor="white", edgecolor="#d0d0d0")
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.8)

    output_stem = DATA_ROOT / network_spec["dir_name"] / OUTPUT_STEM
    output_stem.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    saved_paths = _save_png_and_pdf(fig, output_stem)
    plt.close(fig)
    return saved_paths, curve_summaries


def main():
    _set_plot_style()
    for network_spec in NETWORKS:
        saved_paths, curve_summaries = plot_network(network_spec)
        for output_path in saved_paths:
            print(f"Saved workload cert-progress plot to: {output_path}")
        for label, curve in curve_summaries:
            print(
                f"  {network_spec['label']} / {label}: "
                f"completed_rounds={curve['completed_rounds']}, "
                f"max_cert_count={max(curve['progress'])}"
            )


if __name__ == "__main__":
    main()
