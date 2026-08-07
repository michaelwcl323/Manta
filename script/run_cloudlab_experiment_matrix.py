#!/usr/bin/env python3
"""
Run the requested CloudLab experiment matrix.

Matrix:
1. network_tag in {no-delay, geo}
2. fixed kappa = 1
3. sigma = 2:
   - reference in [1, 3f+1]
   - coverage = reference
   - enable all boolean switches
4. sigma = 1:
   - reference in [1, 3f+1]
   - coverage = reference
   - disable all boolean switches

Network operations:
- no-delay: fab cloudlab-wan --action=clear
- geo:      fab cloudlab-wan --action=clear
            fab cloudlab-wan --settings-file=cloudlab_settings_6_3_1.json
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List


ROOT_DIR = Path(__file__).resolve().parent.parent
BENCHMARK_DIR = ROOT_DIR / "benchmark"


@dataclass(frozen=True)
class Experiment:
    network_tag: str
    sigma: int
    kappa: int
    reference: int
    coverage: int
    allow_cross_step_weak_edges: bool
    enable_fast_coin: bool
    solid_commit_trigger_on_solid_step: bool
    enable_commit_recheck: bool
    enable_adaptive_intermediate_spill: bool


def resolve_fab_launcher() -> List[str]:
    fab_path = shutil.which("fab")
    if fab_path:
        return [fab_path]

    pyenv_path = shutil.which("pyenv")
    if pyenv_path:
        try:
            prefix = subprocess.run(
                [pyenv_path, "prefix", "narwhal-bench"],
                cwd=BENCHMARK_DIR,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except subprocess.CalledProcessError:
            prefix = ""

        if prefix:
            candidate = Path(prefix) / "bin" / "fab"
            if candidate.exists():
                return [str(candidate)]

        return [pyenv_path, "exec", "fab"]

    return ["fab"]


def run_command(cmd: List[str], dry_run: bool) -> None:
    print(f"$ {shlex.join(cmd)}")
    if dry_run:
        return

    result = subprocess.run(cmd, cwd=BENCHMARK_DIR, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}: {shlex.join(cmd)}"
        )


def ensure_geo_settings_file(settings_file: str) -> None:
    settings_path = Path(settings_file)
    if not settings_path.is_absolute():
        settings_path = BENCHMARK_DIR / settings_file

    if not settings_path.exists():
        raise FileNotFoundError(
            f"Geo network settings file not found: {settings_path}"
        )


def apply_network(network_tag: str, geo_settings_file: str, dry_run: bool) -> None:
    print(f"\n=== Configure network: {network_tag} ===")
    fab = resolve_fab_launcher()

    # Always clear first so tc state from the previous run does not leak.
    run_command([*fab, "cloudlab-wan", "--action=clear"], dry_run=dry_run)

    if network_tag == "geo":
        ensure_geo_settings_file(geo_settings_file)
        run_command(
            [
                *fab,
                "cloudlab-wan",
                f"--settings-file={geo_settings_file}",
            ],
            dry_run=dry_run,
        )
    elif network_tag != "no-delay":
        raise ValueError(f"Unsupported network_tag: {network_tag}")


def build_experiments(committee_size: int) -> List[Experiment]:
    if committee_size < 4:
        raise ValueError("committee_size must be at least 4")

    f_value = (committee_size - 1) // 3
    refs = list(range(1, 3 * f_value + 2))

    experiments: List[Experiment] = []
    for network_tag in ("no-delay", "geo"):
        for reference in refs:
            experiments.append(
                Experiment(
                    network_tag=network_tag,
                    sigma=2,
                    kappa=1,
                    reference=reference,
                    coverage=reference,
                    allow_cross_step_weak_edges=True,
                    enable_fast_coin=True,
                    solid_commit_trigger_on_solid_step=True,
                    enable_commit_recheck=False,
                    enable_adaptive_intermediate_spill=True,
                )
            )
        for reference in refs:
            experiments.append(
                Experiment(
                    network_tag=network_tag,
                    sigma=1,
                    kappa=1,
                    reference=reference,
                    coverage=reference,
                    allow_cross_step_weak_edges=False,
                    enable_fast_coin=False,
                    solid_commit_trigger_on_solid_step=False,
                    enable_commit_recheck=False,
                    enable_adaptive_intermediate_spill=False,
                )
            )
    return experiments


def experiment_to_cmd(
    exp: Experiment,
    design_tag: str,
    load_tag: str,
) -> List[str]:
    fab = resolve_fab_launcher()
    boolean_flags = [
        (
            "allow-cross-step-weak-edges",
            exp.allow_cross_step_weak_edges,
        ),
        ("enable-fast-coin", exp.enable_fast_coin),
        (
            "solid-commit-trigger-on-solid-step",
            exp.solid_commit_trigger_on_solid_step,
        ),
        ("enable-commit-recheck", exp.enable_commit_recheck),
        (
            "enable-adaptive-intermediate-spill",
            exp.enable_adaptive_intermediate_spill,
        ),
    ]
    return [
        *fab,
        "cloudlab-remote",
        f"--sigma={exp.sigma}",
        f"--kappa={exp.kappa}",
        f"--reference={exp.reference}",
        f"--coverage={exp.coverage}",
        *[
            f"--{flag_name}" if enabled else f"--no-{flag_name}"
            for flag_name, enabled in boolean_flags
        ],
        f"--design-tag={design_tag}",
        f"--network-tag={exp.network_tag}",
        f"--load-tag={load_tag}",
    ]


def print_plan(experiments: Iterable[Experiment], committee_size: int) -> None:
    f_value = (committee_size - 1) // 3
    print("Planned experiment matrix")
    print("=" * 80)
    print(f"committee_size = {committee_size}")
    print(f"f = floor((n - 1) / 3) = {f_value}")
    print(f"reference range = [1, {3 * f_value + 1}]")
    print()
    for index, exp in enumerate(experiments, start=1):
        print(
            f"{index:02d}. "
            f"network={exp.network_tag}, sigma={exp.sigma}, kappa={exp.kappa}, "
            f"ref={exp.reference}, cov={exp.coverage}, "
            f"fast_coin={exp.enable_fast_coin}, "
            f"solid_step_trigger={exp.solid_commit_trigger_on_solid_step}"
        )
    print("=" * 80)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the requested CloudLab experiment matrix."
    )
    parser.add_argument(
        "--committee-size",
        type=int,
        default=10,
        help="Committee size used to derive f and 3f+1. Default: 10",
    )
    parser.add_argument(
        "--design-tag",
        default="experiment2_forpaper",
        help="Value passed to fab cloudlab-remote --design-tag",
    )
    parser.add_argument(
        "--load-tag",
        default="balanced_50_50",
        help="Value passed to fab cloudlab-remote --load-tag",
    )
    parser.add_argument(
        "--geo-settings-file",
        default="cloudlab_settings_6_3_1.json",
        help="CloudLab WAN settings file used for geo runs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing them",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Run without interactive confirmation",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    experiments = build_experiments(args.committee_size)
    print_plan(experiments, args.committee_size)

    if not args.yes:
        answer = input("Proceed with these experiments? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Aborted.")
            return 1

    current_network = None
    total = len(experiments)

    for index, exp in enumerate(experiments, start=1):
        print(f"\n### Experiment {index}/{total} ###")
        if exp.network_tag != current_network:
            apply_network(
                exp.network_tag,
                geo_settings_file=args.geo_settings_file,
                dry_run=args.dry_run,
            )
            current_network = exp.network_tag

        run_command(
            experiment_to_cmd(
                exp,
                design_tag=args.design_tag,
                load_tag=args.load_tag,
            ),
            dry_run=args.dry_run,
        )

    print("\nAll requested experiments completed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)
