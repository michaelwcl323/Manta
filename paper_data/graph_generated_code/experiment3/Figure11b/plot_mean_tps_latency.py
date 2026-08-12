#!/usr/bin/env python3
"""Aggregate bench summaries and plot mean consensus TPS vs consensus latency per protocol.

Points along each line are ordered by increasing input rate (load sweep).
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

plt.rcParams["font.family"] = "DejaVu Sans"

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[3]
DATA_ROOT = REPO_ROOT / "paper_data" / "original_data" / "Figure11b"
OUT_DIR = REPO_ROOT / "results" / "regenerate_graphs"

# Protocol display names to omit from the figure.
SKIP_PROTOCOLS = frozenset()
LINE_COLORS = [
    "#1399B2",
    "#6FA8DC",
    "#B22222",
    "#4C956C",
    "#7B5EA7",
]
PROTOCOL_COLORS = {
    "Chitu": "#1399B2",
    "DAG-Rider": "#F39C12",
    "Mahi-mahi": "#B22222",
    "Manta": "#4C956C",
    "Tusk": "#7B5EA7",
}
LINEWIDTH = 5.0
MARKERSIZE = 12
# Figure canvas aspect ratio width : height = 1.75 : 1  (e.g. 14in x 8in)
FIG_WIDTH = 14
FIG_HEIGHT = 8
REFERENCE_SINGLE_FIG_HEIGHT = 14
REFERENCE_LABEL_FONTSIZE = 52
REFERENCE_TICK_FONTSIZE = 52
REFERENCE_LEGEND_FONTSIZE = 42
LABEL_FONTSIZE = round(REFERENCE_LABEL_FONTSIZE * FIG_HEIGHT / REFERENCE_SINGLE_FIG_HEIGHT)
TICK_FONTSIZE = round(REFERENCE_TICK_FONTSIZE * FIG_HEIGHT / REFERENCE_SINGLE_FIG_HEIGHT)
LEGEND_FONTSIZE = round(REFERENCE_LEGEND_FONTSIZE * FIG_HEIGHT / REFERENCE_SINGLE_FIG_HEIGHT)
AXIS_LABEL_FONTSIZE = LABEL_FONTSIZE + 6

# One logical bench block: CONFIG (input rate) then RESULTS (consensus TPS & latency).
RE_BENCH = re.compile(
    r"Input rate:\s*([\d,]+)\s*tx/s"
    r"[\s\S]*?"
    r"Consensus TPS:\s*([\d,]+)\s*tx/s"
    r"[\s\S]*?"
    r"Consensus latency:\s*([\d,]+)\s*ms"
)


def parse_blocks(text: str) -> list[tuple[int, int, int]]:
    """Return list of (input_rate, tps, latency_ms) for each SUMMARY / run in the file."""
    out: list[tuple[int, int, int]] = []
    for m in RE_BENCH.finditer(text):
        ir = int(m.group(1).replace(",", ""))
        tps = int(m.group(2).replace(",", ""))
        lat = int(m.group(3).replace(",", ""))
        out.append((ir, tps, lat))
    return out


def protocol_from_path(rel: Path) -> str:
    top = rel.parts[0]
    mapping = {
        "Chitu-result": "Chitu",
        "Chitu-results": "Chitu",
        "Tusk-result": "Tusk",
        "Mahi-mahi-result": "Mahi-mahi",
        "DAG-rider-result": "DAG-Rider",
        "Manta-result": "Manta",
    }
    return mapping.get(top, top)


def collect() -> dict[str, dict[int, list[tuple[int, int]]]]:
    """protocol -> input_rate -> [(tps, lat), ...]"""
    data: dict[str, dict[int, list[tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
    for path in DATA_ROOT.rglob("*.txt"):
        if path.name == Path(__file__).name:
            continue
        rel = path.relative_to(DATA_ROOT)
        if rel.parts[0] in {".venv", "__pycache__"}:
            continue
        proto = protocol_from_path(rel)
        text = path.read_text(encoding="utf-8", errors="replace")
        for ir, tps, lat in parse_blocks(text):
            data[proto][ir].append((tps, lat))
    return data


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = collect()
    # mean consensus TPS & mean consensus latency per (protocol, input_rate)
    series: dict[str, list[tuple[int, float, float]]] = {}
    for proto, by_rate in raw.items():
        if proto in SKIP_PROTOCOLS:
            continue
        pts: list[tuple[int, float, float]] = []
        for ir in sorted(by_rate):
            pairs = by_rate[ir]
            mean_tps = sum(p[0] for p in pairs) / len(pairs)
            mean_lat = sum(p[1] for p in pairs) / len(pairs)
            pts.append((ir, mean_tps, mean_lat))
        pts.sort(key=lambda x: x[0])
        series[proto] = pts

    # constrained_layout: fit labels/ticks with minimal outer whitespace (keeps figsize 1.75:1).
    fig, ax = plt.subplots(figsize=(FIG_WIDTH, FIG_HEIGHT), layout="constrained")
    for i, (proto, pts) in enumerate(sorted(series.items(), key=lambda x: x[0].lower())):
        if not pts:
            continue
        color = PROTOCOL_COLORS.get(proto, LINE_COLORS[i % len(LINE_COLORS)])
        xs = [p[1] / 1000.0 for p in pts]
        ys = [p[2] / 1000.0 for p in pts]
        ax.plot(
            xs,
            ys,
            "o-",
            label=proto,
            color=color,
            markersize=MARKERSIZE,
            linewidth=LINEWIDTH,
            markeredgewidth=0.0,
            markeredgecolor=color,
            alpha=0.95,
        )

    ax.set_xlabel("Throughput (KTps)", fontsize=AXIS_LABEL_FONTSIZE)
    ax.set_ylabel("Latency (s)", fontsize=AXIS_LABEL_FONTSIZE, labelpad=6)
    ax.set_ylim(1, 7)
    ax.tick_params(axis="both", labelsize=TICK_FONTSIZE)
    ax.margins(x=0.05)
    ax.grid(True, linestyle=":", alpha=0.6)
    # Full figure bbox keeps canvas aspect exactly width:height = 1.75:1 (see FIG_*).
    out_pdf = OUT_DIR / "50nodes_protocol_comparison.pdf"
    fig.savefig(out_pdf)
    print(f"Wrote {out_pdf}")


if __name__ == "__main__":
    main()
