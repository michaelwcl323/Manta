#!/usr/bin/env python3
"""
Extract the final DAG shape from consensus visualization logs.
By default, reads logs/primary-*.log and prints the latest line per round.
"""

import argparse
import glob
import json
import os
import re
from typing import Dict, List, Any


ROUND_LINE_RE = re.compile(r"\bRound\s+(\d+):\s+(.*)")
VERTEX_RE = re.compile(r"\(Vertex(\d+)\)\[([^\]]*)\](?:\s+weak=\[([^\]]*)\])?")
PARENT_RE = re.compile(r"\[(w?)(\d+|\?),\s*(\d+|\?)\]")


def parse_round_line(line: str) -> Dict[str, Any]:
    match = ROUND_LINE_RE.search(line)
    if not match:
        return {}
    round_num = int(match.group(1))
    payload = match.group(2).strip()

    vertices = []
    for vertex_match in VERTEX_RE.finditer(payload):
        vertex_id = int(vertex_match.group(1))
        parents_blob = vertex_match.group(2).strip()
        weak_blob = (vertex_match.group(3) or "").strip()
        parents = []
        if parents_blob:
            for parent_match in PARENT_RE.finditer(parents_blob):
                weak_flag, round_str, node_str = parent_match.groups()
                parent = {
                    "round": None if round_str == "?" else int(round_str),
                    "node": None if node_str == "?" else int(node_str),
                    "weak": weak_flag == "w",
                }
                parents.append(parent)
        weak_parents = []
        if weak_blob:
            for parent_match in PARENT_RE.finditer(weak_blob):
                weak_flag, round_str, node_str = parent_match.groups()
                parent = {
                    "round": None if round_str == "?" else int(round_str),
                    "node": None if node_str == "?" else int(node_str),
                    "weak": True if weak_flag == "w" else True,
                }
                weak_parents.append(parent)
        vertices.append(
            {
                "vertex": vertex_id,
                "parents": parents,
                "weak_parents": weak_parents,
            }
        )

    return {"round": round_num, "vertices": vertices, "raw": payload}


def collect_latest_rounds(log_files: List[str]) -> Dict[int, Dict[str, Any]]:
    latest: Dict[int, Dict[str, Any]] = {}
    for path in log_files:
        if not os.path.exists(path):
            continue
        with open(path, "r", errors="replace") as f:
            for line in f:
                match = ROUND_LINE_RE.search(line)
                if not match:
                    continue
                round_num = int(match.group(1))
                parsed = parse_round_line(line)
                if parsed:
                    latest[round_num] = parsed
    return latest


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
        log_files = sorted(glob.glob("logs/primary-*.log"))

    latest = collect_latest_rounds(log_files)
    if not latest:
        print("No DAG visualization lines found.")
        return

    output_lines = []
    if args.json:
        output = [latest[r] for r in sorted(latest.keys())]
        output_lines = [json.dumps(output, indent=2)]
    else:
        for r in sorted(latest.keys()):
            output_lines.append(f"Round {r}: {latest[r]['raw']}")

    output_text = "\n".join(output_lines)
    if args.out:
        with open(args.out, "w") as f:
            f.write(output_text)
    else:
        print(output_text)


if __name__ == "__main__":
    main()
