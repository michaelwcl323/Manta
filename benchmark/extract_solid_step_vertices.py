#!/usr/bin/env python3
"""
Extract "Current round: X, The number of the solid step vertices is Y" logs
from primary logs and output a CSV.
"""

import argparse
import csv
import glob
import os
import re
from typing import List, Dict, Any


LINE_RE = re.compile(
    r"\[(?P<ts>[^]]+)\s+DEBUG\s+primary::(?:proposer|aggregators)\]\s+"
    r"Current round:\s+(?P<round>\d+),\s+The number of the solid step vertices is\s+(?P<count>\d+)"
)


def parse_logs(paths: List[str]) -> List[Dict[str, Any]]:
    # Track the max solid_step_vertices per (primary, round).
    max_by_key: Dict[tuple, Dict[str, Any]] = {}
    for path in paths:
        if not os.path.exists(path):
            continue
        primary_name = os.path.splitext(os.path.basename(path))[0]
        with open(path, "r", errors="replace") as f:
            for line in f:
                match = LINE_RE.search(line)
                if not match:
                    continue
                round_num = int(match.group("round"))
                count = int(match.group("count"))
                ts = match.group("ts")
                key = (primary_name, round_num)
                existing = max_by_key.get(key)
                if existing is None or count > existing["solid_step_vertices"]:
                    max_by_key[key] = {
                        "primary": primary_name,
                        "timestamp": ts,
                        "round": round_num,
                        "solid_step_vertices": count,
                    }
                elif count == existing["solid_step_vertices"]:
                    # Keep the latest timestamp for equal max values.
                    if ts > existing["timestamp"]:
                        existing["timestamp"] = ts
    return list(max_by_key.values())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract solid step vertices logs to CSV."
    )
    parser.add_argument(
        "logs",
        nargs="*",
        help="Log files to parse (default: logs/primary-*.log).",
    )
    parser.add_argument(
        "--out",
        help="Write CSV output to a file instead of stdout.",
    )
    args = parser.parse_args()

    log_files = args.logs
    if not log_files:
        log_files = sorted(glob.glob("logs/primary-*.log"))

    rows = parse_logs(log_files)
    rows.sort(key=lambda r: (r["round"], r["primary"], r["timestamp"]))
    fieldnames = ["primary", "timestamp", "round", "solid_step_vertices"]

    if args.out:
        with open(args.out, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    else:
        writer = csv.DictWriter(os.sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
