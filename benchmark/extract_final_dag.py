#!/usr/bin/env python3
"""
Extract the final DAG shape from consensus visualization logs.
By default, reads logs/primary-*.log and keeps the most complete line per round.
"""

import argparse
import glob
import json
import os

from benchmark.dag_vis import collect_best_round_snapshots
from benchmark.utils import PathMaker

def main() -> None:
    parser = argparse.ArgumentParser(description="Extract final DAG shape from logs.")
    parser.add_argument(
        "logs",
        nargs="*",
        help="Log files to parse (default: logs/primary-*.log).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON instead of text.",
    )
    parser.add_argument(
        "--out",
        help="Write output to a file instead of stdout.",
    )
    args = parser.parse_args()

    log_files = args.logs
    if not log_files:
        log_files = sorted(glob.glob(os.path.join(PathMaker.logs_path(), "primary-*.log")))

    best = collect_best_round_snapshots(log_files)
    if not best:
        print("No DAG visualization lines found.")
        return

    output_lines = []
    if args.json:
        output = [best[r] for r in sorted(best.keys())]
        output_lines = [json.dumps(output, indent=2)]
    else:
        for r in sorted(best.keys()):
            output_lines.append(f"Round {r}: {best[r]['raw']}")

    output_text = "\n".join(output_lines)
    if args.out:
        with open(args.out, "w") as f:
            f.write(output_text)
    else:
        print(output_text)


if __name__ == "__main__":
    main()
