#!/usr/bin/env python3
"""
Generate a certificate collection comparison for the selected pair:
80ms balanced vs geo custom-high-5 at 60k offered load,
for node 0 over rounds 200-350.

Outputs are written under benchmark/tusk_coupled/comparison/consensus/.
"""

from __future__ import annotations

import csv
from pathlib import Path
from statistics import mean

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import StrMethodFormatter

# Compatibility shim for older matplotlib on NumPy 2.x.
if not hasattr(np, "Inf"):
    np.Inf = np.inf

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_ROOT = SCRIPT_DIR / "tusk_coupled"
OUTPUT_DIR = DATA_ROOT / "comparison" / "consensus"

NODE_ID = 0
START_ROUND = 200
END_ROUND = 350
TOTAL_VERTICES = 10

LOW_HETEROGENEITY_COLOR = "#F18F01"
HIGH_HETEROGENEITY_COLOR = "#A23B72"
ANNOTATION_COLOR = "#5a5a5a"
ANNOTATION_TEXT_COLOR = "#3a3a3a"
GRID_COLOR = "#9a9a9a"
FRAME_COLOR = "black"
LEGEND_EDGE_COLOR = "#cfcfcf"

LOW_TIMESTAMP = "20260413_185605"
HIGH_TIMESTAMP = "20260413_170122"
TARGET_RATES = [60_000]


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


def _save_figure_png_and_pdf(fig, output_path: Path):
    png_path = output_path.with_suffix(".png")
    pdf_path = output_path.with_suffix(".pdf")
    fig.savefig(png_path, dpi=220, bbox_inches="tight", pad_inches=0.12)
    fig.savefig(pdf_path, bbox_inches="tight", pad_inches=0.12)
    return png_path, pdf_path


