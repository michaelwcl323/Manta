#!/usr/bin/env python3
"""
Generate annotated DAG artifacts for the current benchmark run.
"""

import argparse
import glob
import os

from benchmark.dag_vis import (
    build_annotated_dag_snapshot,
    export_dag_event_csv,
    export_dag_overview_html,
    export_dag_overview_json,
)
from benchmark.utils import PathMaker


def main() -> None:
    parser = argparse.ArgumentParser(description="Export annotated DAG artifacts.")
    parser.add_argument(
        "--final-dag",
        default=PathMaker.final_dag_file(),
        help="Path to final_dag.txt (default: current run final_dag.txt).",
    )
    parser.add_argument(
        "--logs",
        nargs="*",
        help="Primary logs to parse (default: current run logs/primary-*.log).",
    )
    parser.add_argument(
        "--events-out",
        default=PathMaker.dag_events_csv_file(),
        help="CSV output path for commit/leader events.",
    )
    parser.add_argument(
        "--json-out",
        default=PathMaker.dag_overview_json_file(),
        help="JSON output path for the annotated DAG snapshot.",
    )
    parser.add_argument(
        "--html-out",
        default=PathMaker.dag_overview_html_file(),
        help="HTML output path for the annotated DAG overview.",
    )
    args = parser.parse_args()

    log_files = args.logs or sorted(glob.glob(os.path.join(PathMaker.logs_path(), "primary-*.log")))
    snapshot = build_annotated_dag_snapshot(args.final_dag, log_files)
    if not snapshot["rounds"]:
        print("No annotated DAG data found.")
        return

    events_path = export_dag_event_csv(log_files, args.events_out)
    json_path = export_dag_overview_json(snapshot, args.json_out)
    html_path = export_dag_overview_html(snapshot, args.html_out)

    if events_path:
        print(f"Events CSV: {events_path}")
    if json_path:
        print(f"Overview JSON: {json_path}")
    if html_path:
        print(f"Overview HTML: {html_path}")


if __name__ == "__main__":
    main()
