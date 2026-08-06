#!/usr/bin/env python3
"""Plot mean consensus latency vs reference for kappa=2, by sigma.

Reads per-run latencies from ``paper_data/original_data/Figure10a_10b/consensus_summary.csv``
and averages runs that share the same (sigma, kappa=2, reference).
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

from paper_figure_save import savefig_tight_target_aspect


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
DEFAULT_SUMMARY = REPO_ROOT / "paper_data" / "original_data" / "Figure10a_10b" / "consensus_summary.csv"
DEFAULT_OUTPUT = REPO_ROOT / "results" / "regenerate_graphs" / "reference_impact_kappa2_by_sigma.pdf"

REFERENCES = [4, 7, 10]
KAPPA = 2
SERIES_SPEC = [
    # (sigma, legend, color, linestyle)
    (1, r"$\sigma=1$", "#1b9e77", "-"),
    (2, r"$\sigma=2$", "#7570b3", "--"),
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
    for sigma, label, color, linestyle in SERIES_SPEC:
        ys = []
        for reference in REFERENCES:
            key = (sigma, KAPPA, reference)
            if key not in means:
                raise SystemExit(
                    f"missing averaged latency for sigma={sigma} kappa={KAPPA} ref={reference}"
                )
            ys.append(means[key])
        series.append((label, color, linestyle, ys))
    return series


def draw(series: list[tuple[str, str, str, list[float]]], output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12.87, 8.58), dpi=180)

    for label, color, linestyle, ys in series:
        ax.plot(
            REFERENCES,
            ys,
            color=color,
            linewidth=5.397,
            linestyle=linestyle,
            marker="o",
            markersize=13.493,
            markeredgewidth=1.619,
            label=label,
        )

    ax.set_xlabel(r"Reference $ref$", fontsize=37.368)
    ax.set_ylabel("Latency (s)", fontsize=37.368)
    ax.set_xticks(REFERENCES)
    ax.set_xlim(3.5, 10.5)
    ax.set_ylim(1.0, 1.3)
    ax.set_yticks([1.0, 1.1, 1.2, 1.3])
    ax.set_box_aspect(1 / 1.5)

    ax.tick_params(axis="both", labelsize=37.368, width=2.429)
    for spine in ax.spines.values():
        spine.set_linewidth(2.429)
    ax.grid(True, linestyle=(0, (1, 2)), linewidth=1.349, color="0.78")
    ax.legend(fontsize=29.894, frameon=True, loc="upper left")

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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    means = load_mean_latency_s(args.summary_csv.resolve())
    series = build_series(means)
    output = args.output.resolve()
    draw(series, output)
    print(output)


if __name__ == "__main__":
    main()
