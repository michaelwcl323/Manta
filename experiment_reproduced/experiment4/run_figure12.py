#!/usr/bin/env python3
"""Laptop/AE entry: drive Experiment 4 (Figure 12 + Table 2) via CloudLab controller only.

Replicas are never controlled directly from this machine; the controller SSHs to them.

Usage (from repo root, after CloudLab is up and deploy_environment Getting Started done):

  python experiment_reproduced/experiment4/run_figure12.py
  python experiment_reproduced/experiment4/run_figure12.py --only-suite figure12_complete
  python experiment_reproduced/experiment4/run_figure12.py --only-suite figure12_noflexible
  python experiment_reproduced/experiment4/run_figure12.py --skip-prepare

Results land in experiment_reproduced/experiment4/results/{Figure12,Table2}/.
Plots / Table 2 printout are generated automatically (use --skip-plot to disable).
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
PLOT_SCRIPTS_DIR = REPO_ROOT / "paper_data" / "graph_generated_code" / "experiment4"
SUITE_CHOICES = ["figure12_complete", "figure12_noflexible"]


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


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print("[exp4-local]", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=check)


def phase_banner(idx: int, total: int, name: str) -> None:
    print(f"[exp4-local] ========== phase {idx}/{total}: {name} ==========", flush=True)


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


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run Experiment 4 / Figure 12 + Table 2 via CloudLab controller")
    p.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    p.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD)
    p.add_argument("--connect-timeout", type=int, default=20)
    p.add_argument("--only-suite", choices=SUITE_CHOICES, default=None)
    p.add_argument("--skip-prepare", action="store_true", help="Skip clone/build on replicas and controller")
    p.add_argument("--skip-plot", action="store_true", help="Do not call Figure 12 plot / Table 2 print after sync")
    p.add_argument(
        "--plot-output-dir",
        type=Path,
        default=DEFAULT_PLOT_DIR,
        help="Where plot scripts write PDFs (default: results/regenerate_graphs)",
    )
    p.add_argument("--keep-remote-workdir", action="store_true")
    return p.parse_args()


def _scp_glob_dir(
    *,
    remote_dir: str,
    local_dir: Path,
    username: str,
    controller: str,
    key: Path,
    connect_timeout: int,
    pattern: str,
) -> int:
    """Fetch matching files from a remote directory into local_dir. Returns count synced."""
    local_dir.mkdir(parents=True, exist_ok=True)
    # Use remote ls to expand the glob; scp alone can't list selectively easily.
    list_cmd = f"bash -lc 'ls -1 {remote_dir}/{pattern} 2>/dev/null || true'"
    listed = subprocess.run(
        [
            "ssh",
            "-A",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            f"ConnectTimeout={connect_timeout}",
            "-i",
            str(key),
            f"{username}@{controller}",
            list_cmd,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    paths = [ln.strip() for ln in (listed.stdout or "").splitlines() if ln.strip()]
    synced = 0
    for remote_path in paths:
        name = Path(remote_path).name
        dest = local_dir / name
        rc = subprocess.run(
            [
                "scp",
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                f"ConnectTimeout={connect_timeout}",
                "-i",
                str(key),
                f"{username}@{controller}:{remote_path}",
                str(dest),
            ],
            check=False,
        ).returncode
        if rc == 0:
            print(f"[exp4-local] synced {dest}", flush=True)
            synced += 1
        else:
            print(f"[exp4-local] skip sync {remote_path}", flush=True)
    return synced


def sync_results(
    *,
    remote_workdir: str,
    local_results: Path,
    username: str,
    controller: str,
    key: Path,
    connect_timeout: int,
    only_suite: str | None,
) -> None:
    local_results.mkdir(parents=True, exist_ok=True)
    variants: list[str] = []
    if only_suite in (None, "figure12_complete"):
        variants.append("complete")
    if only_suite in (None, "figure12_noflexible"):
        variants.append("noflexible")

    for variant in variants:
        dest = local_results / "Figure12" / variant
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        remote = f"{remote_workdir}/results/Figure12/{variant}"
        n = _scp_glob_dir(
            remote_dir=remote,
            local_dir=dest,
            username=username,
            controller=controller,
            key=key,
            connect_timeout=connect_timeout,
            pattern="*.txt",
        )
        # Drop resource copies from Figure12 plot dirs after Table2 sync — keep only
        # summary *.txt that look like bench summaries for the plot script.
        # (resource summaries are also mirrored into Table2 aggregation CSVs.)
        if n == 0:
            print(f"[exp4-local] no Figure12/{variant} *.txt synced", flush=True)

    # Table2 CSVs / markdown
    table2 = local_results / "Table2"
    table2.mkdir(parents=True, exist_ok=True)
    for name in (
        "resource_summary.csv",
        "table2_mean_over_runs.csv",
        "table2.md",
    ):
        remote = f"{remote_workdir}/results/Table2/{name}"
        dest = table2 / name
        rc = subprocess.run(
            [
                "scp",
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                f"ConnectTimeout={connect_timeout}",
                "-i",
                str(key),
                f"{username}@{controller}:{remote}",
                str(dest),
            ],
            check=False,
        ).returncode
        if rc == 0:
            print(f"[exp4-local] synced {dest}", flush=True)
        else:
            print(f"[exp4-local] skip sync Table2/{name}", flush=True)
            if dest.exists() and dest.stat().st_size == 0:
                dest.unlink(missing_ok=True)


def plot_and_table(local_results: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    plot = PLOT_SCRIPTS_DIR / "plot_manta_consensus_tps_latency.py"
    complete_dir = local_results / "Figure12" / "complete"
    noflexible_dir = local_results / "Figure12" / "noflexible"
    if plot.is_file() and complete_dir.is_dir() and noflexible_dir.is_dir():
        run(
            [
                sys.executable,
                str(plot),
                "--complete-dir",
                str(complete_dir),
                "--noflexible-dir",
                str(noflexible_dir),
                "--output-dir",
                str(output_dir),
            ]
        )
    else:
        print(
            f"[exp4-local] skip plot: need {plot} and both "
            f"{complete_dir} / {noflexible_dir}",
            flush=True,
        )

    agg = PLOT_SCRIPTS_DIR / "aggregate_table2_resources.py"
    mean_csv = local_results / "Table2" / "table2_mean_over_runs.csv"
    if mean_csv.is_file():
        print(f"[exp4-local] Table 2 means ({mean_csv}):", flush=True)
        print(mean_csv.read_text(encoding="utf-8"), flush=True)
        md = local_results / "Table2" / "table2.md"
        if md.is_file():
            print(md.read_text(encoding="utf-8"), flush=True)
    elif agg.is_file():
        # Fallback: aggregate from any synced *.resource_usage_summary.txt
        run(
            [
                sys.executable,
                str(agg),
                "--input-dir",
                str(local_results / "Figure12"),
                "--output-dir",
                str(local_results / "Table2"),
            ]
        )
    else:
        print("[exp4-local] no Table2 CSV to print", flush=True)


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
    remote_workdir = f"/tmp/manta-exp4-{os.getpid()}"

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

    print(f"[exp4-local] controller={controller}", flush=True)
    print(f"[exp4-local] username={username}", flush=True)
    print(f"[exp4-local] remote_workdir={remote_workdir}", flush=True)

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
        "[exp4-local] CloudLab bench cells can take a long time; "
        "remote progress lines are tagged [exp4] progress ...",
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

    next_phase("sync plot/table inputs")
    local_results = EXP_DIR / "results"
    sync_results(
        remote_workdir=remote_workdir,
        local_results=local_results,
        username=username,
        controller=controller,
        key=key,
        connect_timeout=args.connect_timeout,
        only_suite=args.only_suite,
    )

    # Keep only bench summary *.txt under Figure12/{variant}/ for plotting.
    for variant in ("complete", "noflexible"):
        d = local_results / "Figure12" / variant
        if not d.is_dir():
            continue
        for path in d.glob("*.resource_usage_summary.txt"):
            path.unlink(missing_ok=True)

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
        print("[exp4-local] --keep-remote-workdir: cleanup skipped", flush=True)

    if rc != 0:
        raise SystemExit(f"controller orchestrator failed with code {rc}")

    print(f"[exp4-local] results synced to {local_results}", flush=True)
    if do_plot:
        next_phase("plot / table")
        plot_and_table(local_results, args.plot_output_dir)
        print(f"[exp4-local] plots/table written under {args.plot_output_dir} / {local_results / 'Table2'}", flush=True)
    else:
        print("[exp4-local] --skip-plot: plot/table phase skipped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
