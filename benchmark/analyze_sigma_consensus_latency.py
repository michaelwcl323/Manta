#!/usr/bin/env python3
"""Analyze consensus latency across sigma values for sigma_spill_boundary_final."""

from __future__ import annotations

import argparse
import csv
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


SIGMAS = (2, 3, 4, 5)
REFS = (2, 4, 7)
BAD_LATENCY_MS = 5000


def parse_int(text: str, label: str) -> int:
    match = re.search(rf"{re.escape(label)}: ([\d,]+)", text)
    if match is None:
        raise ValueError(f"missing field: {label}")
    return int(match.group(1).replace(",", ""))


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100.0
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    weight = rank - lo
    return ordered[lo] * (1 - weight) + ordered[hi] * weight


@dataclass
class RunStats:
    sigma: int
    ref: int
    run_dir: Path
    summary_latency_ms: int
    summary_tps: int
    csv_mean_ms: float | None = None
    csv_p50_ms: float | None = None
    csv_p90_ms: float | None = None
    has_latency_csv: bool = False


@dataclass
class CellStats:
    sigma: int
    ref: int
    runs: list[RunStats] = field(default_factory=list)

    @property
    def n_runs(self) -> int:
        return len(self.runs)

    @property
    def mean_summary_latency(self) -> float:
        return statistics.mean(r.summary_latency_ms for r in self.runs)

    @property
    def mean_summary_tps(self) -> float:
        return statistics.mean(r.summary_tps for r in self.runs)

    @property
    def mean_csv_p50(self) -> float | None:
        vals = [r.csv_p50_ms for r in self.runs if r.csv_p50_ms is not None]
        return statistics.mean(vals) if vals else None


def load_latency_csv(path: Path) -> tuple[float, float, float]:
    latencies: list[float] = []
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if row.get("metric") != "consensus_latency":
                continue
            latencies.append(float(row["latency_ms"]))
    if not latencies:
        raise ValueError(f"no consensus_latency rows in {path}")
    return (
        statistics.mean(latencies),
        percentile(latencies, 50),
        percentile(latencies, 90),
    )


