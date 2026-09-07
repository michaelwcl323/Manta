#!/usr/bin/env python3
"""Sweep sigma × adaptive spill × intermediate solid-step boundary on CloudLab.

Matrix (24 cells):
  sigma = 1..6
  kappa = 2, reference = 2, coverage = 7, runs = 1
  enable_adaptive_intermediate_spill ∈ {True, False}
  enable_intermediate_wave_boundary ∈ {True, False}
  (flag name kept; semantics are solid-step / next-critical cleanup)
"""

from __future__ import annotations

import argparse
import itertools
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class Cell:
    sigma: int
    spill: bool
    boundary: bool

    def load_tag(self, kappa: int, reference: int, coverage: int) -> str:
        spill = "spill_on" if self.spill else "spill_off"
        bound = "bound_on" if self.boundary else "bound_off"
        return f"s{self.sigma}_k{kappa}_r{reference}_c{coverage}_{spill}_{bound}"

    def label(self, kappa: int, reference: int, coverage: int) -> str:
        return (
            f"sigma={self.sigma} spill={self.spill} "
            f"boundary={self.boundary} "
            f"load_tag={self.load_tag(kappa, reference, coverage)}"
        )


def build_matrix(sigmas: list[int]) -> list[Cell]:
    return [
        Cell(sigma=sigma, spill=spill, boundary=boundary)
        for sigma, spill, boundary in itertools.product(
            sigmas, (True, False), (True, False)
        )
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run fab cloudlab-remote for sigma=1..6 × "
            "enable_adaptive_intermediate_spill × enable_intermediate_wave_boundary."
        )
    )
    parser.add_argument(
        "--sigma",
        type=int,
        nargs="+",
        default=list(range(1, 7)),
        help="Sigma values to sweep (default: 1 2 3 4 5 6).",
    )
    parser.add_argument("--kappa", type=int, default=2)
    parser.add_argument("--reference", type=int, default=2)
    parser.add_argument("--coverage", type=int, default=7)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument(
        "--design-tag",
        default="sigma_spill_boundary",
        help="design_tag written into result paths (default: sigma_spill_boundary).",
    )
    parser.add_argument(
        "--network-tag",
        default="geo",
        help="network_tag for result paths (default: geo).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned commands without running them.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue with the next cell even if one run fails.",
    )
    parser.add_argument(
        "--start-from",
        type=int,
        default=1,
        help="1-based index of the first cell to run (default: 1).",
    )
    return parser.parse_args()


def append_invoke_bool(
    cmd: list[str], name: str, enabled: bool, *, default: bool
) -> None:
    """Invoke bool flags: default=True → --no-name; default=False → --name only."""
    if enabled == default:
        return
    if enabled:
        cmd.append(f"--{name}")
    else:
        cmd.append(f"--no-{name}")


def build_cmd(cell: Cell, args: argparse.Namespace) -> list[str]:
    # Keep in sync with fabfile.cloudlab_remote defaults for these two flags.
    cmd = [
        "fab",
        "cloudlab-remote",
        f"--sigma={cell.sigma}",
        f"--kappa={args.kappa}",
        f"--reference={args.reference}",
        f"--coverage={args.coverage}",
        f"--runs={args.runs}",
        f"--design-tag={args.design_tag}",
        f"--network-tag={args.network_tag}",
        f"--load-tag={cell.load_tag(args.kappa, args.reference, args.coverage)}",
    ]
    append_invoke_bool(
        cmd,
        "enable-adaptive-intermediate-spill",
        cell.spill,
        default=True,
    )
    append_invoke_bool(
        cmd,
        "enable-intermediate-wave-boundary",
        cell.boundary,
        default=False,
    )
    return cmd


def run_one(cmd: list[str], step: int, total: int, workdir: Path, dry_run: bool) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print()
    print(f"[{step}/{total}] {now}")
    print("Running:", " ".join(cmd))
    print("-" * 72)
    if dry_run:
        print("(dry-run) skipped")
        print("-" * 72)
        return 0

    process = subprocess.Popen(
        cmd,
        cwd=str(workdir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None

    saw_internal_error = False
    saw_success = False
    for line in process.stdout:
        print(line, end="")
        plain = ANSI_ESCAPE_RE.sub("", line)
        if "ERROR:" in plain:
            saw_internal_error = True
        if "All benchmarks completed" in plain:
            saw_success = True

    process.wait()
    rc = process.returncode
    # fab/paramiko occasionally dies with SIGSEGV (-11) after a successful run.
    if rc and rc < 0 and saw_success and not saw_internal_error:
        print(
            f"WARN: fab exited with signal {-rc} after successful completion; "
            "treating as success."
        )
        rc = 0
    if rc == 0 and saw_internal_error:
        rc = 1

    print("-" * 72)
    print(f"Exit code: {rc}")
    return rc


def main() -> int:
    args = parse_args()
    if args.runs <= 0:
        print("Error: --runs must be > 0", file=sys.stderr)
        return 2
    if args.start_from <= 0:
        print("Error: --start-from must be >= 1", file=sys.stderr)
        return 2

    cells = build_matrix(args.sigma)
    total = len(cells)
    if args.start_from > total:
        print(
            f"Error: --start-from={args.start_from} exceeds matrix size {total}",
            file=sys.stderr,
        )
        return 2

    workdir = Path(__file__).resolve().parent
    print("Planned matrix")
    print("=" * 80)
    print(
        f"sigma={args.sigma} kappa={args.kappa} reference={args.reference} "
        f"coverage={args.coverage} runs={args.runs}"
    )
    print(f"design_tag={args.design_tag} network_tag={args.network_tag}")
    print(f"cells={total} (spill × boundary × sigma)")
    for idx, cell in enumerate(cells, start=1):
        mark = " " if idx >= args.start_from else "skip"
        print(f"{idx:02d}. [{mark}] {cell.label(args.kappa, args.reference, args.coverage)}")
    print("=" * 80)

    failed = 0
    ran = 0
    for idx, cell in enumerate(cells, start=1):
        if idx < args.start_from:
            continue
        ran += 1
        cmd = build_cmd(cell, args)
        rc = run_one(cmd, idx, total, workdir, args.dry_run)
        if rc != 0:
            failed += 1
            if not args.continue_on_error:
                print("Stopping due to failure (use --continue-on-error to keep going).")
                return rc

    print()
    if failed == 0:
        print(f"All {ran} fab cloudlab-remote runs completed successfully.")
        return 0
    print(f"Completed with failures: {failed}/{ran} commands failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
