#!/usr/bin/env python3
"""
Extract all weak edges and their rounds from a DAG visualization file.
Defaults to benchmark/final_dag.txt.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Dict, List, Tuple

from benchmark.utils import PathMaker

ROUND_LINE_RE = re.compile(r"\bRound\s+(\d+):\s+(.*)")
VERTEX_ID_RE = re.compile(r"\(Vertex(\d+)\)")
PARENT_RE = re.compile(r"\[(w?)(\d+|\?),\s*(\d+|\?)\]")


def parse_round_line(line: str) -> List[Tuple[int, int, int, int]]:
    match = ROUND_LINE_RE.search(line)
    if not match:
        return []
    round_num = int(match.group(1))
    payload = match.group(2).strip()

    edges: List[Tuple[int, int, int, int]] = []
    for chunk in payload.split(" --- "):
        vertex_match = VERTEX_ID_RE.search(chunk)
        if not vertex_match:
            continue
        vertex_id = int(vertex_match.group(1))

        for parent_match in PARENT_RE.finditer(chunk):
            weak_flag, round_str, node_str = parent_match.groups()
            if weak_flag != "w":
                continue
            if round_str == "?" or node_str == "?":
                continue
            edges.append((round_num, vertex_id, int(round_str), int(node_str)))

    return edges


def collect_weak_edges(path: str) -> List[Tuple[int, int, int, int]]:
    if not os.path.exists(path):
        return []
    seen = set()
    edges: List[Tuple[int, int, int, int]] = []
    with open(path, "r", errors="replace") as f:
        for line in f:
            for edge in parse_round_line(line):
                if edge in seen:
                    continue
                seen.add(edge)
                edges.append(edge)
    return edges


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract weak edges with their rounds."
    )
    parser.add_argument(
        "--input",
        default=PathMaker.final_dag_file(),
        help="Path to a DAG visualization file (default: current run final_dag.txt).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON instead of text.",
    )
    args = parser.parse_args()

    edges = collect_weak_edges(args.input)
    if args.json:
        output: List[Dict[str, int]] = []
        for round_num, vertex_id, parent_round, parent_node in edges:
            output.append(
                {
                    "round": round_num,
                    "vertex": vertex_id,
                    "parent_round": parent_round,
                    "parent_node": parent_node,
                }
            )
        print(json.dumps(output, indent=2))
        return

    for round_num, vertex_id, parent_round, parent_node in edges:
        print(
            f"Round {round_num} Vertex{vertex_id}: "
            f"[w{parent_round},{parent_node}]"
        )


if __name__ == "__main__":
    main()
