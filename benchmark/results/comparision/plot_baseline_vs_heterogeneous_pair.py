#!/usr/bin/env python3
"""
Generate two comparison figures for the selected baseline vs heterogeneous pair:

1. TPS-latency comparison across available rates.
2. Certificate collection comparison at 30k for node 0, rounds 200-350.

Outputs are written to `results/comparision/` as requested.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, stdev

import matplotlib.pyplot as plt
from matplotlib.ticker import StrMethodFormatter

SCRIPT_DIR = Path(__file__).resolve().parent
BENCHMARK_DIR = SCRIPT_DIR.parent.parent
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

from plot_certificate_progress import (
    _completed_rows,
    _load_rows,
    _prepare_rows_for_plotting,
    configure_plot_style,
    filter_certificate_rows,
)


OUTPUT_DIR = Path("results/comparision")
NODE_ID = 0
START_ROUND = 200
END_ROUND = 350
MAX_RATE = 50_000
TOTAL_VERTICES = 10

# 两张图共用的颜色设置。
# 后面如果想换配色，优先改这里。
# `LOW_HETEROGENEITY_COLOR`:
#   对应低异质性那组，也就是 `80ms balanced` 的线、点和图例颜色。
# `HIGH_HETEROGENEITY_COLOR`:
#   对应高异质性那组，也就是 `geo custom-high-5` 的线、点和图例颜色。
# `ANNOTATION_COLOR`:
#   对应图中箭头、辅助线等标注元素的颜色。
# `ANNOTATION_TEXT_COLOR`:
#   对应标注文字的颜色。
# `GRID_COLOR`:
#   对应网格线颜色。
# `FRAME_COLOR`:
#   对应两张图四周边框颜色。
# `LEGEND_EDGE_COLOR`:
#   对应图例外框颜色。
LOW_HETEROGENEITY_COLOR = "#F18F01"
HIGH_HETEROGENEITY_COLOR = "#A23B72"
ANNOTATION_COLOR = "#5a5a5a"
ANNOTATION_TEXT_COLOR = "#3a3a3a"
GRID_COLOR = "#9a9a9a"
FRAME_COLOR = "black"
LEGEND_EDGE_COLOR = "#cfcfcf"

PROGRESS_SERIES = [
    {
        "label": "Low heterogeneity",
        # cert 图里“低异质性 / 80ms balanced”这条曲线的颜色。
        "color": LOW_HETEROGENEITY_COLOR,
        "marker": "o",
        "csv_paths": [
            Path(
                "results/80ms/balanced/"
                "20260401_024346_n10_r30000_run1/"
                "80ms_balanced_round_certificate_analysis.csv"
            ),
            Path(
                "results/80ms/balanced/"
                "20260401_024346_n10_r30000_run2/"
                "80ms_balanced_round_certificate_analysis.csv"
            ),
        ],
    },
    {
        "label": "High heterogeneity",
        # cert 图里“高异质性 / geo custom-high-5”这条曲线的颜色。
        "color": HIGH_HETEROGENEITY_COLOR,
        "marker": "s",
        "csv_paths": [
            Path(
                "results/geo/custom-high-5/"
                "20260402_033338_n10_r30000_run1/"
                "geo_custom-high-5_round_certificate_analysis.csv"
            ),
            Path(
                "results/geo/custom-high-5/"
                "20260402_033338_n10_r30000_run2/"
                "geo_custom-high-5_round_certificate_analysis.csv"
            ),
        ],
    },
]

TPS_SERIES = [
    {
        "label": "Low heterogeneity",
        "root": Path("results/80ms/balanced"),
        "summary_name": "80ms_balanced_summary.txt",
        # TPS-latency 图里“低异质性 / 80ms balanced”的线和散点颜色。
        "color": LOW_HETEROGENEITY_COLOR,
        "marker": "o",
        "annotate_dx": 8,
    },
    {
        "label": "High heterogeneity",
        "root": Path("results/geo/custom-high-5"),
        "summary_name": "geo_custom-high-5_summary.txt",
        # TPS-latency 图里“高异质性 / geo custom-high-5”的线和散点颜色。
        "color": HIGH_HETEROGENEITY_COLOR,
        "marker": "s",
        "annotate_dx": -20,
    },
]

RUN_DIR_PATTERN = re.compile(
    r"(?P<timestamp>\d{8}_\d{6})_n(?P<nodes>\d+)_r(?P<rate>\d+)_run(?P<run>\d+)$"
)
TPS_PATTERN = re.compile(r"End-to-end TPS: ([\d,]+) tx/s")
LATENCY_PATTERN = re.compile(r"End-to-end latency: ([\d,]+) ms")


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


def _save_figure_png_and_pdf(fig, output_path: Path):
    """同时保存 PNG 和 PDF，两者使用同一个文件名主干。"""
    png_path = output_path.with_suffix(".png")
    pdf_path = output_path.with_suffix(".pdf")
    fig.savefig(png_path, dpi=220, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    return png_path, pdf_path


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
        completed_rows = _completed_rows(filtered_rows)
        prepared_rows.extend(_prepare_rows_for_plotting(completed_rows, cert_columns))
        round_end_values.extend(
            float(row["Round_End_Time_ms"])
            for row in completed_rows
            if row.get("Round_End_Time_ms")
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


def plot_progress_comparison(output_path: Path):
    curves = [_compute_progress_curve(spec) for spec in PROGRESS_SERIES]
    low_curve = next(curve for curve in curves if curve["label"] == "Low heterogeneity")
    high_curve = next(curve for curve in curves if curve["label"] == "High heterogeneity")

    fig, ax = plt.subplots(figsize=(8.6, 5.8))
    for curve in curves:
        ax.plot(
            curve["averages"],
            curve["progress_pct"],
            marker=curve["marker"],
            linewidth=2.2,
            markersize=5.5,
            color=curve["color"],
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
        fontsize=10,
        color=ANNOTATION_TEXT_COLOR,
    )

    ax.set_title("Certificate Collection Comparison", fontsize=13, pad=10)
    ax.set_xlabel("Time (ms)", fontsize=11)
    ax.set_ylabel("Collected vertices (%)", fontsize=11)
    ax.set_ylim(0, 104)
    ax.set_yticks(range(10, 101, 10))
    ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.0f}"))
    ax.grid(True, axis="both", linestyle=(0, (2.2, 2.2)), alpha=0.28, color=GRID_COLOR)
    ax.tick_params(direction="in", top=True, right=True)
    ax.legend(frameon=True, facecolor="white", edgecolor=LEGEND_EDGE_COLOR, fontsize=10)
    for spine in ax.spines.values():
        spine.set_visible(True)
        # 这里控制 cert 图四周边框的颜色。
        spine.set_color(FRAME_COLOR)
        spine.set_linewidth(0.9)
    fig.tight_layout()
    saved_paths = _save_figure_png_and_pdf(fig, output_path)
    plt.close(fig)

    return curves, saved_paths


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
    if tps is None or latency_ms is None:
        return None

    return SummaryPoint(
        rate=rate,
        timestamp=match.group("timestamp"),
        run=int(match.group("run")),
        tps=tps,
        latency_ms=latency_ms,
        path=path,
    )


def _collect_workload_points(root: Path, summary_name: str) -> list[SummaryPoint]:
    all_points = []
    for path in sorted(root.glob(f"**/{summary_name}")):
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
            if point.timestamp != latest_timestamp:
                continue
            if point.tps <= 0 or point.latency_ms <= 0:
                continue
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


def plot_tps_latency_comparison(output_path: Path):
    fig, ax = plt.subplots(figsize=(8.6, 5.8))
    series_rows = []

    for spec in TPS_SERIES:
        points = _collect_workload_points(spec["root"], spec["summary_name"])
        aggregated = _aggregate(points)
        if not aggregated:
            continue

        ax.scatter(
            [point.tps / 1000.0 for point in points],
            [point.latency_ms / 1000.0 for point in points],
            color=spec["color"],
            alpha=0.16,
            s=30,
        )

        ax.errorbar(
            [row["mean_tps"] / 1000.0 for row in aggregated],
            [row["mean_latency_ms"] / 1000.0 for row in aggregated],
            xerr=[row["std_tps"] / 1000.0 for row in aggregated],
            yerr=[row["std_latency_ms"] / 1000.0 for row in aggregated],
            fmt=f"-{spec['marker']}",
            linewidth=2.2,
            markersize=6,
            capsize=4,
            color=spec["color"],
            ecolor=spec["color"],
            label=spec["label"],
        )

        for row in aggregated:
            ax.annotate(
                f"{row['rate'] // 1000}k",
                (row["mean_tps"] / 1000.0, row["mean_latency_ms"] / 1000.0),
                textcoords="offset points",
                xytext=(spec["annotate_dx"], 6),
                fontsize=9,
                color=spec["color"],
            )

        series_rows.append((spec["label"], aggregated))

    ax.set_title("End-to-end Latency vs Throughput", fontsize=13, pad=10)
    ax.set_xlabel("Throughput (KTps)", fontsize=11)
    ax.set_ylabel("End-to-end Latency (s)", fontsize=11)
    ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.1f}"))
    ax.grid(True, axis="both", linestyle=(0, (2.2, 2.2)), alpha=0.28, color=GRID_COLOR)
    ax.tick_params(direction="in", top=True, right=True)
    ax.legend(frameon=True, facecolor="white", edgecolor=LEGEND_EDGE_COLOR, fontsize=10)
    for spine in ax.spines.values():
        spine.set_visible(True)
        # 这里控制 TPS-latency 图四周边框的颜色。
        spine.set_color(FRAME_COLOR)
        spine.set_linewidth(0.9)
    fig.tight_layout()
    saved_paths = _save_figure_png_and_pdf(fig, output_path)
    plt.close(fig)

    return series_rows, saved_paths


def main():
    configure_plot_style()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    progress_output = OUTPUT_DIR / (
        "cert_collection_compare_80ms_balanced_vs_geo_custom-high-5"
        "_node0_rounds_200_350.png"
    )
    tps_output = OUTPUT_DIR / (
        "tps_latency_compare_80ms_balanced_vs_geo_custom-high-5_upto_50k.png"
    )

    curves, progress_saved_paths = plot_progress_comparison(progress_output)
    series_rows, tps_saved_paths = plot_tps_latency_comparison(tps_output)

    print(f"Saved cert comparison to: {progress_saved_paths[0]}")
    print(f"Saved cert comparison to: {progress_saved_paths[1]}")
    for curve in curves:
        print(
            f"  {curve['label']}: completed_rounds={curve['completed_rounds']}, "
            f"max_rank={max(curve['progress'])}"
        )

    print(f"Saved TPS-latency comparison to: {tps_saved_paths[0]}")
    print(f"Saved TPS-latency comparison to: {tps_saved_paths[1]}")
    for label, rows in series_rows:
        for row in rows:
            print(
                f"  {label}: rate={row['rate']}, runs={row['runs_used']}, "
                f"mean_tps={row['mean_tps']:.1f}, mean_latency_ms={row['mean_latency_ms']:.1f}"
            )


if __name__ == "__main__":
    main()
