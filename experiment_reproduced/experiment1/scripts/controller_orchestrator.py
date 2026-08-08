#!/usr/bin/env python3
"""Controller-side Experiment 1 / Figure 9 orchestrator.

Runs on the CloudLab controller. Replicas are driven only via SSH/Fabric from here.

Scenario switching:
  * variant  -> flat repo $HOME/manta-exp1-{coupled,decoupled}
  * network  -> cloudlab_wan clear + setup (re-applied whenever delay profile changes)
  * workload -> CloudLabBench bench_params only

Prepare runs in parallel across all replicas and must finish before any WAN/delay
setup or benchmark cells.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace


def progress(tag: str, done: int, total: int, msg: str) -> None:
    pct = 0 if total <= 0 else int(100 * done / total)
    print(f"[{tag}] progress {done}/{total} ({pct}%) — {msg}", flush=True)


def load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore
    except ImportError:
        raise SystemExit("PyYAML is required on the controller (pip install pyyaml)")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class ConnectKwargs(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, key):
        try:
            del self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print("[exp1]", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check)


def _ssh_base(identity: str | None, connect_timeout: int) -> list[str]:
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={connect_timeout}",
    ]
    if identity:
        cmd.extend(["-o", "IdentitiesOnly=yes", "-i", identity])
    return cmd


def _scp_base(identity: str | None) -> list[str]:
    cmd = [
        "scp",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if identity:
        cmd.extend(["-o", "IdentitiesOnly=yes", "-i", identity])
    return cmd


def ssh_host(
    username: str,
    host: str,
    remote_cmd: str,
    *,
    identity: str | None = None,
    connect_timeout: int = 20,
    check: bool = True,
) -> subprocess.CompletedProcess:
    return run(
        _ssh_base(identity, connect_timeout) + [f"{username}@{host}", remote_cmd],
        check=check,
    )


def scp_to_host(
    local: Path,
    username: str,
    host: str,
    remote: str,
    *,
    identity: str | None = None,
) -> None:
    run(_scp_base(identity) + [str(local), f"{username}@{host}:{remote}"])


def merge_wan_settings(base_hosts: list[dict], wan_profile: dict, remote_key: str, port: int, flat_repo: str, branch: str, repo_url: str) -> dict:
    """Merge paper WAN profile onto replica hosts only.

    Controller must never appear in hosts/peers so controller↔replica delay stays 0
    (CloudLabWan also excludes TCP/22 from netem).
    """
    wan = dict(wan_profile.get("cloudlab_wan") or wan_profile)
    sites = wan.pop("site_by_host_index", None)
    if sites is not None and len(sites) != 10:
        raise SystemExit(f"site_by_host_index must have 10 entries, got {len(sites)}")

    by_octet: dict[int, dict] = {}
    for h in base_hosts:
        ip = str(h.get("hostname", ""))
        if not ip.startswith("10.10.1."):
            continue
        try:
            last = int(ip.rsplit(".", 1)[-1])
        except ValueError:
            continue
        if last < 1 or last > 10:
            continue
        entry = dict(h)
        if sites is not None:
            entry["wan_site"] = sites[last - 1]
        by_octet[last] = entry

    hosts = [by_octet[i] for i in range(1, 11) if i in by_octet]
    if len(hosts) != 10:
        raise SystemExit(
            f"expected 10 replica hosts for WAN, got {len(hosts)}: {[h['hostname'] for h in hosts]}"
        )
    return {
        "key": {"path": remote_key},
        "port": port,
        "repo": {"name": flat_repo, "url": repo_url, "branch": branch},
        "hosts": hosts,
        "cloudlab_wan": {
            "rtt_ms": wan.get("rtt_ms", {}),
            "hosts": hosts,
        },
    }


def ensure_monorepo(repo_dir: Path, repo_url: str, branch: str) -> None:
    if not (repo_dir / ".git").exists():
        run(["git", "clone", repo_url, str(repo_dir)])
    run(["git", "fetch", "--tags", "origin", branch], cwd=repo_dir, check=False)
    run(["git", "checkout", branch], cwd=repo_dir)
    run(["git", "pull", "--ff-only", "origin", branch], cwd=repo_dir, check=False)


def _prepare_one_host(
    *,
    host: str,
    user: str,
    identity: str,
    repo_url: str,
    branch: str,
    monorepo_name: str,
    variants: dict,
    prepare_script: Path,
    remote_prep: str,
) -> str:
    """Clone monorepo and build every requested flat variant on one replica."""
    print(f"[exp1] prepare start host={host}", flush=True)
    ssh_host(
        user,
        host,
        f"set -euo pipefail; "
        f"export PATH=\"$HOME/.cargo/bin:$PATH\"; "
        f"repo=\"$HOME/{monorepo_name}\"; "
        f"if [ ! -d \"$repo/.git\" ]; then git clone {repo_url} \"$repo\"; fi; "
        f"cd \"$repo\"; git fetch --tags origin {branch} || true; "
        f"git checkout {branch}; git pull --ff-only origin {branch} || true",
        identity=identity,
        connect_timeout=30,
    )
    scp_to_host(prepare_script, user, host, remote_prep, identity=identity)
    ssh_host(user, host, f"chmod +x {remote_prep}", identity=identity)
    for name, cfg in variants.items():
        flat = cfg["flat_repo_name"]
        subdir = cfg["monorepo_subdir"]
        print(f"[exp1] prepare host={host} variant={name} -> $HOME/{flat}", flush=True)
        ssh_host(
            user,
            host,
            f"bash {remote_prep} \"$HOME/{monorepo_name}\" {branch} {subdir} \"$HOME/{flat}\"",
            identity=identity,
            connect_timeout=30,
        )
    print(f"[exp1] prepare done host={host}", flush=True)
    return host


def prepare_variants(
    *,
    hosts: list[dict],
    username: str,
    remote_key: str,
    repo_url: str,
    branch: str,
    monorepo_name: str,
    variants: dict,
    prepare_script: Path,
    skip_prepare: bool,
) -> None:
    """Parallel deploy on all replicas; blocks until every host finishes."""
    if skip_prepare:
        print("[exp1] skip prepare", flush=True)
        print("[exp1] phase prepare: skipped", flush=True)
        return

    remote_prep = f"/tmp/prepare_flat_tree_{int(time.time())}.sh"
    jobs = []
    for h in hosts:
        host = h["hostname"]
        user = h.get("username") or username
        jobs.append((host, user))

    total = len(jobs)
    print(f"[exp1] phase prepare: {total} hosts (parallel)", flush=True)
    print(
        f"[exp1] parallel prepare on {total} hosts for variants={list(variants)} "
        f"(delay/experiments wait until all finish)",
        flush=True,
    )
    failures: list[tuple[str, str]] = []
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
        futures = {
            pool.submit(
                _prepare_one_host,
                host=host,
                user=user,
                identity=remote_key,
                repo_url=repo_url,
                branch=branch,
                monorepo_name=monorepo_name,
                variants=variants,
                prepare_script=prepare_script,
                remote_prep=remote_prep,
            ): host
            for host, user in jobs
        }
        for fut in as_completed(futures):
            host = futures[fut]
            try:
                fut.result()
                completed += 1
                progress("exp1", completed, total, f"prepare done host={host}")
            except Exception as exc:  # noqa: BLE001 — surface per-host failure then abort
                failures.append((host, str(exc)))
                completed += 1
                progress("exp1", completed, total, f"prepare FAILED host={host}")
                print(f"[exp1] prepare FAILED host={host}: {exc}", file=sys.stderr, flush=True)

    if failures:
        detail = "; ".join(f"{h}: {err}" for h, err in failures)
        raise SystemExit(f"prepare failed on {len(failures)} host(s): {detail}")

    print("[exp1] phase prepare: complete", flush=True)
    print("[exp1] all replicas prepared; applying delay profiles and running experiments", flush=True)


def switch_wan(
    bench_dir: Path,
    settings_path: Path,
    py: str,
    *,
    network_tag: str,
    previous_tag: str | None,
) -> None:
    """Clear + setup tc-netem whenever we enter a (possibly new) delay profile."""
    if previous_tag is None:
        print(f"[exp1] apply delay profile network={network_tag} (initial)", flush=True)
    elif previous_tag != network_tag:
        print(
            f"[exp1] delay profile changed {previous_tag} -> {network_tag}; clear+setup again",
            flush=True,
        )
    else:
        print(f"[exp1] re-apply delay profile network={network_tag}", flush=True)

    code = f"""
