#!/usr/bin/env python3
"""
Extract key Narwhal log events and print them in time order.

Default behavior: scan benchmark/logs/*.log under the repository.
"""

from __future__ import annotations

import argparse
import csv
import sys
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


EVENT_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("start_batch", re.compile(r"Start to generate batch")),
    ("generate_batch", re.compile(r"Batch ([^ ]+) contains (\d+) B")),
    ("generate_header", re.compile(r"Created .* Digest number")),
    ("header_batch", re.compile(r"Created B\d+\([^ ]+\) -> ")),
    ("generate_certificate", re.compile(r"Assembled .* The header's id is")),
    ("send_certificate", re.compile(r"Received certificate from network: round")),
]

TIMESTAMP_PATTERN = re.compile(r"\[(\d{4}-\d{2}-\d{2}T[^\s\]]+Z)\s+.*?\]")


@dataclass(frozen=True)
class Entry:
    ts: Optional[float]
    seq: int
    source: str
    event: str
    line: str


def parse_timestamp(line: str) -> Optional[float]:
    match = TIMESTAMP_PATTERN.search(line)
    if not match:
        return None
    ts_raw = match.group(1)
    try:
        dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError:
        return None


def match_event(line: str) -> Optional[str]:
    for name, pattern in EVENT_PATTERNS:
        if pattern.search(line):
            return name
    return None


def iter_lines_from_paths(paths: Iterable[Path]) -> Iterable[Tuple[str, str]]:
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                yield str(path), line.rstrip("\n")


def extract_entries(lines: Iterable[Tuple[str, str]]) -> List[Entry]:
    entries: List[Entry] = []
    seq = 0
    for source, line in lines:
        event = match_event(line)
        if not event:
            continue
        ts = parse_timestamp(line)
        entries.append(Entry(ts=ts, seq=seq, source=source, event=event, line=line))
        seq += 1
    return entries


def sort_entries(entries: List[Entry]) -> List[Entry]:
    return sorted(
        entries,
        key=lambda e: (
            0 if e.ts is not None else 1,
            e.ts if e.ts is not None else 0.0,
            e.seq,
        ),
    )


def format_entry(entry: Entry, show_source: bool) -> str:
    prefix = f"[{entry.event}]"
    if show_source:
        prefix = f"{prefix} {entry.source}"
    return f"{prefix} {entry.line}"


def write_csv(entries: List[Entry], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "timestamp_iso",
                "timestamp_posix",
                "event",
                "source",
                "line",
            ]
        )
        for entry in entries:
            ts_iso = ""
            if entry.ts is not None:
                ts_iso = datetime.utcfromtimestamp(entry.ts).isoformat(timespec="milliseconds") + "Z"
            writer.writerow([ts_iso, entry.ts if entry.ts is not None else "", entry.event, entry.source, entry.line])


def default_log_paths() -> List[Path]:
    base_dir = Path(__file__).resolve().parent
    logs_dir = base_dir / "logs"
    return sorted(logs_dir.glob("*.log"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract key Narwhal logs in time order."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Log file paths. If omitted, use benchmark/logs/*.log.",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read logs from stdin instead of files.",
    )
    parser.add_argument(
        "--show-source",
        action="store_true",
        help="Prefix each line with its source file path.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write extracted events to a CSV file path.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.stdin:
        lines = (("<stdin>", line.rstrip("\n")) for line in sys.stdin)
    else:
        if args.paths:
            paths = [Path(path) for path in args.paths]
        else:
            paths = default_log_paths()
        if not paths:
            print("No log files found.", file=sys.stderr)
            return 2
        lines = iter_lines_from_paths(paths)

    entries = extract_entries(lines)
    entries = sort_entries(entries)
    if args.output:
        write_csv(entries, Path(args.output))
    else:
        default_output = Path(__file__).resolve().parent / "key_logs.csv"
        write_csv(entries, default_output)
        for entry in entries:
            print(format_entry(entry, args.show_source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