def discover_runs(root: Path) -> dict[tuple[int, int], CellStats]:
    cells: dict[tuple[int, int], CellStats] = {}
    for sigma in SIGMAS:
        for ref in REFS:
            load_tag = f"s{sigma}_k2_r{ref}_c7_spill_on_bound_on"
            cell_dir = root / load_tag
            if not cell_dir.is_dir():
                cells[(sigma, ref)] = CellStats(sigma=sigma, ref=ref)
                continue

            runs: list[RunStats] = []
            for run_dir in sorted(cell_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                summary_path = run_dir / "summary.txt"
                if not summary_path.is_file():
                    continue
                text = summary_path.read_text()
                run = RunStats(
                    sigma=sigma,
                    ref=ref,
                    run_dir=run_dir,
                    summary_latency_ms=parse_int(text, "Consensus latency"),
                    summary_tps=parse_int(text, "Consensus TPS"),
                )
                csv_path = run_dir / "latency.csv"
                if csv_path.is_file():
                    run.has_latency_csv = True
                    run.csv_mean_ms, run.csv_p50_ms, run.csv_p90_ms = load_latency_csv(
                        csv_path
                    )
                runs.append(run)
            cells[(sigma, ref)] = CellStats(sigma=sigma, ref=ref, runs=runs)
    return cells


def fmt(v: float | None, digits: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}"


def print_table(title: str, headers: list[str], rows: list[list[str]]) -> None:
    print(f"\n{title}")
    print(" | ".join(headers))
    print(" | ".join(["---"] * len(headers)))
    for row in rows:
        print(" | ".join(row))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent
        / "manta_result"
        / "sigma_spill_boundary_final"
        / "geo",
    )
    args = parser.parse_args()
    root: Path = args.root

    cells = discover_runs(root)

    # Per-run detail
    run_rows: list[list[str]] = []
    bad_runs: list[str] = []
    for sigma in SIGMAS:
        for ref in REFS:
            cell = cells[(sigma, ref)]
            for run in cell.runs:
                if run.summary_latency_ms > BAD_LATENCY_MS:
                    bad_runs.append(
                        f"σ={sigma} ref={ref} {run.run_dir.name}: "
                        f"{run.summary_latency_ms} ms (summary)"
                    )
                run_rows.append(
                    [
                        str(sigma),
                        str(ref),
                        run.run_dir.name.split("_")[-6]
                        if "run" in run.run_dir.name
                        else run.run_dir.name[-20:],
                        str(run.summary_latency_ms),
                        str(run.summary_tps),
                        fmt(run.csv_mean_ms),
                        fmt(run.csv_p50_ms),
                        fmt(run.csv_p90_ms),
                        "yes" if run.has_latency_csv else "no",
                    ]
                )

    print_table(
        "Per-run consensus latency",
        [
            "σ",
            "ref",
            "run",
            "summary_ms",
            "summary_tps",
            "csv_mean",
            "csv_p50",
            "csv_p90",
            "has_csv",
        ],
        run_rows,
    )

    # Aggregate per (sigma, ref)
    agg_rows: list[list[str]] = []
    agg_by_ref: dict[int, list[tuple[int, float, int]]] = defaultdict(list)
    p50_rows: list[list[str]] = []
    for sigma in SIGMAS:
        for ref in REFS:
            cell = cells[(sigma, ref)]
            if cell.n_runs == 0:
                agg_rows.append(
                    [str(sigma), str(ref), "0", "—", "—", "—", "—", "—"]
                )
                p50_rows.append([str(sigma), str(ref), "0", "—"])
                continue
            mean_lat = cell.mean_summary_latency
            delta = mean_lat - cells[(2, ref)].mean_summary_latency if cells[(2, ref)].n_runs else float("nan")
            agg_by_ref[ref].append((sigma, mean_lat, cell.n_runs))
            agg_rows.append(
                [
                    str(sigma),
                    str(ref),
                    str(cell.n_runs),
                    fmt(mean_lat, 1),
                    fmt(cell.mean_summary_tps, 0),
                    fmt(delta, 1),
                    fmt(cell.mean_csv_p50, 1),
                    "complete" if cell.n_runs >= 3 else f"partial ({cell.n_runs}/3)",
                ]
            )
            p50_rows.append(
                [
                    str(sigma),
                    str(ref),
                    str(cell.n_runs),
                    fmt(cell.mean_csv_p50, 1),
                ]
            )

    print_table(
        "Aggregate mean summary latency per (σ, ref)",
        [
            "σ",
            "ref",
            "n_runs",
            "mean_latency_ms",
            "mean_tps",
            "Δ vs σ=2",
            "mean_csv_p50",
            "status",
        ],
        agg_rows,
    )

    # Ranking within each ref
    for ref in REFS:
        ranked = sorted(agg_by_ref[ref], key=lambda x: x[1])
        if not ranked:
            print(f"\nref={ref}: no data")
            continue
        baseline = next((lat for s, lat, _ in ranked if s == 2), None)
        rank_rows = []
        for rank, (sigma, lat, n) in enumerate(ranked, 1):
            delta = lat - baseline if baseline is not None else float("nan")
            rank_rows.append(
                [
                    str(rank),
                    str(sigma),
                    str(n),
                    fmt(lat, 1),
                    fmt(delta, 1),
                ]
            )
        print_table(
            f"Ranking by mean latency (ref={ref}, lowest first)",
            ["rank", "σ", "n_runs", "mean_ms", "Δ vs σ=2"],
            rank_rows,
        )

    # Pivot table: rows=ref, cols=sigma
    pivot_headers = ["ref"] + [f"σ{s}" for s in SIGMAS] + [f"σ{s} Δ" for s in SIGMAS if s != 2]
    pivot_rows: list[list[str]] = []
    for ref in REFS:
        row = [str(ref)]
        base = cells[(2, ref)].mean_summary_latency if cells[(2, ref)].n_runs else None
        for sigma in SIGMAS:
            cell = cells[(sigma, ref)]
            row.append(
                fmt(cell.mean_summary_latency, 1) if cell.n_runs else "—"
            )
        for sigma in (3, 4, 5):
            cell = cells[(sigma, ref)]
            if base is not None and cell.n_runs:
                row.append(fmt(cell.mean_summary_latency - base, 1))
            else:
                row.append("—")
        pivot_rows.append(row)
    print_table(
        "Pivot: mean summary latency (ms) by ref × σ",
        pivot_headers,
        pivot_rows,
    )

    print_table(
        "Digest-level p50 (mean of per-run p50)",
        ["σ", "ref", "n_runs", "mean_csv_p50_ms"],
        p50_rows,
    )

    # Completeness
    print("\nCompleteness (expected 3 runs per cell):")
    for sigma in SIGMAS:
        for ref in REFS:
            cell = cells[(sigma, ref)]
            status = "OK" if cell.n_runs >= 3 else f"INCOMPLETE {cell.n_runs}/3"
            print(f"  σ={sigma} ref={ref}: {cell.n_runs} runs — {status}")

    if bad_runs:
        print(f"\nRuns with summary consensus latency > {BAD_LATENCY_MS} ms:")
        for line in bad_runs:
            print(f"  {line}")
    else:
        print(f"\nNo runs with summary consensus latency > {BAD_LATENCY_MS} ms.")


if __name__ == "__main__":
    main()
