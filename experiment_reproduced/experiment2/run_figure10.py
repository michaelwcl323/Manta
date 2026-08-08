#!/usr/bin/env python3
"""Laptop/AE entry: drive Experiment 2 (Figure 10) via the CloudLab controller only.

Replicas are never controlled directly from this machine; the controller SSHs to them.

Usage (from repo root, after CloudLab is up and deploy_environment Getting Started done):

  python experiment_reproduced/experiment2/run_figure10.py
  python experiment_reproduced/experiment2/run_figure10.py --only-suite figure10a_10b
  python experiment_reproduced/experiment2/run_figure10.py --only-suite figure10c
  python experiment_reproduced/experiment2/run_figure10.py --skip-prepare

Results land in experiment_reproduced/experiment2/results/{Figure10a_10b,Figure10c}/.
Plots are generated automatically into results/regenerate_graphs/ (use --skip-plot to disable).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXP_DIR = Path(__file__).resolve().parent
DEFAULT_SETTINGS = REPO_ROOT / "cloudlab_settings.json"
DEFAULT_BUILD = REPO_ROOT / "build"
DEFAULT_PLOT_DIR = REPO_ROOT / "results" / "regenerate_graphs"
PLOT_SCRIPTS_DIR = REPO_ROOT / "paper_data" / "graph_generated_code" / "experiment2"
FIGURE10C_ORDER = ["k2-c4", "k2-c7", "k3-c7", "k3-c10"]
FIGURE10C_LABELS = ["k2-ref4", "k2-ref7", "k3-ref7", "k3-ref10"]


def nested(settings: dict, *keys, default=None):
    value = settings
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def load_settings(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("cloudlab", data)


def default_key(settings: dict) -> Path:
    explicit = nested(settings, "key", "private") or nested(settings, "key", "path")
    if explicit:
        return Path(str(explicit)).expanduser()
    pubkey = nested(settings, "key", "pubkey")
    if pubkey and str(pubkey).endswith(".pub"):
        return Path(str(pubkey)[:-4]).expanduser()
    return Path("~/.ssh/id_rsa").expanduser()


def default_username(settings: dict) -> str:
    hosts = settings.get("hosts") or []
    if hosts and isinstance(hosts[0], dict) and hosts[0].get("username"):
        return str(hosts[0]["username"])
    return "ubuntu"


def read_controller(build_dir: Path) -> str:
    path = build_dir / "controller"
    if not path.exists():
        nodes = build_dir / "nodes"
        if not nodes.exists():
            raise SystemExit("missing build/controller (and build/nodes); run portal manifests / wait first")
        lines = [ln.strip() for ln in nodes.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if not lines:
            raise SystemExit("build/nodes is empty")
        return lines[-1]
    return path.read_text(encoding="utf-8").strip()


def ensure_ssh_agent(key: Path, password: str | None) -> None:
    sys.path.insert(0, str(REPO_ROOT))
    from cloudlab.deploy_environment import ensure_ssh_agent as _ensure

    _ensure(key, password)


def clear_local_results(local_results: Path, *, tag: str = "exp2-local") -> None:
    """Drop prior local campaign outputs so plots never mix old files."""
    local_results.mkdir(parents=True, exist_ok=True)
    for child in list(local_results.iterdir()):
        if child.name == ".gitkeep":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)
        print(f"[{tag}] cleared prior {child}", flush=True)
    (local_results / ".gitkeep").touch(exist_ok=True)


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print("[exp2-local]", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=check)


def phase_banner(idx: int, total: int, name: str) -> None:
    print(f"[exp2-local] ========== phase {idx}/{total}: {name} ==========", flush=True)


def scp_to_controller(
    local: Path,
    remote: str,
    username: str,
    controller: str,
    key: Path,
    connect_timeout: int,
    recursive: bool = False,
) -> None:
    cmd = [
        "scp",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        "-o",
        "IdentitiesOnly=yes",
        "-i",
        str(key),
    ]
    if recursive:
        cmd.append("-r")
    cmd.extend([str(local), f"{username}@{controller}:{remote}"])
    if run(cmd, check=False).returncode != 0:
        raise SystemExit(f"failed to scp {local} -> {username}@{controller}:{remote}")


def scp_from_controller(
    remote: str,
    local: Path,
    username: str,
    controller: str,
    key: Path,
    connect_timeout: int,
) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "scp",
        "-r",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        "-o",
        "IdentitiesOnly=yes",
        "-i",
        str(key),
        f"{username}@{controller}:{remote}",
        str(local),
    ]
    if run(cmd, check=False).returncode != 0:
        raise SystemExit(f"failed to scp {username}@{controller}:{remote} -> {local}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run Experiment 2 / Figure 10 via CloudLab controller")
    p.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    p.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD)
    p.add_argument("--connect-timeout", type=int, default=20)
    p.add_argument(
        "--only-suite",
        choices=["figure10a_10b", "figure10c"],
        default=None,
    )
    p.add_argument("--skip-prepare", action="store_true", help="Skip clone/build on replicas and controller")
    p.add_argument("--skip-plot", action="store_true", help="Do not call Figure 10 plot scripts after sync")
    p.add_argument(
        "--plot-output-dir",
        type=Path,
        default=DEFAULT_PLOT_DIR,
        help="Where plot scripts write PDFs/PNGs (default: results/regenerate_graphs)",
    )
    p.add_argument("--keep-remote-workdir", action="store_true")
    return p.parse_args()


def plot_figure10(local_results: Path, only_suite: str | None, output_dir: Path) -> int:
    """Call the paper Figure 10 plot scripts on freshly synced results.

    Returns the number of plot script failures (0 = all requested plots ok / skipped).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    do_ab = only_suite in (None, "figure10a_10b")
    do_c = only_suite in (None, "figure10c")
    failures = 0

    summary_csv = local_results / "Figure10a_10b" / "consensus_summary.csv"
    if do_ab:
        plot_a = PLOT_SCRIPTS_DIR / "plot_latency_by_kappa_sigma_reference.py"
        plot_b = PLOT_SCRIPTS_DIR / "plot_reference_impact_kappa2_by_sigma.py"
        if not summary_csv.is_file():
            print(f"[exp2-local] skip plot 10a/10b: missing {summary_csv}", flush=True)
        else:
            if plot_a.is_file():
                rc = run(
                    [
                        sys.executable,
                        str(plot_a),
                        "--summary-csv",
                        str(summary_csv),
                        "--output",
                        str(output_dir / "latency_by_kappa_sigma_reference.pdf"),
                        "--auto-limits",
                    ],
                    check=False,
                ).returncode
                if rc != 0:
                    failures += 1
                    print(
                        f"[exp2-local] plot 10a failed (rc={rc}); "
                        "usually means consensus_summary.csv is missing some (sigma,kappa,ref) cells",
                        flush=True,
                    )
            else:
                print(f"[exp2-local] skip plot 10a: missing {plot_a}", flush=True)
            if plot_b.is_file():
                rc = run(
                    [
                        sys.executable,
                        str(plot_b),
                        "--summary-csv",
                        str(summary_csv),
                        "--output",
                        str(output_dir / "reference_impact_kappa2_by_sigma.pdf"),
                        "--auto-limits",
                    ],
                    check=False,
                ).returncode
                if rc != 0:
                    failures += 1
                    print(
                        f"[exp2-local] plot 10b failed (rc={rc}); "
                        "usually means consensus_summary.csv is missing some (sigma,kappa=2,ref) cells",
                        flush=True,
                    )
            else:
                print(f"[exp2-local] skip plot 10b: missing {plot_b}", flush=True)

    if do_c:
        plot_c = PLOT_SCRIPTS_DIR / "plot_attack_latency_timeseries.py"
        fig10c = local_results / "Figure10c"
        run_dirs = []
        for label in FIGURE10C_LABELS:
            d = fig10c / label
            if d.is_dir() and (d / "latency.csv").is_file():
                run_dirs.append(d)
        if not plot_c.is_file():
            print(f"[exp2-local] skip plot 10c: missing {plot_c}", flush=True)
        elif len(run_dirs) != 4:
            print(
                f"[exp2-local] skip plot 10c: need 4 run dirs with latency.csv under {fig10c}, "
                f"found {len(run_dirs)} ({[p.name for p in run_dirs]})",
                flush=True,
            )
        else:
            rc = run(
                [
                    sys.executable,
                    str(plot_c),
                    "--merge-four-runs",
                    *[str(p) for p in run_dirs],
                    "--time-axis",
                    "commit",
                    "--rolling-stat",
                    "mean",
                    "--attack-start-secs",
                    "60",
                    "--attack-end-secs",
                    "120",
                    "--order",
                    ",".join(FIGURE10C_ORDER),
                    "--output",
                    str(output_dir / "attack_latency_timeseries_overlay_mean.pdf"),
                    "--auto-limits",
                ],
                check=False,
            ).returncode
            if rc != 0:
                failures += 1
                print(f"[exp2-local] plot 10c failed (rc={rc})", flush=True)

    return failures