from benchmark.cloudlab_wan import CloudLabWan
w = CloudLabWan(settings_file={str(settings_path)!r})
w.clear()
w.setup()
print('WAN profile applied:', {network_tag!r}, {str(settings_path)!r})
"""
    run([py, "-c", code], cwd=bench_dir)


def run_cell(
    *,
    bench_dir: Path,
    settings_path: Path,
    variant_cfg: dict,
    network_tag: str,
    workload: dict,
    py: str,
    password: str | None,
) -> Path:
    if password:
        os.environ["SSH_KEY_PASSWORD"] = password

    target_settings = bench_dir / "cloudlab_settings.json"
    target_settings.write_text(settings_path.read_text(encoding="utf-8"), encoding="utf-8")

    rate = int(variant_cfg["rate"])
    runs = int(variant_cfg["runs"])
    duration = int(variant_cfg["duration"])
    nodes = int(variant_cfg["nodes"])
    node_params = dict(variant_cfg["node_params"])

    bench_params = {
        "faults": 0,
        "nodes": [nodes],
        "workers": 1,
        "collocate": True,
        "rate_type": workload["rate_type"],
        "rate": [rate],
        "tx_size": 512,
        "duration": duration,
        "runs": runs,
        "network_tag": network_tag,
        "workload_tag": workload["tag"],
    }
    if workload.get("percentages"):
        bench_params["percentages"] = list(workload["percentages"])

    runner = bench_dir / "_exp1_cell_runner.py"
    runner.write_text(
        f"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
from types import SimpleNamespace

class ConnectKwargs(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc
    def __setattr__(self, key, value):
        self[key] = value

from benchmark.cloudlab_remote import CloudLabBench
from benchmark.utils import BenchError, Print

settings = json.loads(Path('cloudlab_settings.json').read_text())
password = settings.get('ssh_key_password') or ''
if password and not os.environ.get('SSH_KEY_PASSWORD'):
    os.environ['SSH_KEY_PASSWORD'] = password

ctx = SimpleNamespace(connect_kwargs=ConnectKwargs())
bench_params = json.loads({json.dumps(bench_params)!r})
node_params = json.loads({json.dumps(node_params)!r})
try:
    CloudLabBench(ctx).run(bench_params, node_params, False)
except BenchError as exc:
    Print.error(exc)
    sys.exit(1)
""",
        encoding="utf-8",
    )
    run([py, str(runner)], cwd=bench_dir)
    return bench_dir


