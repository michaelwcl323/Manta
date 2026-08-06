#!/usr/bin/env python3
"""Plot complete Manta vs no-flexible-coin ablation (Figure 12).

Complete: Manta rows from ``paper_data/original_data/Figure11a/geo_consensus_tps_latency.csv``.
No flexible coin: averages of summary ``*.txt`` under ``paper_data/original_data/Figure12/``.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
COMPLETE_CSV = REPO_ROOT / "paper_data" / "original_data" / "Figure11a" / "geo_consensus_tps_latency.csv"
NOFLEXIBLE_DIR = REPO_ROOT / "paper_data" / "original_data" / "Figure12"
OUT_DIR = REPO_ROOT / "results" / "regenerate_graphs"

INPUT_RATES = [80000, 100000, 120000]

RE_BENCH = re.compile(
    r"Input rate:\s*([\d,]+)\s*tx/s"
    r"[\s\S]*?"
    r"Consensus TPS:\s*([\d,]+)\s*tx/s"
    r"[\s\S]*?"
    r"Consensus latency:\s*([\d,]+)\s*ms"
)


def load_complete_manta(csv_path: Path, rates: list[int]) -> tuple[list[float], list[float]]:
    """Return (tps, latency_s) for Manta at each requested input rate."""
    by_rate: dict[int, tuple[float, float]] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["protocol"].strip() != "Manta":
                continue
            ir = int(float(row["input_rate"]))
            tps = float(row["mean_consensus_tps"])
            lat_s = float(row["mean_consensus_latency_ms"]) / 1000.0
            by_rate[ir] = (tps, lat_s)

    tps_out: list[float] = []
    lat_out: list[float] = []
    for ir in rates:
        if ir not in by_rate:
            raise ValueError(f"Missing Manta row for input_rate={ir} in {csv_path}")
        tps, lat_s = by_rate[ir]
        tps_out.append(tps)
        lat_out.append(lat_s)
    return tps_out, lat_out


def load_noflexible(txt_dir: Path, rates: list[int]) -> tuple[list[float], list[float]]:
    """Average consensus TPS / latency over Figure12 summary files per input rate."""
    by_rate: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for path in sorted(txt_dir.glob("*.txt")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in RE_BENCH.finditer(text):
            ir = int(m.group(1).replace(",", ""))
            tps = int(m.group(2).replace(",", ""))
            lat_ms = int(m.group(3).replace(",", ""))
            by_rate[ir].append((tps, lat_ms))

    tps_out: list[float] = []
    lat_out: list[float] = []
    for ir in rates:
        pairs = by_rate.get(ir)
        if not pairs:
            raise ValueError(f"Missing no-flexible summaries for input_rate={ir} in {txt_dir}")
        mean_tps = sum(p[0] for p in pairs) / len(pairs)
        mean_lat_s = (sum(p[1] for p in pairs) / len(pairs)) / 1000.0
        tps_out.append(mean_tps)
        lat_out.append(mean_lat_s)
    return tps_out, lat_out


def main() -> None:
    plt.rcdefaults()
    plt.rcParams["font.family"] = "DejaVu Sans"
    plt.rcParams["pdf.fonttype"] = 42
    plt.rcParams["ps.fonttype"] = 42

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    input_rates = list(INPUT_RATES)
    complete_tps, complete_latency_s = load_complete_manta(COMPLETE_CSV, input_rates)
    noflexible_tps, noflexible_latency_s = load_noflexible(NOFLEXIBLE_DIR, input_rates)

    complete_tps_k = [v / 1000.0 for v in complete_tps]
    noflexible_tps_k = [v / 1000.0 for v in noflexible_tps]

    LABEL_FONTSIZE = 52
    TICK_FONTSIZE = 52
    LEGEND_FONTSIZE = 42
    COMBINED_FIG_W, COMBINED_FIG_H = 18, 12
    SINGLE_FIG_W, SINGLE_FIG_H = 15, 10

    fig = plt.figure(figsize=(COMBINED_FIG_W, COMBINED_FIG_H))
    gs = fig.add_gridspec(
        nrows=2,
        ncols=1,
        left=0.12,
        right=0.94,
        bottom=0.11,
        top=0.90,
        hspace=0.13,
    )
    ax_top = fig.add_subplot(gs[0, 0])
    ax_bottom = fig.add_subplot(gs[1, 0])
    ax_top.set_axisbelow(True)
    ax_bottom.set_axisbelow(True)

    rate_colors = ["#1399B2", "#daeff4", "#b22222"]  # 80k, 100k, 120k
    group_gap = 0.55
    intra_gap = 0.46
    bar_w = 0.36
    centers = np.arange(len(input_rates)) * (2 * intra_gap + group_gap)
    pos_complete = centers - intra_gap / 2
    pos_noflexible = centers + intra_gap / 2

    for i in range(len(input_rates)):
        color = rate_colors[i]
        ax_top.bar(
            pos_complete[i], complete_latency_s[i], bar_w,
            color=color, edgecolor="#4A4A4A", linewidth=0.6, hatch="", zorder=3
        )
        ax_top.bar(
            pos_noflexible[i], noflexible_latency_s[i], bar_w,
            color=color, edgecolor="#4A4A4A", linewidth=0.6, hatch="/", zorder=3
        )

        ax_bottom.bar(
            pos_complete[i], complete_tps_k[i], bar_w,
            color=color, edgecolor="#4A4A4A", linewidth=0.6, hatch="", zorder=3
        )
        ax_bottom.bar(
            pos_noflexible[i], noflexible_tps_k[i], bar_w,
            color=color, edgecolor="#4A4A4A", linewidth=0.6, hatch="/", zorder=3
        )

    ax_top.set_ylabel("Latency (s)", fontsize=LABEL_FONTSIZE)
    ax_bottom.set_ylabel("Throughput (KTps)", fontsize=LABEL_FONTSIZE)
    ax_bottom.set_xlabel("")

    xticks = []
    xticklabels = []
    for i in range(len(input_rates)):
        xticks.extend([pos_complete[i], pos_noflexible[i]])
        xticklabels.extend(["C", "NF"])
    ax_bottom.set_xticks(xticks)
    ax_bottom.set_xticklabels(xticklabels, fontsize=TICK_FONTSIZE)
    ax_top.tick_params(axis="x", labelbottom=False)

    ax_top.tick_params(axis="y", labelsize=TICK_FONTSIZE)
    ax_bottom.tick_params(axis="y", labelsize=TICK_FONTSIZE)

    ax_top.grid(axis="y", linestyle=(0, (2, 2)), alpha=0.35, zorder=0)
    ax_bottom.grid(axis="y", linestyle=(0, (2, 2)), alpha=0.35, zorder=0)

    ax_top.set_ylim(0, max(max(complete_latency_s), max(noflexible_latency_s)) * 1.22)
    ax_bottom.set_ylim(0, max(max(complete_tps_k), max(noflexible_tps_k)) * 1.18)

    legend_handles = [
        Patch(facecolor=rate_colors[0], edgecolor="#4A4A4A", label="80k"),
        Patch(facecolor=rate_colors[1], edgecolor="#4A4A4A", label="100k"),
        Patch(facecolor=rate_colors[2], edgecolor="#4A4A4A", label="120k"),
        Patch(facecolor="#EEEEEE", edgecolor="#4A4A4A", hatch="", label="complete"),
        Patch(facecolor="#EEEEEE", edgecolor="#4A4A4A", hatch="/", label="no flexible coin"),
    ]
    ax_top.legend(
        handles=legend_handles,
        ncol=5,
        loc="upper left",
        fontsize=LEGEND_FONTSIZE,
        frameon=True,
        handlelength=1.1,
        borderpad=0.3,
        columnspacing=0.8,
        handletextpad=0.3,
    )

    out_combined = OUT_DIR / "manta_consensus_tps_latency_bar_csv_vs_without_80k_100k_120k.pdf"
    fig.savefig(out_combined, format="pdf", bbox_inches="tight", pad_inches=0)

    SEP_LEFT, SEP_BOTTOM, SEP_WIDTH, SEP_HEIGHT = 0.16, 0.18, 0.76, 0.74
    sep_complete_color = "#1399b2"
    sep_noflexible_color = "#B22222"

    fig_lat = plt.figure(figsize=(SINGLE_FIG_W, SINGLE_FIG_H))
    ax_lat = fig_lat.add_axes([SEP_LEFT, SEP_BOTTOM, SEP_WIDTH, SEP_HEIGHT])
    ax_lat.set_axisbelow(True)
    for i in range(len(input_rates)):
        ax_lat.bar(
            pos_complete[i], complete_latency_s[i], bar_w,
            color=sep_complete_color, edgecolor="#4A4A4A", linewidth=0.6, hatch="", zorder=3
        )
        ax_lat.bar(
            pos_noflexible[i], noflexible_latency_s[i], bar_w,
            color=sep_noflexible_color, edgecolor="#4A4A4A", linewidth=0.6, hatch="/", zorder=3
        )
    ax_lat.set_ylabel("Latency (s)", fontsize=LABEL_FONTSIZE)
    ax_lat.set_xlabel("Input Rate (tx/s)", fontsize=LABEL_FONTSIZE)
    ax_lat.set_xticks(centers)
    ax_lat.set_xticklabels([str(x) for x in input_rates], fontsize=TICK_FONTSIZE)
    ax_lat.tick_params(axis="y", labelsize=TICK_FONTSIZE)
    ax_lat.grid(axis="y", linestyle=(0, (2, 2)), alpha=0.35, zorder=0)
    ax_lat.set_ylim(0, max(max(complete_latency_s), max(noflexible_latency_s)) * 1.22)
    out_lat = OUT_DIR / "manta_consensus_latency_only_no_legend.pdf"
    fig_lat.savefig(out_lat, format="pdf", bbox_inches="tight", pad_inches=0)

    fig_tps = plt.figure(figsize=(SINGLE_FIG_W, SINGLE_FIG_H))
    ax_tps = fig_tps.add_axes([SEP_LEFT, SEP_BOTTOM, SEP_WIDTH, SEP_HEIGHT])
    ax_tps.set_axisbelow(True)
    for i in range(len(input_rates)):
        ax_tps.bar(
            pos_complete[i], complete_tps_k[i], bar_w,
            color=sep_complete_color, edgecolor="#4A4A4A", linewidth=0.6, hatch="", zorder=3
        )
        ax_tps.bar(
            pos_noflexible[i], noflexible_tps_k[i], bar_w,
            color=sep_noflexible_color, edgecolor="#4A4A4A", linewidth=0.6, hatch="/", zorder=3
        )
    ax_tps.set_ylabel("Throughput (KTps)", fontsize=LABEL_FONTSIZE)
    ax_tps.set_xlabel("Input Rate (tx/s)", fontsize=LABEL_FONTSIZE)
    ax_tps.set_xticks(centers)
    ax_tps.set_xticklabels([str(x) for x in input_rates], fontsize=TICK_FONTSIZE)
    ax_tps.tick_params(axis="y", labelsize=TICK_FONTSIZE)
    ax_tps.grid(axis="y", linestyle=(0, (2, 2)), alpha=0.35, zorder=0)
    ax_tps.set_ylim(0, max(max(complete_tps_k), max(noflexible_tps_k)) * 1.18)
    out_tps = OUT_DIR / "manta_consensus_throughput_only_no_legend.pdf"
    fig_tps.savefig(out_tps, format="pdf", bbox_inches="tight", pad_inches=0)

    separate_legend_handles = [
        Patch(facecolor=sep_complete_color, edgecolor="#4A4A4A", hatch="", label="complete"),
        Patch(facecolor=sep_noflexible_color, edgecolor="#4A4A4A", hatch="/", label="no flexible coin"),
    ]
    fig_leg = plt.figure(figsize=(12.5, 1.55))
    ax_leg = fig_leg.add_axes([0, 0, 1, 1])
    ax_leg.axis("off")
    legend = ax_leg.legend(
        handles=separate_legend_handles,
        ncol=2,
        loc="center",
        fontsize=LEGEND_FONTSIZE,
        frameon=False,
        handlelength=1.1,
        borderpad=0.3,
        columnspacing=0.8,
        handletextpad=0.3,
    )
    fig_leg.canvas.draw()
    bbox = legend.get_window_extent(fig_leg.canvas.get_renderer()).transformed(
        fig_leg.dpi_scale_trans.inverted()
    )
    out_leg = OUT_DIR / "manta_consensus_legend_only.pdf"
    fig_leg.savefig(out_leg, format="pdf", bbox_inches=bbox, pad_inches=0)

    print(f"Wrote {out_combined}")
    print(f"Wrote {out_lat}")
    print(f"Wrote {out_tps}")
    print(f"Wrote {out_leg}")


if __name__ == "__main__":
    main()
