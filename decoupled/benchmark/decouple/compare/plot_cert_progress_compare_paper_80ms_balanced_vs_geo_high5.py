#!/usr/bin/env python3
"""
Plot a paper-style certificate collection progress comparison for two decoupled
configurations at a fixed offered load:
- 80ms + balanced
- geo + high5 (mapped to custom-high-5)

Outputs are saved to:
- benchmark/decouple/paper/cert_progress_compare_80ms_balanced_vs_geo_high5_node0_rounds_200_350_80k.png
- benchmark/decouple/paper/cert_progress_compare_80ms_balanced_vs_geo_high5_node0_rounds_200_350_80k.pdf
- benchmark/decouple/paper/cert_progress_compare_80ms_balanced_vs_geo_high5_node0_rounds_200_350_100k.png
- benchmark/decouple/paper/cert_progress_compare_80ms_balanced_vs_geo_high5_node0_rounds_200_350_100k.pdf
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from statistics import mean

import matplotlib
import numpy as np

if not hasattr(np, "Inf"):
    np.Inf = np.inf

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import StrMethodFormatter


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_ROOT = SCRIPT_DIR.parent
NODE_ID = 0
START_ROUND = 200
END_ROUND = 350
OUTPUT_DIR = DATA_ROOT / "paper"

RUN_DIR_PATTERN = re.compile(
    r"(?:(?P<prefix>.+?)_)?(?P<timestamp>\d{8}_\d{6})_n(?P<nodes>\d+)_r(?P<rate>\d+)_run(?P<run>\d+)$"
)

SERIES = [
    {
        "label": "80ms + balanced",
        "network_dir": "80ms",
        "workload_dir": "balanced",
        "color": "#1f77b4",
        "marker": "o",
    },
    {
        "label": "geo + high5",
        "network_dir": "geo",
        "workload_dir": "custom-high-5",
        "color": "#d62728",
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


def _parse_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _parse_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _build_output_stem(target_rate: int) -> Path:
    return OUTPUT_DIR / (
        "cert_progress_compare_80ms_balanced_vs_geo_high5"
        f"_node{NODE_ID}_rounds_{START_ROUND}_{END_ROUND}_{target_rate // 1000}k"
    )


def _collect_latest_csvs(network_dir: str, workload_dir: str, target_rate: int) -> list[Path]:
    root = DATA_ROOT / network_dir / workload_dir
    candidates: list[tuple[str, int, Path]] = []
    for path in sorted(root.glob("**/round_certificate_analysis.csv")):
        match = RUN_DIR_PATTERN.match(path.parent.name)
        if match is None:
            continue
        if int(match.group("rate")) != target_rate:
            continue
        candidates.append((match.group("timestamp"), int(match.group("run")), path))

    if not candidates:
        raise FileNotFoundError(
            f"No round_certificate_analysis.csv found for {network_dir}/{workload_dir} "
            f"at {target_rate}"
        )

    latest_timestamp = max(timestamp for timestamp, _, _ in candidates)
    selected = [path for timestamp, _, path in candidates if timestamp == latest_timestamp]
    selected.sort()
    return selected


def _compute_progress_curve(csv_paths: list[Path]) -> dict[str, list[float] | int]:
    prepared_rows: list[list[float]] = []

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
    fig.savefig(png_path, dpi=300, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    return png_path, pdf_path


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rate",
        type=int,
        default=80_000,
        help="Target offered load, e.g. 80000 or 100000",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    target_rate = args.rate
    _set_plot_style()

    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    curve_summaries = []

    for series in SERIES:
        csv_paths = _collect_latest_csvs(
            series["network_dir"], series["workload_dir"], target_rate
        )
        curve = _compute_progress_curve(csv_paths)

        ax.plot(
            curve["averages"],
            curve["progress"],
            marker=series["marker"],
            linewidth=2.0,
            markersize=5.5,
            color=series["color"],
            label=f"{series['label']} ({curve['completed_rounds']} rounds)",
        )
        curve_summaries.append((series["label"], curve))

    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Collected certificates")
    ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.0f}"))
    ax.set_yticks(
        list(range(1, max(max(curve["progress"]) for _, curve in curve_summaries) + 1))
    )
    ax.grid(True, axis="both", linestyle=(0, (2.2, 2.2)), alpha=0.28, color="#9a9a9a")
    ax.tick_params(direction="in", top=True, right=True)
    ax.legend(frameon=True, facecolor="white", edgecolor="#d0d0d0", loc="best")

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.8)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    saved_paths = _save_png_and_pdf(fig, _build_output_stem(target_rate))
    plt.close(fig)

    for output_path in saved_paths:
        print(f"Saved paper cert-progress comparison to: {output_path}")
    for label, curve in curve_summaries:
        print(
            f"  {label}: completed_rounds={curve['completed_rounds']}, "
            f"max_cert_count={max(curve['progress'])}"
        )


if __name__ == "__main__":
    main()
