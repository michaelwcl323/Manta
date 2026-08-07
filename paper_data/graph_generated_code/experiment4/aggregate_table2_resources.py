#!/usr/bin/env python3
"""Aggregate CloudLab resource_usage_summary.txt files into Table 2 CSVs.

Accepts either:
  - a directory tree containing ``*.resource_usage_summary.txt`` / ``resource_usage_summary.txt``
  - or an existing per-run ``resource_summary.csv`` to re-average

Outputs:
  - table2_mean_resources.csv   (alias of per-run host-averaged rows)
  - table2_mean_over_runs.csv   (mean across runs per variant×rate)
  - table2.md                   (markdown table for the paper)
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

CPU_RE = re.compile(r"cpu avg/max\s*:\s*([\d.]+)%\s*/\s*([\d.]+)%")
RX_RE = re.compile(r"rx avg/max\s*:\s*([\d.]+)\s*/\s*([\d.]+)\s*Mbps")
TX_RE = re.compile(r"tx avg/max\s*:\s*([\d.]+)\s*/\s*([\d.]+)\s*Mbps")
RATE_RE = re.compile(r"-r(\d+)")
RUN_RE = re.compile(r"-run(\d+)")
VARIANT_HINTS = ("complete", "noflexible")


def mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else float("nan")


def parse_resource_summary(text: str) -> list[dict]:
    hosts: list[dict] = []
    current: dict | None = None
    skip_prefixes = (
        "Resource usage",
        "Generated at",
        "Total rows",
        "No valid",
    )
    for line in text.splitlines():
        raw = line.rstrip()
        if not raw:
            continue
        if not raw.startswith((" ", "\t")):
            if raw.startswith(skip_prefixes):
                continue
            if current and "cpu_avg_pct" in current:
                hosts.append(current)
            current = {"host": raw.strip()}
            continue
        if current is None:
            continue
        m = CPU_RE.search(raw)
        if m:
            current["cpu_avg_pct"] = float(m.group(1))
            current["cpu_max_pct"] = float(m.group(2))
            continue
        m = RX_RE.search(raw)
        if m:
            current["rx_avg_mbps"] = float(m.group(1))
            continue
        m = TX_RE.search(raw)
        if m:
            current["tx_avg_mbps"] = float(m.group(1))
            continue
    if current and "cpu_avg_pct" in current:
        hosts.append(current)
    return hosts


def infer_meta(path: Path) -> dict:
    name = path.name
    parts = list(path.parts)
    variant = None
    for hint in VARIANT_HINTS:
        if hint in parts or hint in name:
            variant = hint
            break
    rate_m = RATE_RE.search(name)
    run_m = RUN_RE.search(name)
    return {
        "variant": variant or "unknown",
        "input_rate": int(rate_m.group(1)) if rate_m else 0,
        "run": int(run_m.group(1)) if run_m else 0,
    }


def collect_from_dir(input_dir: Path) -> list[dict]:
    rows: list[dict] = []
    files = list(input_dir.rglob("resource_usage_summary.txt"))
    files.extend(input_dir.rglob("*.resource_usage_summary.txt"))
    # de-dupe
    seen = set()
    uniq = []
    for p in files:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        uniq.append(p)

    for path in sorted(uniq):
        hosts = parse_resource_summary(path.read_text(encoding="utf-8", errors="replace"))
        if not hosts:
            print(f"[table2] skip empty/unparsed: {path}")
            continue
        meta = infer_meta(path)
        rows.append(
            {
                "variant": meta["variant"],
                "input_rate": meta["input_rate"],
                "run": meta["run"],
                "cpu_avg_pct": mean([h["cpu_avg_pct"] for h in hosts]),
                "cpu_max_pct": mean([h["cpu_max_pct"] for h in hosts]),
                "rx_avg_mbps": mean([h["rx_avg_mbps"] for h in hosts]),
                "tx_avg_mbps": mean([h["tx_avg_mbps"] for h in hosts]),
                "n_hosts": len(hosts),
            }
        )
    return rows


def load_per_run_csv(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(
                {
                    "variant": row["variant"],
                    "input_rate": int(float(row["input_rate"])),
                    "run": int(float(row.get("run") or 0)),
                    "cpu_avg_pct": float(row["cpu_avg_pct"]),
                    "cpu_max_pct": float(row["cpu_max_pct"]),
                    "rx_avg_mbps": float(row["rx_avg_mbps"]),
                    "tx_avg_mbps": float(row["tx_avg_mbps"]),
                    "n_hosts": int(float(row.get("n_hosts") or 0)),
                }
            )
    return rows


def write_outputs(rows: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    per_run = output_dir / "table2_mean_resources.csv"
    also = output_dir / "resource_summary.csv"
    mean_path = output_dir / "table2_mean_over_runs.csv"
    md_path = output_dir / "table2.md"

    fields = [
        "variant",
        "input_rate",
        "run",
        "cpu_avg_pct",
        "cpu_max_pct",
        "rx_avg_mbps",
        "tx_avg_mbps",
        "n_hosts",
    ]
    for path in (per_run, also):
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in sorted(rows, key=lambda r: (r["variant"], r["input_rate"], r["run"])):
                writer.writerow({k: row.get(k, "") for k in fields})

    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["variant"], int(row["input_rate"]))].append(row)

    mean_fields = [
        "variant",
        "input_rate",
        "n_runs",
        "cpu_avg_pct",
        "cpu_max_pct",
        "rx_avg_mbps",
        "tx_avg_mbps",
    ]
    mean_rows = []
    for (variant, rate), group in sorted(grouped.items()):
        mean_rows.append(
            {
                "variant": variant,
                "input_rate": rate,
                "n_runs": len(group),
                "cpu_avg_pct": round(mean([g["cpu_avg_pct"] for g in group]), 3),
                "cpu_max_pct": round(mean([g["cpu_max_pct"] for g in group]), 3),
                "rx_avg_mbps": round(mean([g["rx_avg_mbps"] for g in group]), 3),
                "tx_avg_mbps": round(mean([g["tx_avg_mbps"] for g in group]), 3),
            }
        )

    with mean_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=mean_fields)
        writer.writeheader()
        for row in mean_rows:
            writer.writerow(row)

    lines = [
        "| variant | input_rate | CPU avg % | CPU max % | RX avg Mbps | TX avg Mbps |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in mean_rows:
        lines.append(
            f"| {row['variant']} | {row['input_rate']} | {row['cpu_avg_pct']:.2f} | "
            f"{row['cpu_max_pct']:.2f} | {row['rx_avg_mbps']:.2f} | {row['tx_avg_mbps']:.2f} |"
        )
    md = "\n".join(lines) + "\n"
    md_path.write_text(md, encoding="utf-8")
    print(md)
    print(f"Wrote {per_run}")
    print(f"Wrote {also}")
    print(f"Wrote {mean_path}")
    print(f"Wrote {md_path}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Aggregate Table 2 resource usage")
    p.add_argument("--input-dir", type=Path, help="Directory tree with resource_usage_summary.txt files")
    p.add_argument("--per-run-csv", type=Path, help="Existing resource_summary.csv to re-average")
    p.add_argument("--output-dir", type=Path, required=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.per_run_csv:
        rows = load_per_run_csv(args.per_run_csv)
    elif args.input_dir:
        rows = collect_from_dir(args.input_dir)
    else:
        raise SystemExit("provide --input-dir or --per-run-csv")
    if not rows:
        raise SystemExit("no resource rows found")
    write_outputs(rows, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
