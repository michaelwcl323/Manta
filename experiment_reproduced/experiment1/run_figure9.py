#!/usr/bin/env python3
"""Laptop/AE entry: drive Experiment 1 (Figure 9) via the CloudLab controller only.

Replicas are never controlled directly from this machine; the controller SSHs to them.

Usage (from repo root, after CloudLab is up and deploy_environment Getting Started done):

  python experiment_reproduced/experiment1/run_figure9.py
  python experiment_reproduced/experiment1/run_figure9.py --only-variant decoupled
  python experiment_reproduced/experiment1/run_figure9.py --only-network geo --only-workload balanced
  python experiment_reproduced/experiment1/run_figure9.py --skip-prepare

Results land in experiment_reproduced/experiment1/results/{Figure9a,Figure9b}/.
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
PLOT_SCRIPTS = {
    "decoupled": (
        "Figure9a",
        REPO_ROOT
        / "paper_data"
        / "graph_generated_code"
        / "experiment1"
        / "plot_workload_grouped_network_metrics_decouple_100k_consensus.py",
    ),
    "coupled": (
        "Figure9b",
        REPO_ROOT
        / "paper_data"
        / "graph_generated_code"
        / "experiment1"
        / "plot_workload_grouped_network_metrics_tusk_coupled_60k_consensus.py",
    ),
}


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
    # Reuse deploy helper when available.
    sys.path.insert(0, str(REPO_ROOT))
    from cloudlab.deploy_environment import ensure_ssh_agent as _ensure

    _ensure(key, password)


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print("[exp1-local]", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=check)


def phase_banner(idx: int, total: int, name: str) -> None:
    print(f"[exp1-local] ========== phase {idx}/{total}: {name} ==========", flush=True)


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
    p = argparse.ArgumentParser(description="Run Experiment 1 / Figure 9 via CloudLab controller")
    p.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    p.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD)
    p.add_argument("--connect-timeout", type=int, default=20)
    p.add_argument("--only-variant", choices=["coupled", "decoupled"], default=None)
    p.add_argument("--only-network", choices=["80ms", "geo"], default=None)
    p.add_argument("--only-workload", choices=["balanced", "custom-high-3", "custom-high-5"], default=None)
    p.add_argument("--skip-prepare", action="store_true", help="Skip rematerializing flat trees on replicas")
    p.add_argument("--skip-plot", action="store_true", help="Do not call Figure 9 plot scripts after sync")
    p.add_argument(
        "--plot-output-dir",
        type=Path,
        default=DEFAULT_PLOT_DIR,
        help="Where plot scripts write PDFs/PNGs (default: results/regenerate_graphs)",
    )
    p.add_argument("--keep-remote-workdir", action="store_true")
    return p.parse_args()


def plot_figure9(local_results: Path, only_variant: str | None, output_dir: Path) -> None:
    """Call the paper Figure 9 plot scripts on freshly synced summaries."""
    output_dir.mkdir(parents=True, exist_ok=True)
    variants = [only_variant] if only_variant else list(PLOT_SCRIPTS)
    for variant in variants:
        figure, script = PLOT_SCRIPTS[variant]
        data_root = local_results / figure
        if not script.is_file():
            print(f"[exp1-local] skip plot {figure}: missing {script}", flush=True)
            continue
        if not data_root.is_dir() or not any(data_root.glob("*.txt")):
            print(f"[exp1-local] skip plot {figure}: no summary *.txt under {data_root}", flush=True)
            continue
        run(
            [
                sys.executable,
                str(script),
                "--data-root",
                str(data_root),
                "--output-dir",
                str(output_dir),
            ]
        )


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
    remote_workdir = f"/tmp/manta-exp1-{os.getpid()}"

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

    print(f"[exp1-local] controller={controller}", flush=True)
    print(f"[exp1-local] username={username}", flush=True)
    print(f"[exp1-local] remote_workdir={remote_workdir}", flush=True)

    next_phase("prepare laptop SSH / upload package")
    # Ensure controller key + workdir.
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

    # Upload scripts + matrix + wan profiles.
    for rel in [
        "matrix.yaml",
        "scripts/controller_orchestrator.py",
        "scripts/prepare_flat_tree.sh",
        "wan/cloudlab_settings_80ms.json",
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

    # Upload settings for orchestrator (with password if present).
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
    if args.only_variant:
        orch_args.extend(["--only-variant", args.only_variant])
    if args.only_network:
        orch_args.extend(["--only-network", args.only_network])
    if args.only_workload:
        orch_args.extend(["--only-workload", args.only_workload])
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
        "[exp1-local] CloudLab bench cells can take a long time; "
        "remote progress lines are tagged [exp1] progress ...",
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

    # Sync summary *.txt only (Figure9a / Figure9b). Skip raw dumps.
    next_phase("sync plot/table inputs")
    local_results = EXP_DIR / "results"
    local_results.mkdir(parents=True, exist_ok=True)
    stale_raw = local_results / "raw"
    if stale_raw.exists():
        shutil.rmtree(stale_raw)
    tmp_fetch = EXP_DIR / "logs" / f"_fetch_results_{os.getpid()}"
    if tmp_fetch.exists():
        shutil.rmtree(tmp_fetch)
    tmp_fetch.mkdir(parents=True, exist_ok=True)
    for figure in ("Figure9a", "Figure9b"):
        remote_fig = f"{remote_workdir}/results/{figure}"
        local_fig_tmp = tmp_fetch / figure
        # Ignore missing figure dirs (e.g. --only-variant ran one suite).
        rc_scp = subprocess.run(
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
                "-r",
                f"{username}@{controller}:{remote_fig}",
                str(local_fig_tmp),
            ],
            check=False,
        ).returncode
        if rc_scp != 0:
            print(f"[exp1-local] skip sync {figure} (not present or scp failed)", flush=True)
            continue
        # scp -r may nest as local_fig_tmp/Figure9a
        src = local_fig_tmp / figure if (local_fig_tmp / figure).is_dir() else local_fig_tmp
        dest = local_results / figure
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        if src.exists():
            for path in src.rglob("*.txt"):
                shutil.copy2(path, dest / path.name)
                print(f"[exp1-local] synced {dest / path.name}", flush=True)
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
        print("[exp1-local] --keep-remote-workdir: cleanup skipped", flush=True)

    if rc != 0:
        raise SystemExit(f"controller orchestrator failed with code {rc}")

    print(f"[exp1-local] results synced to {local_results}", flush=True)
    if do_plot:
        next_phase("plot / table")
        plot_figure9(local_results, args.only_variant, args.plot_output_dir)
        print(f"[exp1-local] plots written under {args.plot_output_dir}", flush=True)
    else:
        print("[exp1-local] --skip-plot: plot phase skipped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
