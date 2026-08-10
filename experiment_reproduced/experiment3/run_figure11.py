#!/usr/bin/env python3
"""Laptop/AE entry: drive Experiment 3 (Figure 11a/11c) via the CloudLab controller only.

Replicas are never controlled directly from this machine; the controller SSHs to them.

Usage (from repo root, after CloudLab is up and deploy_environment Getting Started done):

  python experiment_reproduced/experiment3/run_figure11.py
  python experiment_reproduced/experiment3/run_figure11.py --only-experiment 11a
  python experiment_reproduced/experiment3/run_figure11.py --only-experiment 11c
  python experiment_reproduced/experiment3/run_figure11.py --only-suite figure11a
  python experiment_reproduced/experiment3/run_figure11.py --only-variant manta
  python experiment_reproduced/experiment3/run_figure11.py --skip-prepare

Results land in experiment_reproduced/experiment3/results/{Figure11a,Figure11c}/.
Plots are generated automatically into results/regenerate_graphs/ (use --skip-plot to disable).

Figure 11(b) is AWS-only and is not covered by this runner.
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
PLOT_SCRIPTS_DIR = REPO_ROOT / "paper_data" / "graph_generated_code" / "experiment3"

VARIANT_CHOICES = ("tusk", "dag-rider", "manta", "chitu", "mahi-mahi")
SUITE_CHOICES = ("figure11a", "figure11c")
EXPERIMENT_CHOICES = ("11a", "11c")
EXPERIMENT_TO_SUITE = {"11a": "figure11a", "11c": "figure11c"}


def resolve_only_suite(only_suite: str | None, only_experiment: str | None) -> str | None:
    """Map --only-experiment 11a/11c onto suite names; reject conflicting filters."""
    from_exp = EXPERIMENT_TO_SUITE[only_experiment] if only_experiment else None
    if only_suite and from_exp and only_suite != from_exp:
        raise SystemExit(
            f"conflicting filters: --only-suite {only_suite} vs --only-experiment {only_experiment} "
            f"(maps to {from_exp})"
        )
    return only_suite or from_exp


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


def clear_local_results(local_results: Path, *, tag: str = "exp3-local") -> None:
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
    print("[exp3-local]", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=check)


def phase_banner(idx: int, total: int, name: str) -> None:
    print(f"[exp3-local] ========== phase {idx}/{total}: {name} ==========", flush=True)


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
    p = argparse.ArgumentParser(description="Run Experiment 3 / Figure 11(a)+(c) via CloudLab controller")
    p.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    p.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD)
    p.add_argument("--connect-timeout", type=int, default=20)
    p.add_argument("--only-variant", choices=list(VARIANT_CHOICES), default=None)
    p.add_argument(
        "--only-suite",
        choices=list(SUITE_CHOICES),
        default=None,
        help="Run only figure11a or figure11c (alias of --only-experiment)",
    )
    p.add_argument(
        "--only-experiment",
        choices=list(EXPERIMENT_CHOICES),
        default=None,
        help="Run only 11a or 11c (maps to --only-suite figure11a / figure11c)",
    )
    p.add_argument("--skip-prepare", action="store_true", help="Skip clone/build on replicas and controller")
    p.add_argument("--skip-plot", action="store_true", help="Do not call Figure 11 plot scripts after sync")
    p.add_argument(
        "--plot-output-dir",
        type=Path,
        default=DEFAULT_PLOT_DIR,
        help="Where plot scripts write PDFs/PNGs (default: results/regenerate_graphs)",
    )
    p.add_argument("--keep-remote-workdir", action="store_true")
    return p.parse_args()


def _has_txt(data_root: Path) -> bool:
    if not data_root.is_dir():
        return False
    return any(data_root.rglob("*.txt"))


def plot_figure11(local_results: Path, only_suite: str | None, output_dir: Path) -> None:
    """Call the paper Figure 11(a)/(c) plot scripts on freshly synced results."""
    output_dir.mkdir(parents=True, exist_ok=True)
    suites = [only_suite] if only_suite else list(SUITE_CHOICES)

    for suite in suites:
        if suite == "figure11a":
            figure = "Figure11a"
            script = PLOT_SCRIPTS_DIR / "Figure11a" / "plot_mean_tps_latency.py"
        else:
            figure = "Figure11c"
            script = PLOT_SCRIPTS_DIR / "Figure11c" / "plot_mean_tps_latency.py"

        data_root = local_results / figure
        if not script.is_file():
            print(f"[exp3-local] skip plot {figure}: missing {script}", flush=True)
            continue
        if not _has_txt(data_root):
            print(f"[exp3-local] skip plot {figure}: no summary *.txt under {data_root}", flush=True)
            continue
        run(
            [
                sys.executable,
                str(script),
                "--data-root",
                str(data_root),
                "--output-dir",
                str(output_dir),
                "--auto-limits",
            ]
        )


def main() -> int:
    args = parse_args()
    args.only_suite = resolve_only_suite(args.only_suite, args.only_experiment)
    settings = load_settings(args.settings)
    username = default_username(settings)
    key = default_key(settings)
    controller = read_controller(args.build_dir)
    password = settings.get("ssh_key_password") or nested(settings, "key", "password")

    if not key.exists():
        raise SystemExit(f"SSH private key does not exist: {key}")

    ensure_ssh_agent(key, password)

    remote_key = f"/users/{username}/.ssh/manta_ae_functional"
    remote_workdir = f"/tmp/manta-exp3-{os.getpid()}"

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

    print(f"[exp3-local] controller={controller}", flush=True)
    print(f"[exp3-local] username={username}", flush=True)
    print(f"[exp3-local] remote_workdir={remote_workdir}", flush=True)

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
        "--remote-key",
        remote_key,
    ]
    if args.only_variant:
        orch_args.extend(["--only-variant", args.only_variant])
    if args.only_suite:
        orch_args.extend(["--only-suite", args.only_suite])
    if args.skip_prepare:
        orch_args.append("--skip-prepare")

    remote_cmd = (
        f"chmod +x {remote_workdir}/scripts/*.sh {remote_workdir}/scripts/*.py; "
        f"export PATH=\"$HOME/.cargo/bin:$PATH\"; "
        "python3 -m pip install -q pyyaml >/dev/null 2>&1 || true; "
        + " ".join(orch_args)
    )

    next_phase("controller orchestrator (remote logs follow)")
    print(
        "[exp3-local] CloudLab bench cells can take a long time; "
        "remote progress lines are tagged [exp3] progress ...",
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

    # Sync summary *.txt only under Figure11a / Figure11c (plot inputs).
    next_phase("sync plot/table inputs")
    local_results = EXP_DIR / "results"
    clear_local_results(local_results)
    tmp_fetch = EXP_DIR / "logs" / f"_fetch_results_{os.getpid()}"
    if tmp_fetch.exists():
        shutil.rmtree(tmp_fetch)
    tmp_fetch.mkdir(parents=True, exist_ok=True)
    for figure in ("Figure11a", "Figure11c"):
        remote_fig = f"{remote_workdir}/results/{figure}"
        local_fig_tmp = tmp_fetch / figure
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
            print(f"[exp3-local] skip sync {figure} (not present or scp failed)", flush=True)
            continue
        src = local_fig_tmp / figure if (local_fig_tmp / figure).is_dir() else local_fig_tmp
        dest = local_results / figure
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        if src.exists():
            for path in src.rglob("*.txt"):
                rel = path.relative_to(src)
                out = dest / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, out)
                print(f"[exp3-local] synced {out}", flush=True)
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
        print("[exp3-local] --keep-remote-workdir: cleanup skipped", flush=True)

    if rc != 0:
        raise SystemExit(f"controller orchestrator failed with code {rc}")

    print(f"[exp3-local] results synced to {local_results}", flush=True)
    if do_plot:
        next_phase("plot / table")
        plot_figure11(local_results, args.only_suite, args.plot_output_dir)
        print(f"[exp3-local] plots written under {args.plot_output_dir}", flush=True)
    else:
        print("[exp3-local] --skip-plot: plot phase skipped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
