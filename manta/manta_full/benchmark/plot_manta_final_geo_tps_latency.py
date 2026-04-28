#!/usr/bin/env python3
"""Plot TPS vs latency from manta_final_geo benchmark summaries (excludes old/)."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

# Matplotlib 3.3.x references np.Inf, removed in NumPy 2.0
if not hasattr(np, "Inf"):
    np.Inf = np.inf  # type: ignore[attr-defined]

import matplotlib as mpl
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
RESULT_DIR = ROOT / "manta_result" / "manta_final_geo"

# Publication-oriented defaults (serif, restrained palette)
_ACADEMIC_RC = {
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif", "Bitstream Vera Serif", "serif"],
    "axes.labelsize": 11,
    "axes.titlesize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 9,
    "axes.linewidth": 0.8,
    "xtick.direction": "in",
    "ytick.direction": "in",
    "xtick.top": True,
    "ytick.right": True,
    "figure.dpi": 150,
}

# Okabe–Ito (colorblind-safe)
_COLOR_CONSENSUS = "#0072B2"


def parse_summary(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    def num(label: str) -> int | None:
        m = re.search(rf"{re.escape(label)}\s*([\d,]+)\s*(?:tx/s|ms|B/s)", text)
        if not m:
            return None
        return int(m.group(1).replace(",", ""))
    inp = num("Input rate:")
    c_tps = num("Consensus TPS:")
    c_lat = num("Consensus latency:")
    if None in (inp, c_tps, c_lat):
        return None
    return {
        "dir": path.parent.name,
        "input_rate": inp,
        "consensus_tps": c_tps,
        "consensus_lat_ms": c_lat,
    }


def main() -> None:
    rows: list[dict] = []
    for p in sorted(RESULT_DIR.glob("*/summary.txt")):
        if "old" in p.parts:
            continue
        row = parse_summary(p)
        if row:
            rows.append(row)

    rows.sort(key=lambda r: (r["input_rate"], r["dir"]))

    c_tps = [r["consensus_tps"] for r in rows]
    c_lat = [r["consensus_lat_ms"] for r in rows]

    with mpl.rc_context(rc=_ACADEMIC_RC):
        fig, ax = plt.subplots(figsize=(5.2, 3.8))

        ax.plot(
            c_tps,
            c_lat,
            "-o",
            color=_COLOR_CONSENSUS,
            linewidth=1.0,
            markersize=5.5,
            markeredgecolor="0.15",
            markeredgewidth=0.45,
            clip_on=False,
        )

        ax.set_xlabel("Throughput (tx/s)")
        ax.set_ylabel("Latency (ms)")
        ax.set_title("Consensus throughput vs. latency")

        ax.grid(True, linestyle="-", linewidth=0.4, color="0.85", zorder=0)
        ax.set_axisbelow(True)

        fig.tight_layout()
        out = RESULT_DIR / "tps_latency_manta_final_geo.png"
        fig.savefig(out, dpi=200, bbox_inches="tight", facecolor="white")
        plt.close(fig)

    print(f"have saved {out} ({len(rows)} runs, excluding old/)")


if __name__ == "__main__":
    main()