def _load_rows(csv_path: Path):
    with open(csv_path, "r", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    cert_columns = sorted(
        [field for field in fieldnames if field.startswith("Certificate_") and field.endswith("_Time_Delta_ms")],
        key=lambda name: int(name.split("_")[1]),
    )
    if not cert_columns:
        raise ValueError(f"No certificate time columns found in {csv_path}")
    return rows, cert_columns


def _to_int(value):
    if value in (None, "", "UNKNOWN"):
        return None
    return int(value)


def _to_float(value):
    if value in (None, "", "UNKNOWN"):
        return None
    return float(value)


def filter_certificate_rows(rows, node_id: int, start_round: int, end_round: int):
    filtered = []
    for row in rows:
        row_node_id = _to_int(row.get("Node_ID"))
        round_id = _to_int(row.get("Round"))
        if row_node_id is None or round_id is None:
            continue
        if row_node_id != node_id:
            continue
        if round_id < start_round or round_id > end_round:
            continue
        filtered.append(row)
    return filtered


def _completed_rows(rows):
    return [row for row in rows if (_to_int(row.get("Certificate_Count")) or 0) > 0]


def _prepare_rows_for_plotting(rows, cert_columns):
    prepared = []
    for row in rows:
        values = [_to_float(row.get(column)) for column in cert_columns]
        values = sorted(value for value in values if value is not None)
        if values:
            prepared.append((_to_int(row.get("Round")), values))
    return prepared


def _build_progress_series(target_rate: int):
    return [
        {
            "label": "Low heterogeneity",
            "color": LOW_HETEROGENEITY_COLOR,
            "marker": "o",
            "linestyle": "-",
            "csv_paths": [
                DATA_ROOT
                / "80ms"
                / "balanced"
                / f"{LOW_TIMESTAMP}_n10_r{target_rate}_run1"
                / "80ms_balanced_round_certificate_analysis.csv",
                DATA_ROOT
                / "80ms"
                / "balanced"
                / f"{LOW_TIMESTAMP}_n10_r{target_rate}_run2"
                / "80ms_balanced_round_certificate_analysis.csv",
            ],
        },
        {
            "label": "High heterogeneity",
            "color": HIGH_HETEROGENEITY_COLOR,
            "marker": "s",
            "linestyle": "--",
            "csv_paths": [
                DATA_ROOT
                / "geo"
                / "custom-high-5"
                / f"{HIGH_TIMESTAMP}_n10_r{target_rate}_run1"
                / "geo_custom-high-5_round_certificate_analysis.csv",
                DATA_ROOT
                / "geo"
                / "custom-high-5"
                / f"{HIGH_TIMESTAMP}_n10_r{target_rate}_run2"
                / "geo_custom-high-5_round_certificate_analysis.csv",
            ],
        },
    ]


def _compute_progress_curve(spec):
    prepared_rows = []
    round_end_values = []

    for csv_path in spec["csv_paths"]:
        rows, cert_columns = _load_rows(csv_path)
        filtered_rows = filter_certificate_rows(rows, NODE_ID, START_ROUND, END_ROUND)
        if not filtered_rows:
            raise ValueError(
                f"No rows found for {csv_path} with Node_ID={NODE_ID} "
                f"in round range {START_ROUND}-{END_ROUND}."
            )
        completed = _completed_rows(filtered_rows)
        prepared_rows.extend(_prepare_rows_for_plotting(completed, cert_columns))
        round_end_values.extend(
            _to_float(row.get("Round_End_Time_ms"))
            for row in completed
            if _to_float(row.get("Round_End_Time_ms")) is not None
        )

    if not prepared_rows:
        raise ValueError(f"No completed rounds available for {spec['label']}.")

    common_rank_count = min(len(values) for _, values in prepared_rows)
    if common_rank_count == 0:
        raise ValueError(f"No certificate latency values available for {spec['label']}.")

    return {
        "label": spec["label"],
        "color": spec["color"],
        "marker": spec["marker"],
        "linestyle": spec["linestyle"],
        "progress": list(range(1, common_rank_count + 1)),
        "progress_pct": [
            100.0 * (index + 1) / TOTAL_VERTICES for index in range(common_rank_count)
        ],
        "averages": [
            mean(values[index] for _, values in prepared_rows)
            for index in range(common_rank_count)
        ],
        "completed_rounds": len(prepared_rows),
        "avg_round_end_ms": mean(round_end_values),
    }


def plot_progress_comparison(output_path: Path, target_rate: int):
    progress_series = _build_progress_series(target_rate)
    curves = [_compute_progress_curve(spec) for spec in progress_series]
    low_curve = curves[0]
    high_curve = curves[1]

    fig, ax = plt.subplots(figsize=(8.6, 5.8))
    for curve in curves:
        ax.plot(
            curve["averages"],
            curve["progress_pct"],
            linestyle=curve["linestyle"],
            marker=curve["marker"],
            linewidth=2.2,
            markersize=5.5,
            color=curve["color"],
            markerfacecolor="white",
            markeredgewidth=1.1,
            label=curve["label"],
        )

    prolonged_y = 70
    low_cross_x = low_curve["averages"][6]
    high_cross_x = high_curve["averages"][6]
    ax.hlines(
        prolonged_y,
        xmin=low_cross_x,
        xmax=high_cross_x,
        colors=ANNOTATION_COLOR,
        linestyles="--",
        linewidth=1.1,
        alpha=0.9,
    )
    ax.scatter(
        [low_cross_x, high_cross_x],
        [prolonged_y, prolonged_y],
        color=[low_curve["color"], high_curve["color"]],
        s=34,
        zorder=4,
    )
    ax.annotate(
        "",
        xy=(high_cross_x, prolonged_y),
        xytext=(low_cross_x, prolonged_y),
        arrowprops=dict(arrowstyle="<->", color=ANNOTATION_COLOR, lw=1.2),
    )
    ax.text(
        (low_cross_x + high_cross_x) / 2.0,
        prolonged_y + 3.0,
        "Prolonged round",
        ha="center",
        va="bottom",
        fontsize=15,
        color=ANNOTATION_TEXT_COLOR,
    )

    ax.set_xlabel("Time (ms)", fontsize=30)
    ax.set_ylabel("Collected vertices (%)", fontsize=30)
    ax.set_ylim(0, 104)
    ax.set_yticks(range(0, 101, 20))
    ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.0f}"))
    ax.grid(True, axis="both", linestyle=(0, (2.2, 2.2)), alpha=0.28, color=GRID_COLOR)
    ax.tick_params(direction="in", top=True, right=True)
    ax.legend(
        loc="lower right",
        frameon=True,
        facecolor="white",
        edgecolor=LEGEND_EDGE_COLOR,
        fontsize=25,
    )
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color(FRAME_COLOR)
        spine.set_linewidth(0.9)
    fig.tight_layout()

    saved_paths = _save_figure_png_and_pdf(fig, output_path)
    plt.close(fig)

    return curves, saved_paths


def main():
    configure_plot_style()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for target_rate in TARGET_RATES:
        rate_label = f"{target_rate // 1000}k"
        output_path = OUTPUT_DIR / (
            "cert_collection_compare_80ms_balanced_vs_geo_custom-high-5"
            f"_node0_rounds_200_350_{rate_label}.png"
        )

        curves, saved_paths = plot_progress_comparison(output_path, target_rate)

        print(f"Saved cert comparison to: {saved_paths[0]}")
        print(f"Saved cert comparison to: {saved_paths[1]}")
        for curve in curves:
            print(
                f"  {curve['label']}: completed_rounds={curve['completed_rounds']}, "
                f"max_rank={max(curve['progress'])}, avg_round_end_ms={curve['avg_round_end_ms']:.1f}"
            )


if __name__ == "__main__":
    main()