def main() -> int:
    args = parse_args()
    settings = load_settings(args.settings)
    username = default_username(settings)
    key = default_key(settings)
    controller = read_controller(args.build_dir)
    repo_name = nested(settings, "repo", "name", default="manta-nsdi27")
    password = settings.get("ssh_key_password") or nested(settings, "key", "password")

    if not key.exists():
        raise SystemExit(f"SSH private key does not exist: {key}")

    ensure_ssh_agent(key, password)

    remote_key = f"/users/{username}/.ssh/manta_ae_functional"
    remote_workdir = f"/tmp/manta-exp2-{os.getpid()}"

    do_cleanup = not args.keep_remote_workdir
    do_plot = not args.skip_plot
    phase_names = [
        "prepare laptop SSH / upload package",
        "controller orchestrator (remote logs follow)",
        "sync plot/table inputs",
    ]
    if do_cleanup:
        phase_names.append("cleanup remote workdir")
    if do_plot:
        phase_names.append("plot / table")
    total_phases = len(phase_names)
    phase_i = 0

    def next_phase(name: str) -> None:
        nonlocal phase_i
        phase_i += 1
        phase_banner(phase_i, total_phases, name)

    print(f"[exp2-local] controller={controller}", flush=True)
    print(f"[exp2-local] username={username}", flush=True)
    print(f"[exp2-local] remote_workdir={remote_workdir}", flush=True)

    next_phase("prepare laptop SSH / upload package")
    prep = run(
        [
            "ssh",
            "-A",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ConnectTimeout={args.connect_timeout}",
            "-o",
            "IdentitiesOnly=yes",
            "-i",
            str(key),
            f"{username}@{controller}",
            f"mkdir -p ~/.ssh {remote_workdir} && chmod 700 ~/.ssh",
        ],
        check=False,
    )
    if prep.returncode != 0:
        raise SystemExit("failed to prepare controller workdir")

    scp_to_controller(key, remote_key, username, controller, key, args.connect_timeout)
    run(
        [
            "ssh",
            "-A",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-i",
            str(key),
            f"{username}@{controller}",
            f"chmod 600 {remote_key}",
        ]
    )

    for rel in [
        "matrix.yaml",
        "scripts/controller_orchestrator.py",
        "scripts/prepare_repo.sh",
        "wan/cloudlab_settings_geo.json",
    ]:
        local = EXP_DIR / rel
        remote = f"{remote_workdir}/{rel}"
        run(
            [
                "ssh",
                "-A",
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-i",
                str(key),
                f"{username}@{controller}",
                f"mkdir -p $(dirname {remote})",
            ]
        )
        scp_to_controller(local, remote, username, controller, key, args.connect_timeout)

    settings_blob = dict(settings)
    remote_settings = f"{remote_workdir}/cloudlab_settings.json"
    tmp_settings = EXP_DIR / "logs" / "_controller_settings.json"
    tmp_settings.parent.mkdir(parents=True, exist_ok=True)
    tmp_settings.write_text(json.dumps(settings_blob, indent=2) + "\n", encoding="utf-8")
    scp_to_controller(tmp_settings, remote_settings, username, controller, key, args.connect_timeout)

    orch_args = [
        "python3",
        f"{remote_workdir}/scripts/controller_orchestrator.py",
        "--workdir",
        remote_workdir,
        "--settings",
        remote_settings,
        "--matrix",
        f"{remote_workdir}/matrix.yaml",
        "--monorepo",
        f"$HOME/{repo_name}",
        "--remote-key",
        remote_key,
    ]
    if args.only_suite:
        orch_args.extend(["--only-suite", args.only_suite])
    if args.skip_prepare:
        orch_args.append("--skip-prepare")

    remote_cmd = (
        f"chmod +x {remote_workdir}/scripts/*.sh {remote_workdir}/scripts/*.py; "
        f"export PATH=\"$HOME/{repo_name}/venv/bin:$HOME/.cargo/bin:$PATH\"; "
        "python3 -m pip install -q pyyaml >/dev/null 2>&1 || true; "
        + " ".join(orch_args)
    )

    next_phase("controller orchestrator (remote logs follow)")
    print(
        "[exp2-local] CloudLab bench cells can take a long time; "
        "remote progress lines are tagged [exp2] progress ...",
        flush=True,
    )
    rc = run(
        [
            "ssh",
            "-A",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ConnectTimeout={args.connect_timeout}",
            "-o",
            "IdentitiesOnly=yes",
            "-i",
            str(key),
            f"{username}@{controller}",
            remote_cmd,
        ],
        check=False,
    ).returncode

    # Sync plot inputs only: consensus_summary.csv + Figure10c/*/latency.csv.
    next_phase("sync plot/table inputs")
    local_results = EXP_DIR / "results"
    clear_local_results(local_results)
    tmp_fetch = EXP_DIR / "logs" / f"_fetch_results_{os.getpid()}"
    if tmp_fetch.exists():
        shutil.rmtree(tmp_fetch)
    tmp_fetch.mkdir(parents=True, exist_ok=True)

    # 10a/10b: single CSV
    remote_csv = f"{remote_workdir}/results/Figure10a_10b/consensus_summary.csv"
    dest_ab = local_results / "Figure10a_10b"
    dest_ab.mkdir(parents=True, exist_ok=True)
    rc_ab = subprocess.run(
        [
            "scp",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            f"ConnectTimeout={args.connect_timeout}",
            "-i",
            str(key),
            f"{username}@{controller}:{remote_csv}",
            str(dest_ab / "consensus_summary.csv"),
        ],
        check=False,
    ).returncode
    if rc_ab == 0:
        print(f"[exp2-local] synced {dest_ab / 'consensus_summary.csv'}", flush=True)
    else:
        print("[exp2-local] skip sync Figure10a_10b CSV (not present or scp failed)", flush=True)

    # 10c: only latency.csv under each label dir
    dest_c = local_results / "Figure10c"
    if dest_c.exists():
        shutil.rmtree(dest_c)
    dest_c.mkdir(parents=True, exist_ok=True)
    for label in FIGURE10C_LABELS:
        remote_lat = f"{remote_workdir}/results/Figure10c/{label}/latency.csv"
        local_label = dest_c / label
        local_label.mkdir(parents=True, exist_ok=True)
        rc_c = subprocess.run(
            [
                "scp",
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                f"ConnectTimeout={args.connect_timeout}",
                "-i",
                str(key),
                f"{username}@{controller}:{remote_lat}",
                str(local_label / "latency.csv"),
            ],
            check=False,
        ).returncode
        if rc_c == 0:
            print(f"[exp2-local] synced {local_label / 'latency.csv'}", flush=True)
        else:
            print(f"[exp2-local] skip sync Figure10c/{label} (not present or scp failed)", flush=True)
            # Remove empty label dir if nothing landed.
            if not (local_label / "latency.csv").exists():
                shutil.rmtree(local_label, ignore_errors=True)
    shutil.rmtree(tmp_fetch, ignore_errors=True)

    if do_cleanup:
        next_phase("cleanup remote workdir")
        run(
            [
                "ssh",
                "-A",
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-i",
                str(key),
                f"{username}@{controller}",
                f"rm -rf {remote_workdir}",
            ],
            check=False,
        )
    else:
        print("[exp2-local] --keep-remote-workdir: cleanup skipped", flush=True)

    if rc != 0:
        raise SystemExit(f"controller orchestrator failed with code {rc}")

    print(f"[exp2-local] results synced to {local_results}", flush=True)
    if do_plot:
        next_phase("plot / table")
        plot_failures = plot_figure10(local_results, args.only_suite, args.plot_output_dir)
        if plot_failures:
            print(
                f"[exp2-local] {plot_failures} plot script(s) failed under {args.plot_output_dir}",
                flush=True,
            )
            return 1
        print(f"[exp2-local] plots written under {args.plot_output_dir}", flush=True)
    else:
        print("[exp2-local] --skip-plot: plot phase skipped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