def wipe_bench_artifacts(bench_dir: Path, *, logs_only: bool = False, tag: str = "exp") -> None:
    """Remove leftover bench outputs so collect/plot never mix prior runs."""
    targets = ["logs", "manta_result/logs"] if logs_only else [
        "logs",
        "results",
        "manta_result",
        "data/latest",
        "data/paper-data",
        "tusk_coupled",
        "decouple",
        "exp1results",
        "result_decouple",
        "manta_compare",
        "manta_final_geo",
    ]
    for name in targets:
        path = bench_dir / name
        if path.exists():
            shutil.rmtree(path)
            print(f"[{tag}] wiped {path}", flush=True)
    if not logs_only:
        for path in bench_dir.glob("bench-*.txt"):
            path.unlink(missing_ok=True)
        for path in bench_dir.glob("summary*.txt"):
            path.unlink(missing_ok=True)
        for path in bench_dir.glob("round_*.csv"):
            path.unlink(missing_ok=True)


def wipe_remote_repo_logs(
    *,
    hosts: list[dict],
    username: str,
    remote_key: str,
    repo_names: list[str],
    tag: str = "exp1",
    retries: int = 3,
) -> None:
    """Delete protocol log dirs on every replica; block until verified clean."""
    names = sorted({n for n in repo_names if n})
    if not names:
        return
    home = Path.home()
    for name in names:
        for rel in ("logs", "benchmark/logs", "manta_result/logs"):
            path = home / name / rel
            if path.exists():
                shutil.rmtree(path)
                print(f"[{tag}] wiped local {path}", flush=True)
    repos = " ".join(shlex.quote(n) for n in names)
    remote_cmd = (
        "set -e; "
        f"for repo in {repos}; do "
        'rm -rf "$HOME/$repo/logs" "$HOME/$repo/benchmark/logs" '
        '"$HOME/$repo/manta_result/logs"; '
        "done; "
        "left=0; "
        f"for repo in {repos}; do "
        'for d in "$HOME/$repo/logs" "$HOME/$repo/benchmark/logs" '
        '"$HOME/$repo/manta_result/logs"; do '
        'if [ -d "$d" ]; then '
        'n=$(find "$d" -type f -name "*.log" 2>/dev/null | wc -l); '
        "left=$((left + n)); "
        "fi; "
        "done; "
        "done; "
        'echo "LOGS_LEFT=$left"; '
        "test \"$left\" -eq 0"
    )

    def _one(host: dict) -> tuple[str, int, str]:
        h = str(host["hostname"])
        user = host.get("username", username)
        last_out = ""
        for attempt in range(1, retries + 1):
            cmd = _ssh_base(remote_key, 20) + [f"{user}@{h}", remote_cmd]
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
            last_out = ((proc.stdout or "") + (proc.stderr or "")).strip()
            if proc.returncode == 0 and "LOGS_LEFT=0" in last_out:
                return h, 0, last_out
            print(
                f"[{tag}] wipe retry {attempt}/{retries} host={h} rc={proc.returncode} "
                f"out={last_out!r}",
                flush=True,
            )
            time.sleep(1.0)
        return h, 1, last_out

    print(
        f"[{tag}] wiping remote logs on {len(hosts)} hosts for repos={names} "
        "(blocking until clean)...",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=min(16, max(1, len(hosts)))) as pool:
        results = list(pool.map(_one, hosts))
    failed = [(h, out) for h, rc, out in results if rc != 0]
    if failed:
        detail = "; ".join(f"{h}: {out}" for h, out in failed)
        raise SystemExit(
            f"[{tag}] remote log wipe incomplete on {len(failed)} host(s): {detail}"
        )
    print(
        f"[{tag}] remote log wipe complete on {len(hosts)} hosts; proceeding to next cell",
        flush=True,
    )


