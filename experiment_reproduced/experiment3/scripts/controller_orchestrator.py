#!/usr/bin/env python3
"""Controller-side Experiment 3 / Figure 11(a)+(c) orchestrator.

Runs on the CloudLab controller. Replicas are driven only via SSH/Fabric from here.

Each protocol (tusk, dag-rider, manta, chitu, mahi-mahi) is a separate logical
variant. ``tusk`` and ``dag-rider`` share git branch ``tusk`` / tree
``$HOME/manta-exp3-tusk`` and differ by solid-wave ``sigma``/``kappa``.
``manta`` / ``chitu`` / ``mahi-mahi`` each use their own branch checked out to
``$HOME/{flat_repo_name}``. All five protocols pass
``sigma``/``kappa``/``reference``/``coverage`` from ``matrix.yaml``.

Prepare runs in parallel across all replicas (one variant at a time) and must
finish before any WAN/delay setup or benchmark cells.

Suites:
  * figure11a — geo WAN clear+setup, faults=0
  * figure11c — geo WAN clear+setup, silent faults=3 (last 3 nodes not booted)
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

CORE_NODE_KEYS = (
    "header_size",
    "max_header_delay",
    "gc_depth",
    "sync_retry_delay",
    "sync_retry_nodes",
    "batch_size",
    "max_batch_delay",
)

# Committee solid-wave params (tusk / dag-rider / manta).
WAVE_KEYS = (
    "sigma",
    "kappa",
    "reference",
    "coverage",
)

MANTA_EXTRA_KEYS = (
    "allow_cross_step_weak_edges",
    "enable_fast_coin",
    "solid_commit_trigger_on_solid_step",
    "enable_commit_recheck",
    "enable_adaptive_intermediate_spill",
    "adaptive_intermediate_spill_trigger_digests",
    "adaptive_intermediate_spill_cap_digests",
    "fast_coin_candidate_threshold",
    "solid_candidate_threshold",
)

VARIANT_CHOICES = ("tusk", "dag-rider", "manta", "chitu", "mahi-mahi")
SUITE_CHOICES = ("figure11a", "figure11c")


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
    print("[exp3]", " ".join(cmd), flush=True)
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


def replica_hosts(base_hosts: list[dict]) -> list[dict]:
    """Return the 10 CloudLab replica hosts (10.10.1.1–10), excluding the controller."""
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
        by_octet[last] = dict(h)
    hosts = [by_octet[i] for i in range(1, 11) if i in by_octet]
    if len(hosts) != 10:
        raise SystemExit(
            f"expected 10 replica hosts, got {len(hosts)}: {[h.get('hostname') for h in hosts]}"
        )
    return hosts


def merge_settings(
    base_hosts: list[dict],
    *,
    remote_key: str,
    port: int,
    repo_name: str,
    branch: str,
    repo_url: str,
    wan_profile: dict | None,
) -> dict:
    """Build cloudlab settings for replicas only (controller never in peers)."""
    hosts = replica_hosts(base_hosts)
    out: dict = {
        "key": {"path": remote_key},
        "port": port,
        "repo": {"name": repo_name, "url": repo_url, "branch": branch},
        "hosts": hosts,
    }
    if wan_profile is not None:
        wan = dict(wan_profile.get("cloudlab_wan") or wan_profile)
        sites = wan.pop("site_by_host_index", None)
        if sites is not None and len(sites) != 10:
            raise SystemExit(f"site_by_host_index must have 10 entries, got {len(sites)}")
        if sites is not None:
            for i, entry in enumerate(hosts):
                entry["wan_site"] = sites[i]
        out["cloudlab_wan"] = {
            "rtt_ms": wan.get("rtt_ms", {}),
            "hosts": hosts,
        }
    return out


def prepare_controller_flat(repo_dir: Path, repo_url: str, branch: str, prepare_script: Path) -> None:
    """Clone/checkout/build one flat protocol tree on the controller."""
    print(f"[exp3] prepare controller flat={repo_dir} branch={branch}", flush=True)
    run(["bash", str(prepare_script), str(repo_dir), branch, repo_url])
    print(f"[exp3] controller prepared {repo_dir}", flush=True)


def _prepare_one_host(
    *,
    host: str,
    user: str,
    identity: str,
    repo_url: str,
    branch: str,
    flat_repo_name: str,
    prepare_script: Path,
    remote_prep: str,
) -> str:
    print(f"[exp3] prepare start host={host} flat={flat_repo_name} branch={branch}", flush=True)
    scp_to_host(prepare_script, user, host, remote_prep, identity=identity)
    ssh_host(user, host, f"chmod +x {remote_prep}", identity=identity)
    ssh_host(
        user,
        host,
        f"export PATH=\"$HOME/.cargo/bin:$PATH\"; "
        f"bash {remote_prep} \"$HOME/{flat_repo_name}\" {branch} {repo_url}",
        identity=identity,
        connect_timeout=30,
    )
    print(f"[exp3] prepare done host={host} flat={flat_repo_name}", flush=True)
    return host


def prepare_variant_on_replicas(
    *,
    hosts: list[dict],
    username: str,
    remote_key: str,
    repo_url: str,
    branch: str,
    flat_repo_name: str,
    prepare_script: Path,
    progress_base: int = 0,
    progress_total: int = 0,
) -> int:
    """Parallel prepare of one variant across all replicas; blocks until all finish.

    Returns the number of hosts completed in this batch (for cumulative progress).
    """
    remote_prep = f"/tmp/prepare_repo_exp3_{flat_repo_name}_{int(time.time())}.sh"
    jobs = [(h["hostname"], h.get("username") or username) for h in hosts]
    batch_total = len(jobs)
    total = progress_total if progress_total > 0 else batch_total
    print(
        f"[exp3] parallel prepare variant={flat_repo_name} on {batch_total} hosts",
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
                flat_repo_name=flat_repo_name,
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
                progress(
                    "exp3",
                    progress_base + completed,
                    total,
                    f"prepare done host={host} flat={flat_repo_name}",
                )
            except Exception as exc:  # noqa: BLE001
                failures.append((host, str(exc)))
                completed += 1
                progress(
                    "exp3",
                    progress_base + completed,
                    total,
                    f"prepare FAILED host={host} flat={flat_repo_name}",
                )
                print(f"[exp3] prepare FAILED host={host}: {exc}", file=sys.stderr, flush=True)

    if failures:
        detail = "; ".join(f"{h}: {err}" for h, err in failures)
        raise SystemExit(f"prepare failed on {len(failures)} host(s): {detail}")
    return completed


def prepare_all_variants(
    *,
    hosts: list[dict],
    username: str,
    remote_key: str,
    repo_url: str,
    variants: dict,
    prepare_script: Path,
    skip_prepare: bool,
) -> None:
    if skip_prepare:
        print("[exp3] skip prepare", flush=True)
        print("[exp3] phase prepare: skipped", flush=True)
        return

    n_hosts = len(hosts)
    # tusk and dag-rider share flat_repo_name / branch — prepare once per unique tree.
    unique_trees: list[tuple[str, str, str]] = []
    seen_flat: set[str] = set()
    for name, cfg in variants.items():
        flat = cfg["flat_repo_name"]
        if flat in seen_flat:
            print(f"[exp3] prepare: reuse tree {flat} for variant={name}", flush=True)
            continue
        seen_flat.add(flat)
        unique_trees.append((name, flat, cfg["branch"]))

    progress_total = n_hosts * len(unique_trees)
    print(
        f"[exp3] phase prepare: {n_hosts} hosts (parallel) × {len(unique_trees)} trees "
        f"(from {len(variants)} variants; total_jobs={progress_total})",
        flush=True,
    )
    progress_base = 0
    for name, flat, branch in unique_trees:
        prepare_controller_flat(Path.home() / flat, repo_url, branch, prepare_script)
        done = prepare_variant_on_replicas(
            hosts=hosts,
            username=username,
            remote_key=remote_key,
            repo_url=repo_url,
            branch=branch,
            flat_repo_name=flat,
            prepare_script=prepare_script,
            progress_base=progress_base,
            progress_total=progress_total,
        )
        progress_base += done
    print("[exp3] phase prepare: complete", flush=True)
    print("[exp3] all variants prepared; applying WAN / running experiments", flush=True)


def ensure_bench_venv(bench_dir: Path) -> str:
    venv_py = bench_dir / ".venv" / "bin" / "python"
    if not venv_py.exists():
        run(["python3", "-m", "venv", str(bench_dir / ".venv")])
        run([str(venv_py), "-m", "pip", "install", "--upgrade", "pip"])
        req = bench_dir / "requirements.txt"
        if req.is_file():
            run([str(venv_py), "-m", "pip", "install", "-r", "requirements.txt"], cwd=bench_dir)
        run([str(venv_py), "-m", "pip", "install", "pyyaml"], cwd=bench_dir, check=False)
    return str(venv_py)


def apply_wan(
    bench_dir: Path,
    settings_path: Path,
    py: str,
    *,
    network_tag: str,
    setup: bool,
) -> None:
    if setup:
        print(f"[exp3] apply WAN clear+setup network={network_tag}", flush=True)
        code = f"""
