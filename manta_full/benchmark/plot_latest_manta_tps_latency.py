from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean

import numpy as np
import matplotlib

if not hasattr(np, "Inf"):
    np.Inf = np.inf

matplotlib.use("Agg")
import matplotlib.pyplot as plt


RESULT_DIR = Path(__file__).resolve().parent / "manta_result"
OUTPUT_PATH = RESULT_DIR / "latest_tps_latency.png"


def parse_int(text: str, label: str) -> int:
    match = re.search(rf"{re.escape(label)}: ([\d,]+)", text)
    if match is None:
        raise ValueError(f"missing field: {label}")
    return int(match.group(1).replace(",", ""))


def load_rows(result_dir: Path) -> list[dict[str, int | str]]:
    rows: list[dict[str, int | str]] = []
    for summary_path in sorted(result_dir.rglob("summary.txt")):
        entry = summary_path.parent
        if "plots" in summary_path.parts:
            continue

        text = summary_path.read_text()
        rows.append(
            {
                "name": str(entry.relative_to(result_dir)),
                "input_rate": parse_int(text, "Input rate"),
                "consensus_tps": parse_int(text, "Consensus TPS"),
                "consensus_latency": parse_int(text, "Consensus latency"),
                "end_to_end_tps": parse_int(text, "End-to-end TPS"),
                "end_to_end_latency": parse_int(text, "End-to-end latency"),
            }
        )

    if not rows:
        raise ValueError(f"no summary files found under {result_dir}")

    rows.sort(key=lambda row: (int(row["input_rate"]), str(row["name"])))
    return rows


def aggregate_rows(rows: list[dict[str, int | str]]) -> list[dict[str, float]]:
    grouped: dict[int, list[dict[str, int | str]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["input_rate"])].append(row)

    aggregated: list[dict[str, float]] = []
    for input_rate in sorted(grouped):
        items = grouped[input_rate]
        aggregated.append(
            {
                "input_rate": float(input_rate),
                "consensus_tps": mean(float(item["consensus_tps"]) for item in items),
                "consensus_latency": mean(float(item["consensus_latency"]) for item in items),
                "end_to_end_tps": mean(float(item["end_to_end_tps"]) for item in items),
                "end_to_end_latency": mean(float(item["end_to_end_latency"]) for item in items),
            }
        )
    return aggregated


def draw(
    rows: list[dict[str, int | str]],
    aggregated: list[dict[str, float]],
    title: str,
    output_path: Path,
) -> None:
    fig, ax = plt.subplots(figsize=(10, 6), dpi=180)

    consensus_color = "#f59e0b"
    e2e_color = "#06b6d4"

    ax.scatter(
        [int(row["consensus_tps"]) for row in rows],
        [int(row["consensus_latency"]) for row in rows],
        s=40,
        alpha=0.35,
        color=consensus_color,
        label="Consensus runs",
    )
    ax.scatter(
        [int(row["end_to_end_tps"]) for row in rows],
        [int(row["end_to_end_latency"]) for row in rows],
        s=40,
        alpha=0.35,
        color=e2e_color,
        label="End-to-end runs",
    )

    ax.plot(
        [row["consensus_tps"] for row in aggregated],
        [row["consensus_latency"] for row in aggregated],
        marker="o",
        linewidth=2.2,
        color=consensus_color,
        label="Consensus avg",
    )
    ax.plot(
        [row["end_to_end_tps"] for row in aggregated],
        [row["end_to_end_latency"] for row in aggregated],
        marker="o",
        linewidth=2.2,
        color=e2e_color,
        label="End-to-end avg",
    )

    for row in aggregated:
        label = f"{int(row['input_rate']) // 1000}k"
        ax.annotate(
            label,
            (row["end_to_end_tps"], row["end_to_end_latency"]),
            xytext=(5, 6),
            textcoords="offset points",
            fontsize=8,
            color="#0f172a",
        )

    ax.set_title(title)
    ax.set_xlabel("Throughput (tx/s)")
    ax.set_ylabel("Latency (ms)")
    ax.grid(True, linestyle="--", linewidth=0.6, alpha=0.5)
    ax.legend(frameon=True)
    ax.margins(x=0.06, y=0.08)

    fig.subplots_adjust(left=0.11, right=0.98, bottom=0.11, top=0.90)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, bbox_inches="tight")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot TPS-latency from benchmark summaries.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=RESULT_DIR,
        help="Directory containing benchmark run subdirectories with summary.txt files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output PNG path. Defaults to latest_tps_latency.png in the input directory.",
    )
    parser.add_argument(
        "--title",
        default="Manta TPS-Latency (latest top-level results)",
        help="Chart title.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_path = args.output.resolve() if args.output else input_dir / "tps_latency.png"

    rows = load_rows(input_dir)
    aggregated = aggregate_rows(rows)
    draw(rows, aggregated, args.title, output_path)
    print(output_path)


if __name__ == "__main__":
    main()
