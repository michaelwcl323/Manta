#!/usr/bin/env python3
"""Sweep sigma × reference × network (geo/lan) on CloudLab.

Fixed for this sweep:
  spill = off, boundary = on, runs = 3 (override with flags)

Default matrix (18 cells × 2 networks = 36 fab invocations):
  sigma = 1..6
  kappa = 2, reference = 2 4 7, coverage = 7
  network ∈ {geo, lan}

Before each network group, runs:
  fab cloudlab-network --mode=<geo|lan>

Example:
  python run_sigma_network_sweep.py
  python run_sigma_network_sweep.py --network geo --runs 3 --dry-run
  python run_sigma_network_sweep.py --sigma 1 2 --reference 2 --network lan geo
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
    reference: int
    network: str

    def load_tag(self, kappa: int, coverage: int) -> str:
        # spill fixed off, boundary fixed on for this sweep
        return (
            f"s{self.sigma}_k{kappa}_r{self.reference}_c{coverage}"
            f"_spill_off_bound_on"
        )

    def label(self, kappa: int, coverage: int) -> str:
        return (
            f"network={self.network} sigma={self.sigma} "
            f"reference={self.reference} "
            f"load_tag={self.load_tag(kappa, coverage)}"
        )


def parse_network(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "geo": "geo",
        "wan": "geo",
        "heterogeneous": "geo",
        "n2": "geo",
        "lan": "lan",
        "homogeneous": "lan",
        "80": "lan",
    }
    if normalized not in aliases:
        raise argparse.ArgumentTypeError(
            f"expected geo/lan (got {value!r})"
        )
    return aliases[normalized]


def build_matrix(
    sigmas: list[int],
    references: list[int],
    networks: list[str],
) -> list[Cell]:
    # Network outermost so we switch tc/netem once per group.
    return [
        Cell(sigma=sigma, reference=reference, network=network)
        for network, sigma, reference in itertools.product(
            networks, sigmas, references
        )
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sweep sigma × reference × network with spill=off, boundary=on."
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
    parser.add_argument(
        "--reference",
        type=int,
        nargs="+",
        default=[2, 4, 7],
        help="Reference value(s) to sweep (default: 2 4 7).",
    )
    parser.add_argument("--coverage", type=int, default=7)
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Runs per cell (default: 3).",
    )
    parser.add_argument(
        "--network",
        type=parse_network,
        nargs="+",
        default=["geo", "lan"],
        help="Network modes to sweep (default: geo lan).",
    )
    parser.add_argument(
        "--design-tag",
        default="sigma_spill_boundary",
        help="design_tag written into result paths.",
    )
    parser.add_argument(
        "--skip-network-setup",
        action="store_true",
        help="Do not call fab cloudlab-network before each network group.",
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


def build_remote_cmd(cell: Cell, args: argparse.Namespace) -> list[str]:
    # Keep in sync with fabfile.cloudlab_remote defaults for spill/boundary.
    cmd = [
        "fab",
        "cloudlab-remote",
        f"--sigma={cell.sigma}",
        f"--kappa={args.kappa}",
        f"--reference={cell.reference}",
        f"--coverage={args.coverage}",
        f"--runs={args.runs}",
        f"--design-tag={args.design_tag}",
        f"--network-tag={cell.network}",
        f"--load-tag={cell.load_tag(args.kappa, args.coverage)}",
    ]
    # spill default True in fabfile → need --no-...
    append_invoke_bool(
        cmd,
        "enable-adaptive-intermediate-spill",
        False,
        default=True,
    )
    # boundary default False in fabfile → need --...
    append_invoke_bool(
        cmd,
        "enable-intermediate-wave-boundary",
        True,
        default=False,
    )
    return cmd


def build_network_cmd(network: str) -> list[str]:
    return ["fab", "cloudlab-network", f"--mode={network}"]


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
        encoding="utf-8",
        errors="replace",
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


def setup_network(
    network: str, workdir: Path, dry_run: bool, skip: bool
) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cmd = build_network_cmd(network)
    print()
    print(f"=== Network setup → {network} @ {now} ===")
    print("Running:", " ".join(cmd))
    print("-" * 72)
    if skip:
        print("(skip-network-setup) leaving current CloudLab WAN profile as-is")
        print("-" * 72)
        return 0
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
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    saw_internal_error = False
    for line in process.stdout:
        print(line, end="")
        plain = ANSI_ESCAPE_RE.sub("", line)
        if "ERROR:" in plain:
            saw_internal_error = True
    process.wait()
    rc = process.returncode
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

    # Preserve user order but drop duplicates.
    networks: list[str] = []
    for n in args.network:
        if n not in networks:
            networks.append(n)

    cells = build_matrix(args.sigma, args.reference, networks)
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
    print("spill=off boundary=on")
    print(f"design_tag={args.design_tag} network={networks}")
    print(f"cells={total}")
    for idx, cell in enumerate(cells, start=1):
        mark = " " if idx >= args.start_from else "skip"
        print(f"{idx:02d}. [{mark}] {cell.label(args.kappa, args.coverage)}")
    print("=" * 80)

    failed = 0
    ran = 0
    active_network: str | None = None
    for idx, cell in enumerate(cells, start=1):
        if idx < args.start_from:
            continue

        if cell.network != active_network:
            rc_net = setup_network(
                cell.network,
                workdir,
                args.dry_run,
                args.skip_network_setup,
            )
            if rc_net != 0:
                failed += 1
                print(
                    f"Network setup for {cell.network} failed "
                    f"(exit {rc_net})."
                )
                if not args.continue_on_error:
                    print(
                        "Stopping due to failure "
                        "(use --continue-on-error to keep going)."
                    )
                    return rc_net
            active_network = cell.network

        ran += 1
        cmd = build_remote_cmd(cell, args)
        rc = run_one(cmd, idx, total, workdir, args.dry_run)
        if rc != 0:
            failed += 1
            if not args.continue_on_error:
                print(
                    "Stopping due to failure "
                    "(use --continue-on-error to keep going)."
                )
                return rc

    print()
    if failed == 0:
        print(f"All {ran} fab cloudlab-remote runs completed successfully.")
        return 0
    print(f"Completed with failures: {failed}/{ran} commands failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