def collect_summaries(bench_dir: Path, variant: str, figure: str, out_dir: Path) -> int:
    """Copy only summary *.txt into results/Figure9{{a,b}} (no csv/png/pdf dumps)."""
    roots = {
        "decoupled": bench_dir / "decouple",
        "coupled": bench_dir / "tusk_coupled",
    }
    root = roots[variant]
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    if not root.exists():
        print(f"[exp1] no result root yet: {root}", flush=True)
        return 0
    for summary in root.rglob("*summary*.txt"):
        run_dir = summary.parent.name
        if summary.name == "summary.txt":
            dest = out_dir / f"{run_dir}.txt"
        else:
            dest = out_dir / summary.name
        shutil.copy2(summary, dest)
        count += 1
        print(f"[exp1] collected {dest}", flush=True)
    return count


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Controller Experiment 1 / Figure 9 runner")
    p.add_argument("--workdir", type=Path, required=True, help="Uploaded experiment_reproduced/experiment1 dir")
    p.add_argument("--settings", type=Path, required=True, help="Controller-local cloudlab_settings.json")
    p.add_argument("--matrix", type=Path, required=True)
    p.add_argument("--monorepo", type=Path, required=True, help="$HOME/manta-nsdi27 on controller")
    p.add_argument("--remote-key", required=True, help="SSH key path on controller for replica access")
    p.add_argument("--only-variant", choices=["coupled", "decoupled"], default=None)
    p.add_argument("--only-network", default=None)
    p.add_argument("--only-workload", default=None)
    p.add_argument("--skip-prepare", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    matrix = load_yaml(args.matrix)
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    if "cloudlab" in settings and isinstance(settings["cloudlab"], dict):
        settings = settings["cloudlab"]

    hosts = settings["hosts"]
    username = hosts[0].get("username", "ubuntu")
    repo_url = settings["repo"]["url"]
    branch = matrix.get("branch", "experiment1")
    monorepo_name = matrix.get("monorepo_name", "manta-nsdi27")
    port = int(settings.get("port", 5000))
    password = settings.get("ssh_key_password") or (settings.get("key") or {}).get("password")

    ensure_monorepo(args.monorepo, repo_url, branch)

    prepare_script = args.workdir / "scripts" / "prepare_flat_tree.sh"
    variants = matrix["variants"]
    if args.only_variant:
        variants = {args.only_variant: variants[args.only_variant]}

    # Phase 1: parallel deploy on all replicas (no delay / no bench yet).
    prepare_variants(
        hosts=hosts,
        username=username,
        remote_key=args.remote_key,
        repo_url=repo_url,
        branch=branch,
        monorepo_name=monorepo_name,
        variants=variants,
        prepare_script=prepare_script,
        skip_prepare=args.skip_prepare,
    )

    results_root = args.workdir / "results"
    results_root.mkdir(parents=True, exist_ok=True)
    # Never mirror full CloudLabBench dumps (csv/png/pdf) for laptop sync.
    stale_raw = results_root / "raw"
    if stale_raw.exists():
        shutil.rmtree(stale_raw)

    # Phase 2: experiments. Re-apply WAN on every network cell (clear+setup).
    last_network_tag: str | None = None

    networks_all = matrix["networks"]
    if args.only_network:
        networks_all = [n for n in networks_all if n["tag"] == args.only_network]
    workloads_all = matrix["workloads"]
    if args.only_workload:
        workloads_all = [w for w in workloads_all if w["tag"] == args.only_workload]
    total_cells = len(variants) * len(networks_all) * len(workloads_all)
    print(
        f"[exp1] phase bench: plan total_cells={total_cells} "
        f"variants={list(variants)} networks={[n['tag'] for n in networks_all]} "
        f"workloads={[w['tag'] for w in workloads_all]}",
        flush=True,
    )
    cell_i = 0

    for variant_name, variant_cfg in variants.items():
        subdir = variant_cfg["monorepo_subdir"]
        flat_repo = variant_cfg["flat_repo_name"]
        figure = variant_cfg["figure"]
        bench_dir = args.monorepo / subdir / "benchmark"
        if not bench_dir.is_dir():
            raise SystemExit(f"missing benchmark dir on controller: {bench_dir}")

        venv_py = bench_dir / ".venv" / "bin" / "python"
        if not venv_py.exists():
            run(["python3", "-m", "venv", str(bench_dir / ".venv")])
            run([str(venv_py), "-m", "pip", "install", "--upgrade", "pip"])
            run([str(venv_py), "-m", "pip", "install", "-r", "requirements.txt"], cwd=bench_dir)
            run([str(venv_py), "-m", "pip", "install", "pyyaml"], cwd=bench_dir)
        py = str(venv_py)

        networks = networks_all
        workloads = workloads_all

        # Drop prior-run / checked-in dumps before this variant's cells.
        wipe_bench_artifacts(bench_dir, logs_only=False, tag="exp1")
        wipe_remote_repo_logs(
            hosts=hosts,
            username=username,
            remote_key=args.remote_key,
            repo_names=[flat_repo],
            tag="exp1",
        )

        for network in networks:
            wan_path = args.workdir / network["wan_profile"]
            wan_profile = json.loads(wan_path.read_text(encoding="utf-8"))
            merged = merge_wan_settings(
                hosts,
                wan_profile,
                args.remote_key,
                port,
                flat_repo,
                branch,
                repo_url,
            )
            if password:
                merged["ssh_key_password"] = password
            cell_settings = args.workdir / "logs" / f"settings_{variant_name}_{network['tag']}.json"
            cell_settings.parent.mkdir(parents=True, exist_ok=True)
            cell_settings.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")

            (bench_dir / "cloudlab_settings.json").write_text(
                cell_settings.read_text(encoding="utf-8"), encoding="utf-8"
            )

            # Always clear+setup for this network cell so a new delay requirement
            # (or re-entry after another profile) is applied before any run.
            print(f"[exp1] phase wan: apply {network['tag']} ...", flush=True)
            switch_wan(
                bench_dir,
                bench_dir / "cloudlab_settings.json",
                py,
                network_tag=network["tag"],
                previous_tag=last_network_tag,
            )
            print("[exp1] phase wan: complete", flush=True)
            last_network_tag = network["tag"]

            for workload in workloads:
                cell_i += 1
                progress(
                    "exp1",
                    cell_i,
                    total_cells,
                    f"START variant={variant_name} network={network['tag']} "
                    f"workload={workload['tag']} rate={variant_cfg['rate']}",
                )
                print(
                    f"[exp1] run variant={variant_name} network={network['tag']} "
                    f"workload={workload['tag']} rate={variant_cfg['rate']}",
                    flush=True,
                )
                wipe_bench_artifacts(bench_dir, logs_only=True, tag="exp1")
                wipe_remote_repo_logs(
                    hosts=hosts,
                    username=username,
                    remote_key=args.remote_key,
                    repo_names=[flat_repo],
                    tag="exp1",
                )
                run_cell(
                    bench_dir=bench_dir,
                    settings_path=cell_settings,
                    variant_cfg=variant_cfg,
                    network_tag=network["tag"],
                    workload=workload,
                    py=py,
                    password=password,
                )
                progress(
                    "exp1",
                    cell_i,
                    total_cells,
                    f"DONE variant={variant_name} network={network['tag']} "
                    f"workload={workload['tag']}",
                )

        print(f"[exp1] phase collect: {figure} summaries ...", flush=True)
        out_dir = results_root / figure
        n = collect_summaries(bench_dir, variant_name, figure, out_dir)
        print(f"[exp1] {figure}: collected {n} summary files into {out_dir}", flush=True)
        print(f"[exp1] phase collect: complete ({n} files)", flush=True)

    print("[exp1] phase all: complete", flush=True)
    print("[exp1] done", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"[exp1] command failed: {exc}", file=sys.stderr)
        raise SystemExit(exc.returncode)