from benchmark.cloudlab_wan import CloudLabWan
w = CloudLabWan(settings_file={str(settings_path)!r})
w.clear()
w.setup()
print('WAN profile applied:', {network_tag!r}, {str(settings_path)!r})
"""
    else:
        print(f"[exp3] clear WAN only network={network_tag}", flush=True)
        code = f"""
from benchmark.cloudlab_wan import CloudLabWan
w = CloudLabWan(settings_file={str(settings_path)!r})
w.clear()
print('WAN cleared (no-delay):', {network_tag!r}, {str(settings_path)!r})
"""
    run([py, "-c", code], cwd=bench_dir)


def build_run_params(
    variant_name: str,
    variant_cfg: dict,
    suite: dict,
    suite_name: str,
) -> tuple[dict, dict]:
    """Return (bench_params, node_params) with tags placed per-protocol."""
    params_key = "node_params_11a" if suite_name == "figure11a" else "node_params_11c"
    raw = dict(variant_cfg[params_key])

    design_tag = suite["design_tag_by_variant"][variant_name]
    network_tag = suite["network_tag"]
    load_tag = suite["load_tag"]

    # Always start from core Narwhal keys present in the matrix.
    node_params = {k: raw[k] for k in CORE_NODE_KEYS if k in raw}

    # All five protocols expose committee wave params in their trees.
    if variant_name in ("tusk", "dag-rider", "manta", "chitu", "mahi-mahi"):
        for k in WAVE_KEYS:
            if k in raw:
                node_params[k] = raw[k]

    if variant_name == "manta":
        for k in MANTA_EXTRA_KEYS:
            if k in raw:
                node_params[k] = raw[k]
        node_params["design_tag"] = design_tag
        node_params["network_tag"] = network_tag
        node_params["load_tag"] = load_tag

    bench_params: dict = {
        "faults": int(suite["faults"]),
        "nodes": [int(suite["nodes"])],
        "workers": int(suite["workers"]),
        "collocate": True,
        "rate_type": suite.get("rate_type", "balanced"),
        "tx_size": int(suite["tx_size"]),
        "duration": int(suite["duration"]),
        "runs": int(suite["runs"]),
    }

    # Tags: manta reads from node_params; others from bench_params where supported.
    if variant_name in ("tusk", "dag-rider"):
        bench_params["design_tag"] = design_tag
        bench_params["network_tag"] = network_tag
    elif variant_name == "chitu":
        # Chitu organizes results by network_tag only.
        bench_params["network_tag"] = network_tag
    elif variant_name == "mahi-mahi":
        bench_params["design_tag"] = design_tag
        bench_params["network_tag"] = network_tag

    return bench_params, node_params


def run_cell(
    *,
    bench_dir: Path,
    settings_path: Path,
    bench_params: dict,
    node_params: dict,
    rate: int,
    py: str,
    password: str | None,
) -> None:
    if password:
        os.environ["SSH_KEY_PASSWORD"] = password

    target_settings = bench_dir / "cloudlab_settings.json"
    target_settings.write_text(settings_path.read_text(encoding="utf-8"), encoding="utf-8")

    bp = dict(bench_params)
    bp["rate"] = [int(rate)]

    runner = bench_dir / "_exp3_cell_runner.py"
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
bench_params = json.loads({json.dumps(bp)!r})
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


def _unique_dest(out_dir: Path, name: str) -> Path:
    dest = out_dir / name
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    i = 1
    while True:
        cand = out_dir / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
        i += 1


_KEEP_BENCH_DIRS = {
    ".venv",
    "benchmark",  # python package under benchmark/
    "__pycache__",
    "data",  # wipe data/latest + data/paper-data explicitly
}

# Design-tag / campaign output dirs used by Exp3 protocol trees.
_TAG_DIR_HINTS = (
    "_experiment",
    "_faulty",
    "-faulty",
    "_forpaper",
    "forpaper",
)


def _is_leftover_output_dir(path: Path) -> bool:
    """True for design-tag trees (and any dir already holding summary/bench txt)."""
    if not path.is_dir() or path.name in _KEEP_BENCH_DIRS:
        return False
    if any(hint in path.name for hint in _TAG_DIR_HINTS):
        return True
    for pat in ("**/summary*.txt", "**/bench-*.txt", "**/summary.txt"):
        if any(path.glob(pat)):
            return True
    return False


def wipe_bench_artifacts(bench_dir: Path, *, logs_only: bool = False) -> None:
    """Remove leftover bench outputs so collect/plot never mix prior runs.

    ``logs_only`` clears parse inputs between cells of the same variant (keeps
    ``results/`` / ``manta_result/`` / design-tag dirs so earlier rates remain).
    """
    targets = ["logs", "manta_result/logs"] if logs_only else [
        "logs",
        "results",
        "manta_result",
        "data/latest",
        "data/paper-data",
        # Exp1-style / shared leftover result roots if present on a protocol tree.
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
            print(f"[exp3] wiped {path}", flush=True)
    if not logs_only:
        for child in list(bench_dir.iterdir()):
            if _is_leftover_output_dir(child):
                shutil.rmtree(child)
                print(f"[exp3] wiped {child}", flush=True)
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
    retries: int = 3,
) -> None:
    """Delete protocol log dirs on every replica (and matching local trees).

    Blocks until every host reports no leftover ``*.log`` under the protocol log
    paths. The next bench cell must not start until this returns successfully.

    CloudLabBench.kill(delete_logs=True) runs ``rm -r logs`` without ``cd`` into
    ``$HOME/{repo}``, so stale ``$HOME/{repo}/logs`` survive — especially on
    silent-fault replicas that are never rebooted. ``download_logs`` then
    re-imports those files and poisons LogParser.
    """
    names = sorted({n for n in repo_names if n})
    if not names:
        return

    home = Path.home()
    for name in names:
        for rel in ("logs", "benchmark/logs", "manta_result/logs"):
            path = home / name / rel
            if path.exists():
                shutil.rmtree(path)
                print(f"[exp3] wiped local {path}", flush=True)

    repos = " ".join(shlex.quote(n) for n in names)
    # Remove, then verify: print leftover count (0 == clean).
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
                f"[exp3] wipe retry {attempt}/{retries} host={h} rc={proc.returncode} "
                f"out={last_out!r}",
                flush=True,
            )
            time.sleep(1.0)
        return h, 1, last_out

    print(
        f"[exp3] wiping remote logs on {len(hosts)} hosts for repos={names} "
        "(blocking until clean)...",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=min(16, max(1, len(hosts)))) as pool:
        results = list(pool.map(_one, hosts))
    failed = [(h, out) for h, rc, out in results if rc != 0]
    if failed:
        detail = "; ".join(f"{h}: {out}" for h, out in failed)
        raise SystemExit(
            f"[exp3] remote log wipe incomplete on {len(failed)} host(s): {detail}"
        )
    print(
        f"[exp3] remote log wipe complete on {len(hosts)} hosts; proceeding to next cell",
        flush=True,
    )


def collect_summaries(bench_dir: Path, out_dir: Path, *, design_tag: str | None = None) -> int:
    """Copy only this-run summary/bench *.txt into results/Figure11*/{result_dir}/.

    Roots:
      - ``results/`` (chitu)
      - ``manta_result/`` (manta)
      - ``{design_tag}/`` (tusk / dag-rider / mahi-mahi)

    Never scans ``data/`` sample trees or unrelated leftover tag dirs.
    """
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    roots = [bench_dir / "results", bench_dir / "manta_result"]
    if design_tag:
        roots.append(bench_dir / design_tag)
    patterns = ("**/summary*.txt", "**/bench-*.txt", "**/summary.txt")
    seen: set[Path] = set()
    count = 0
    for root in roots:
        if not root.is_dir():
            continue
        for pat in patterns:
            for path in root.glob(pat):
                if not path.is_file():
                    continue
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                if path.name == "summary.txt":
                    dest_name = f"{path.parent.name}_summary.txt"
                else:
                    dest_name = path.name
                dest = _unique_dest(out_dir, dest_name)
                shutil.copy2(path, dest)
                count += 1
                print(f"[exp3] collected {dest}", flush=True)
    return count


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Controller Experiment 3 / Figure 11 runner")
    p.add_argument("--workdir", type=Path, required=True, help="Uploaded experiment_reproduced/experiment3 dir")
    p.add_argument("--settings", type=Path, required=True, help="Controller-local cloudlab_settings.json")
    p.add_argument("--matrix", type=Path, required=True)
    p.add_argument("--remote-key", required=True, help="SSH key path on controller for replica access")
    p.add_argument("--only-variant", choices=list(VARIANT_CHOICES), default=None)
    p.add_argument("--only-suite", choices=list(SUITE_CHOICES), default=None)
    p.add_argument("--skip-prepare", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    matrix = load_yaml(args.matrix)
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    if "cloudlab" in settings and isinstance(settings["cloudlab"], dict):
        settings = settings["cloudlab"]

    hosts_all = settings["hosts"]
    hosts = replica_hosts(hosts_all)
    username = hosts[0].get("username", "ubuntu")
    repo_url = settings["repo"]["url"]
    port = int(settings.get("port", 5000))
    password = settings.get("ssh_key_password") or (settings.get("key") or {}).get("password")

    variants = matrix["variants"]
    if args.only_variant:
        variants = {args.only_variant: variants[args.only_variant]}

    prepare_script = args.workdir / "scripts" / "prepare_repo.sh"
    prepare_all_variants(
        hosts=hosts,
        username=username,
        remote_key=args.remote_key,
        repo_url=repo_url,
        variants=variants,
        prepare_script=prepare_script,
        skip_prepare=args.skip_prepare,
    )

    results_root = args.workdir / "results"
    results_root.mkdir(parents=True, exist_ok=True)
    logs_dir = args.workdir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    suites = matrix["suites"]
    if args.only_suite:
        suites = {args.only_suite: suites[args.only_suite]}

    # Prefer manta (or first variant) CloudLabWan for delay control.
    wan_variant_name = "manta" if "manta" in variants else next(iter(variants))
    wan_flat = variants[wan_variant_name]["flat_repo_name"]
    wan_bench = Path.home() / wan_flat / "benchmark"
    if not wan_bench.is_dir():
        raise SystemExit(f"missing WAN bench dir: {wan_bench} (run without --skip-prepare first)")
    wan_py = ensure_bench_venv(wan_bench)

    total_cells = 0
    for suite in suites.values():
        total_cells += len(variants) * len(list(suite["rates"]))
    print(
        f"[exp3] phase bench: plan total_cells={total_cells} "
        f"suites={list(suites)} variants={list(variants)}",
        flush=True,
    )
    cell_i = 0

    for suite_name, suite in suites.items():
        figure = suite["figure"]
        wan_rel = suite.get("wan_profile")
        wan_profile = None
        if wan_rel:
            wan_path = args.workdir / wan_rel
            wan_profile = json.loads(wan_path.read_text(encoding="utf-8"))

        # Settings for WAN ops (repo name = WAN driver flat tree).
        wan_merged = merge_settings(
            hosts_all,
            remote_key=args.remote_key,
            port=port,
            repo_name=wan_flat,
            branch=variants[wan_variant_name]["branch"],
            repo_url=repo_url,
            wan_profile=wan_profile,
        )
        if password:
            wan_merged["ssh_key_password"] = password
        wan_settings = logs_dir / f"settings_wan_{suite_name}.json"
        wan_settings.write_text(json.dumps(wan_merged, indent=2) + "\n", encoding="utf-8")
        (wan_bench / "cloudlab_settings.json").write_text(
            wan_settings.read_text(encoding="utf-8"), encoding="utf-8"
        )
        print(f"[exp3] phase wan: apply {suite['network_tag']} ({suite_name}) ...", flush=True)
        apply_wan(
            wan_bench,
            wan_bench / "cloudlab_settings.json",
            wan_py,
            network_tag=suite["network_tag"],
            setup=wan_profile is not None,
        )
        print("[exp3] phase wan: complete", flush=True)

        for variant_name, variant_cfg in variants.items():
            flat = variant_cfg["flat_repo_name"]
            branch = variant_cfg["branch"]
            bench_dir = Path.home() / flat / "benchmark"
            if not bench_dir.is_dir():
                raise SystemExit(f"missing benchmark dir: {bench_dir}")
            py = ensure_bench_venv(bench_dir)

            result_key = "result_dir_11a" if suite_name == "figure11a" else "result_dir_11c"
            result_dir_name = variant_cfg[result_key]
            out_dir = results_root / figure / result_dir_name

            cell_merged = merge_settings(
                hosts_all,
                remote_key=args.remote_key,
                port=port,
                repo_name=flat,
                branch=branch,
                repo_url=repo_url,
                wan_profile=wan_profile,
            )
            if password:
                cell_merged["ssh_key_password"] = password
            cell_settings = logs_dir / f"settings_{suite_name}_{variant_name}.json"
            cell_settings.write_text(json.dumps(cell_merged, indent=2) + "\n", encoding="utf-8")

            bench_base, node_params = build_run_params(variant_name, variant_cfg, suite, suite_name)
            rates = list(suite["rates"])
            print(
                f"[exp3] suite={suite_name} variant={variant_name} "
                f"faults={suite['faults']} rates={rates}",
                flush=True,
            )
            # Drop prior-run / checked-in dumps before this variant's sweep.
            wipe_bench_artifacts(bench_dir, logs_only=False)
            wipe_remote_repo_logs(
                hosts=hosts,
                username=username,
                remote_key=args.remote_key,
                repo_names=[flat],
            )
            for rate in rates:
                cell_i += 1
                progress(
                    "exp3",
                    cell_i,
                    total_cells,
                    f"START suite={suite_name} variant={variant_name} rate={rate}",
                )
                print(
                    f"[exp3] run suite={suite_name} variant={variant_name} rate={rate}",
                    flush=True,
                )
                # Always delete local + remote logs before every cell. Silent-fault
                # nodes are never rebooted; leftover remote logs would be downloaded
                # and mix into LogParser.
                wipe_bench_artifacts(bench_dir, logs_only=True)
                wipe_remote_repo_logs(
                    hosts=hosts,
                    username=username,
                    remote_key=args.remote_key,
                    repo_names=[flat],
                )
                run_cell(
                    bench_dir=bench_dir,
                    settings_path=cell_settings,
                    bench_params=bench_base,
                    node_params=node_params,
                    rate=int(rate),
                    py=py,
                    password=password,
                )
                progress(
                    "exp3",
                    cell_i,
                    total_cells,
                    f"DONE suite={suite_name} variant={variant_name} rate={rate}",
                )

            print(f"[exp3] phase collect: {figure}/{result_dir_name} ...", flush=True)
            design_tag = suite["design_tag_by_variant"][variant_name]
            n = collect_summaries(bench_dir, out_dir, design_tag=design_tag)
            print(
                f"[exp3] {figure}/{result_dir_name}: collected {n} files into {out_dir} "
                f"(design_tag={design_tag})",
                flush=True,
            )
            print(f"[exp3] phase collect: complete ({n} files)", flush=True)

    print("[exp3] phase all: complete", flush=True)
    print("[exp3] done", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"[exp3] command failed: {exc}", file=sys.stderr)
        raise SystemExit(exc.returncode)
