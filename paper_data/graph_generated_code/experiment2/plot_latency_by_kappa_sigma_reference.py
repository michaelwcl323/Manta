#!/usr/bin/env python3
"""Plot mean consensus latency vs kappa for each (sigma, reference) series.

Reads per-run latencies from ``paper_data/original_data/Figure10a_10b/consensus_summary.csv``
and averages runs that share the same (sigma, kappa, reference).
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"] = 42
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from paper_figure_save import savefig_tight_target_aspect


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
DEFAULT_SUMMARY = REPO_ROOT / "paper_data" / "original_data" / "Figure10a_10b" / "consensus_summary.csv"
DEFAULT_OUTPUT = REPO_ROOT / "results" / "regenerate_graphs" / "latency_by_kappa_sigma_reference.pdf"

KAPPA_VALUES = [2, 3, 4]
SERIES_SPEC = [
    # (sigma, reference, legend, color, linestyle)
    (1, 4, r"$\sigma=1,\ \mathrm{ref}=4$", "#1b9e77", "-"),
    (1, 7, r"$\sigma=1,\ \mathrm{ref}=7$", "#7570b3", "-"),
    (1, 10, r"$\sigma=1,\ \mathrm{ref}=10$", "#d95f02", "-"),
    (2, 4, r"$\sigma=2,\ \mathrm{ref}=4$", "#1b9e77", "--"),
    (2, 7, r"$\sigma=2,\ \mathrm{ref}=7$", "#7570b3", "--"),
    (2, 10, r"$\sigma=2,\ \mathrm{ref}=10$", "#d95f02", "--"),
]


def load_mean_latency_s(summary_csv: Path) -> dict[tuple[int, int, int], float]:
    """Return mean consensus latency in seconds keyed by (sigma, kappa, reference)."""
    if not summary_csv.exists():
        raise SystemExit(f"missing summary csv: {summary_csv}")

    grouped: dict[tuple[int, int, int], list[float]] = defaultdict(list)
    with summary_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (int(row["sigma"]), int(row["kappa"]), int(row["reference"]))
            grouped[key].append(float(row["consensus_latency_ms"]) / 1000.0)

    if not grouped:
        raise SystemExit(f"no latency rows in {summary_csv}")

    return {key: sum(vals) / len(vals) for key, vals in grouped.items()}


def build_series(means: dict[tuple[int, int, int], float]) -> list[tuple[str, str, str, list[float]]]:
    series = []
    for sigma, reference, label, color, linestyle in SERIES_SPEC:
        ys = []
        for kappa in KAPPA_VALUES:
            key = (sigma, kappa, reference)
            if key not in means:
                raise SystemExit(f"missing averaged latency for sigma={sigma} kappa={kappa} ref={reference}")
            ys.append(means[key])
        series.append((label, color, linestyle, ys))
    return series


def draw(
    series: list[tuple[str, str, str, list[float]]],
    output_path: Path,
    *,
    auto_limits: bool = False,
) -> None:
    fig, ax = plt.subplots(figsize=(13.181, 8.787), dpi=180)

    legend_handles = []
    for label, color, linestyle, ys in series:
        legend_handles.append(
            Line2D(
                [],
                [],
                color=color,
                linestyle=linestyle,
                linewidth=4.982,
                marker="o",
                markersize=11.833,
                markeredgewidth=1.495,
                label=label,
            )
        )
        ax.plot(
            KAPPA_VALUES,
            ys,
            color=color,
            linestyle=linestyle,
            linewidth=4.982,
            marker="o",
            markersize=11.833,
            markeredgewidth=1.495,
            label=label,
        )

    ax.set_xlabel(r"Number of Solid Step $\kappa$", fontsize=37.368)
    ax.set_ylabel("Latency (s)", fontsize=37.368)
    ax.set_xticks(KAPPA_VALUES)
    if not auto_limits:
        ax.set_xlim(1.8, 4.2)
        ax.set_ylim(1.0, 2.3)
        ax.set_yticks([1.0, 1.5, 2.0])
    else:
        ax.margins(x=0.08, y=0.12)
    ax.set_box_aspect(1 / 1.5)

    ax.tick_params(axis="both", labelsize=37.368, width=2.242)
    for spine in ax.spines.values():
        spine.set_linewidth(2.242)
    ax.grid(True, linestyle=(0, (1, 2)), linewidth=1.246, color="0.78")
    ax.legend(
        handles=legend_handles,
        fontsize=29.894,
        frameon=True,
        loc="upper left",
        ncol=2,
        handlelength=2.2,
        columnspacing=1.4,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    savefig_tight_target_aspect(fig, output_path, 1.5, pad_inches=0.03, dpi=180)
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-csv",
        type=Path,
        default=DEFAULT_SUMMARY,
        help="Per-run consensus summary CSV (default: original_data/Figure10a_10b/consensus_summary.csv).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output figure path (PDF recommended).",
    )
    parser.add_argument(
        "--auto-limits",
        action="store_true",
        help="Do not apply paper-fixed xlim/ylim (for experiment reproduction).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    means = load_mean_latency_s(args.summary_csv.resolve())
    series = build_series(means)
    output = args.output.resolve()
    draw(series, output, auto_limits=args.auto_limits)
    print(output)


if __name__ == "__main__":
    main()
