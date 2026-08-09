#!/usr/bin/env python3
"""
Extract one-valent / zero-valent / bivalent counts from primary logs.

By default, reads logs/primary-*.log and writes a plain-text summary.
The script deduplicates FAST_PATH_CHECK lines by round to avoid counting
the same round multiple times across different primary logs.
"""

import argparse
import glob
import os
import re
from typing import Dict, List, Set


FAST_PATH_RE = re.compile(
    r"FAST_PATH_CHECK\s+round=(?P<round>\d+)\s+threshold=(?P<threshold>\d+)\s+"
    r"undecided=(?P<bivalent>\d+)\s+decide_zero=(?P<zero>\d+)\s+decide_one=(?P<one>\d+)"
)

COMMITTED_RE = re.compile(
    r"DAG_COMMITTED\s+path=(?P<path>fast|slow)\s+round=(?P<round>\d+)\s+"
    r"node=(?P<node>\d+)\s+digest=(?P<digest>\S+)"
)


def default_logs() -> List[str]:
    return sorted(glob.glob("logs/primary-*.log"))


def parse_logs(paths: List[str]) -> Dict[str, object]:
    rounds: Dict[int, Dict[str, object]] = {}
    fast_committed: Set[str] = set()
    slow_committed: Set[str] = set()

    for path in paths:
        if not os.path.exists(path):
            continue

        with open(path, "r", errors="replace") as f:
            for line_no, line in enumerate(f, start=1):
                match = FAST_PATH_RE.search(line)
                if match:
                    round_num = int(match.group("round"))
                    one = int(match.group("one"))
                    zero = int(match.group("zero"))
                    bivalent = int(match.group("bivalent"))
                    classified = one + zero + bivalent

                    candidate = {
                        "round": round_num,
                        "one": one,
                        "zero": zero,
                        "bivalent": bivalent,
                        "classified": classified,
                        "source": os.path.basename(path),
                        "line_no": line_no,
                    }

                    existing = rounds.get(round_num)
                    if existing is None:
                        rounds[round_num] = candidate
                    else:
                        existing_total = int(existing["classified"])
                        if classified > existing_total:
                            rounds[round_num] = candidate
                        elif classified == existing_total and line_no >= int(existing["line_no"]):
                            rounds[round_num] = candidate

                match = COMMITTED_RE.search(line)
                if match:
                    digest = match.group("digest")
                    if match.group("path") == "fast":
                        fast_committed.add(digest)
                    else:
                        slow_committed.add(digest)

    total_one = sum(int(record["one"]) for record in rounds.values())
    total_zero = sum(int(record["zero"]) for record in rounds.values())
    total_bivalent = sum(int(record["bivalent"]) for record in rounds.values())

    return {
        "logs_scanned": len(paths),
        "rounds": rounds,
        "total_one": total_one,
        "total_zero": total_zero,
        "total_bivalent": total_bivalent,
        "fast_committed": len(fast_committed),
        "slow_committed": len(slow_committed),
    }


def format_summary(stats: Dict[str, object]) -> str:
    rounds: Dict[int, Dict[str, object]] = stats["rounds"]  # type: ignore[assignment]

    lines = [
        "Valence Summary",
        "================",
        f"Primary logs scanned: {stats['logs_scanned']}",
        f"Rounds counted: {len(rounds)}",
        "",
        f"One-valent vertices: {stats['total_one']}",
        f"Zero-valent vertices: {stats['total_zero']}",
        f"Bivalent vertices: {stats['total_bivalent']}",
        "",
        f"Fast-path committed vertices: {stats['fast_committed']}",
        f"Slow-path committed vertices: {stats['slow_committed']}",
    ]

    if rounds:
        lines.extend(["", "Per-round classification:"])
        for round_num in sorted(rounds):
            record = rounds[round_num]
            lines.append(
                "  round {round}: one={one}, zero={zero}, bivalent={bivalent} "
                "(source={source}:{line_no})".format(**record)
            )

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract one/zero/bivalent counts from primary logs."
    )
    parser.add_argument(
        "logs",
        nargs="*",
        help="Primary log files to parse (default: logs/primary-*.log).",
    )
    parser.add_argument(
        "--out",
        help="Write the summary to a file instead of stdout.",
    )
    args = parser.parse_args()

    log_files = args.logs or default_logs()
    stats = parse_logs(log_files)
    output = format_summary(stats)

    if args.out:
        with open(args.out, "w") as f:
            f.write(output)
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
