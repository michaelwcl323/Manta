#!/usr/bin/env python3
"""Controller-side Experiment 2 / Figure 10 orchestrator.

Runs on the CloudLab controller. Replicas are driven only via SSH/Fabric from here.

Uses the flat manta protocol on branch ``experiment2`` (repo root = protocol tree).

Prepare runs in parallel across all replicas and must finish before any WAN/delay
setup or benchmark cells.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

CONSENSUS_TPS_RE = re.compile(r"Consensus TPS:\s*([\d,]+)")
CONSENSUS_LAT_RE = re.compile(r"Consensus latency:\s*([\d,]+)\s*ms")


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


def run(
    cmd: list[str],
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    print("[exp2]", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check, env=env)


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


def reset_remote_benchmark_processes(
    *,
    hosts: list[dict],
    username: str,
    remote_key: str,
    tag: str,
) -> None:
    """Kill detached benchmark processes on every replica and verify zero remain.

    This controller-level reset does not depend on whichever protocol checkout is
    currently installed, so it is safe to run before a fresh clone or a protocol
    switch.
    """
    remote_cmd = r"""
set -euo pipefail
pattern='[t]arget/(debug|release)/node|[b]enchmark_client|[./][n]ode .* run --keys|[/]tmp/run_(primary|worker|client)-|[r]esource-cpu[.]raw|[r]esource-net[.]raw'
pids="$(pgrep -f "$pattern" || true)"
if [ -n "$pids" ]; then
  kill -TERM $pids 2>/dev/null || true
  sleep 1
fi
pids="$(pgrep -f "$pattern" || true)"
if [ -n "$pids" ]; then
  kill -KILL $pids 2>/dev/null || true
  sleep 1
fi
left="$(pgrep -af "$pattern" || true)"
if [ -n "$left" ]; then
  printf 'PROCESSES_LEFT=1\n%s\n' "$left" >&2
  exit 42
fi
rm -f /tmp/run_[p]rimary-*.sh /tmp/run_[w]orker-*.sh /tmp/run_[c]lient-*.sh
printf 'PROCESSES_LEFT=0\n'
"""
    failures: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
        futures = {}
        for entry in hosts:
            host = entry["hostname"]
            user = entry.get("username") or username
            future = pool.submit(
                ssh_host,
                user,
                host,
                remote_cmd,
                identity=remote_key,
                connect_timeout=30,
            )
            futures[future] = host
        for future in as_completed(futures):
            host = futures[future]
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001
                failures.append((host, str(exc)))
    if failures:
        detail = "; ".join(f"{host}: {error}" for host, error in failures)
        raise SystemExit(
            f"[{tag}] global process reset failed on {len(failures)} host(s): {detail}"
        )
    print(f"[{tag}] global process reset verified on {len(hosts)} hosts", flush=True)


def merge_wan_settings(
    base_hosts: list[dict],
    wan_profile: dict,
    remote_key: str,
    port: int,
    repo_name: str,
    branch: str,
    repo_url: str,
) -> dict:
    """Merge paper WAN profile onto replica hosts only.

    Controller must never appear in hosts/peers so controller↔replica delay stays 0
    (CloudLabWan also excludes TCP/22 from netem).
    """
    wan = dict(wan_profile.get("cloudlab_wan") or wan_profile)
    sites = wan.pop("site_by_host_index", None)
    if sites is not None and len(sites) != 10:
        raise SystemExit(f"site_by_host_index must have 10 entries, got {len(sites)}")

    hosts = replica_hosts(base_hosts)
    if sites is not None:
        for i, entry in enumerate(hosts):
            entry["wan_site"] = sites[i]

    return {
        "key": {"path": remote_key},
        "port": port,
        "repo": {"name": repo_name, "url": repo_url, "branch": branch},
        "hosts": hosts,
        "cloudlab_wan": {
            "rtt_ms": wan.get("rtt_ms", {}),
            "hosts": hosts,
        },
    }


def ensure_monorepo(repo_dir: Path, repo_url: str, branch: str) -> None:
    """Create a fresh controller checkout and verify its branch tip."""
    repo_dir = repo_dir.expanduser().resolve()
    if repo_dir in (Path("/"), Path.home().resolve()):
        raise SystemExit(f"refusing to replace unsafe repository path: {repo_dir}")
    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    run([
        "git", "clone", "--branch", branch, "--single-branch", repo_url, str(repo_dir)
    ])
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    origin_head = subprocess.run(
        ["git", "rev-parse", f"origin/{branch}"], cwd=repo_dir, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    if head != origin_head:
        raise SystemExit(
            f"fresh checkout verification failed for {repo_dir}: {head} != {origin_head}"
        )
    print(f"fresh checkout verified: {repo_dir}@{head}", flush=True)


def prepare_controller_monorepo(repo_dir: Path) -> None:
    """Mirror prepare_repo.sh locally on the controller monorepo."""
    print(f"[exp2] prepare controller monorepo={repo_dir}", flush=True)
    env = os.environ.copy()
    cargo_bin = Path.home() / ".cargo" / "bin"
    env["PATH"] = f"{cargo_bin}:{env.get('PATH', '')}"

    node = repo_dir / "target" / "release" / "node"
    client = repo_dir / "target" / "release" / "benchmark_client"
    # Build once; reuse binaries if already present (node only needs compiling once).
    if node.is_file() and os.access(node, os.X_OK) and client.is_file() and os.access(client, os.X_OK):
        print("[exp2] release binaries already present; skip cargo build", flush=True)
    else:
        run(["cargo", "build", "--release", "--features", "benchmark"], cwd=repo_dir, env=env)
    if not node.is_file() or not client.is_file():
        raise SystemExit(f"missing release binaries under {repo_dir / 'target' / 'release'}")
    # Never symlink over the `node/` crate directory; only link the client launcher.
    link = repo_dir / "benchmark_client"
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.exists():
        raise SystemExit(f"refusing to replace non-file path: {link}")
    link.symlink_to(Path("target") / "release" / "benchmark_client")

    bench_dir = repo_dir / "benchmark"
    req = bench_dir / "requirements.txt"
    if req.is_file():
        venv_py = bench_dir / ".venv" / "bin" / "python"
        if not venv_py.exists():
            run(["python3", "-m", "venv", str(bench_dir / ".venv")])
            run([str(venv_py), "-m", "pip", "install", "--upgrade", "pip"])
            run([str(venv_py), "-m", "pip", "install", "-r", "requirements.txt"], cwd=bench_dir)
        run([str(venv_py), "-m", "pip", "install", "pyyaml"], cwd=bench_dir, check=False)
    print("[exp2] controller monorepo prepared", flush=True)


def _prepare_one_host(
    *,
    host: str,
    user: str,
    identity: str,
    repo_url: str,
    branch: str,
    monorepo_name: str,
    prepare_script: Path,
    remote_prep: str,
) -> str:
    """Clone/checkout experiment2 and build on one replica."""
    print(f"[exp2] prepare start host={host}", flush=True)
    scp_to_host(prepare_script, user, host, remote_prep, identity=identity)
    ssh_host(user, host, f"chmod +x {remote_prep}", identity=identity)
    ssh_host(
        user,
        host,
        f"export PATH=\"$HOME/.cargo/bin:$PATH\"; "
        f"bash {remote_prep} \"$HOME/{monorepo_name}\" {branch} {repo_url}",
        identity=identity,
        connect_timeout=30,
    )
    print(f"[exp2] prepare done host={host}", flush=True)
    return host


def prepare_replicas(
    *,
    hosts: list[dict],
    username: str,
    remote_key: str,
    repo_url: str,
    branch: str,
    monorepo_name: str,
    prepare_script: Path,
    skip_prepare: bool,
) -> None:
    """Parallel deploy on all replicas; blocks until every host finishes."""
    if skip_prepare:
        print("[exp2] skip prepare", flush=True)
        print("[exp2] phase prepare: skipped", flush=True)
        return

    remote_prep = f"/tmp/prepare_repo_exp2_{int(time.time())}.sh"
    jobs = []
    for h in hosts:
        host = h["hostname"]
        user = h.get("username") or username
        jobs.append((host, user))

    total = len(jobs)
    print(f"[exp2] phase prepare: {total} hosts (parallel)", flush=True)
    print(
        f"[exp2] parallel prepare on {total} hosts "
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
                progress("exp2", completed, total, f"prepare done host={host}")
            except Exception as exc:  # noqa: BLE001 — surface per-host failure then abort
                failures.append((host, str(exc)))
                completed += 1
                progress("exp2", completed, total, f"prepare FAILED host={host}")
                print(f"[exp2] prepare FAILED host={host}: {exc}", file=sys.stderr, flush=True)

    if failures:
        detail = "; ".join(f"{h}: {err}" for h, err in failures)
        raise SystemExit(f"prepare failed on {len(failures)} host(s): {detail}")

    print("[exp2] phase prepare: complete", flush=True)
    print("[exp2] all replicas prepared; applying delay profile and running experiments", flush=True)


def switch_wan(bench_dir: Path, settings_path: Path, py: str, *, network_tag: str) -> None:
    print(f"[exp2] apply delay profile network={network_tag}", flush=True)
    code = f"""
from benchmark.cloudlab_wan import CloudLabWan
w = CloudLabWan(settings_file={str(settings_path)!r})
w.clear()
w.setup()
print('WAN profile applied:', {network_tag!r}, {str(settings_path)!r})
"""
    run([py, "-c", code], cwd=bench_dir)


def expand_cells(suite_name: str, suite: dict, defaults: dict) -> list[dict]:
    """Expand suite into concrete bench cells."""
    cells: list[dict] = []
    base_rate = int(suite.get("rate", defaults["rate"]))
    base_runs = int(suite.get("runs", 1))
    duration = int(suite.get("duration", defaults["duration"]))
    nodes = int(suite.get("nodes", defaults["nodes"]))
    workers = int(suite.get("workers", defaults["workers"]))
    tx_size = int(suite.get("tx_size", defaults["tx_size"]))
    node_base = dict(defaults.get("node_params_base") or {})
    node_base.update(suite.get("node_params") or {})

    common_tags = {
        "design_tag": suite["design_tag"],
        "network_tag": suite["network_tag"],
        "load_tag": suite["load_tag"],
        "attack_enabled": bool(suite.get("attack_enabled", False)),
    }
    for key in (
        "attack_start_secs",
        "attack_duration_secs",
        "attack_group_size",
        "attack_limit_headers",
        "attack_limit_certificates",
    ):
        if key in suite:
            common_tags[key] = suite[key]

    if suite_name == "figure10a_10b" or "configs" not in suite:
        coverage = int(suite["coverage"])
        for sigma, kappa, reference in itertools.product(
            list(suite["sigma"]),
            list(suite["kappa"]),
            list(suite["reference"]),
        ):
            node_params = dict(node_base)
            node_params.update(common_tags)
            node_params.update(
                {
                    "sigma": int(sigma),
                    "kappa": int(kappa),
                    "reference": int(reference),
                    "coverage": coverage,
                }
            )
            cells.append(
                {
                    "sigma": int(sigma),
                    "kappa": int(kappa),
                    "reference": int(reference),
                    "coverage": coverage,
                    "rate": base_rate,
                    "runs": base_runs,
                    "duration": duration,
                    "nodes": nodes,
                    "workers": workers,
                    "tx_size": tx_size,
                    "node_params": node_params,
                    "label": f"s{sigma}-k{kappa}-ref{reference}",
                }
            )
    else:
        sigma = int(suite["sigma"])
        for cfg in suite["configs"]:
            kappa = int(cfg["kappa"])
            reference = int(cfg["reference"])
            coverage = int(cfg["coverage"])
            node_params = dict(node_base)
            node_params.update(common_tags)
            node_params.update(
                {
                    "sigma": sigma,
                    "kappa": kappa,
                    "reference": reference,
                    "coverage": coverage,
                }
            )
            cells.append(
                {
                    "sigma": sigma,
                    "kappa": kappa,
                    "reference": reference,
                    "coverage": coverage,
                    "rate": base_rate,
                    "runs": base_runs,
                    "duration": duration,
                    "nodes": nodes,
                    "workers": workers,
                    "tx_size": tx_size,
                    "node_params": node_params,
                    "label": f"k{kappa}-ref{reference}",
                }
            )
    return cells


def run_cell(
    *,
    bench_dir: Path,
    settings_path: Path,
    cell: dict,
    py: str,
    password: str | None,
) -> None:
    if password:
        os.environ["SSH_KEY_PASSWORD"] = password

    target_settings = bench_dir / "cloudlab_settings.json"
    target_settings.write_text(settings_path.read_text(encoding="utf-8"), encoding="utf-8")

    node_params = dict(cell["node_params"])
    bench_params = {
        "faults": 0,
        "nodes": [int(cell["nodes"])],
        "workers": int(cell["workers"]),
        "collocate": True,
        "rate_type": "balanced",
        "rate": [int(cell["rate"])],
        "tx_size": int(cell["tx_size"]),
        "duration": int(cell["duration"]),
        "runs": int(cell["runs"]),
    }

    runner = bench_dir / "_exp2_cell_runner.py"
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


def _parse_int_commas(text: str) -> int:
    return int(text.replace(",", ""))


def parse_summary_metrics(summary_text: str) -> tuple[int | None, int | None]:
    tps_m = CONSENSUS_TPS_RE.search(summary_text)
    lat_m = CONSENSUS_LAT_RE.search(summary_text)
    tps = _parse_int_commas(tps_m.group(1)) if tps_m else None
    lat = _parse_int_commas(lat_m.group(1)) if lat_m else None
    return tps, lat


def _meta_from_run_dir(run_dir: Path) -> dict:
    meta_path = run_dir / "run_metadata.json"
    if not meta_path.is_file():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    out: dict = {}
    node = data.get("node_params") or {}
    bench = data.get("bench_params") or {}
    for key in ("sigma", "kappa", "reference", "coverage"):
        if key in node and node[key] is not None:
            out[key] = int(node[key])
    if "run" in bench and bench["run"] is not None:
        out["run"] = int(bench["run"])
    if "rate" in bench and bench["rate"] is not None:
        out["rate"] = int(bench["rate"])
    # Some writers nest run_index.
    if "run_index" in data and data["run_index"] is not None:
        out["run"] = int(data["run_index"])
    return out


def _meta_from_dirname(name: str) -> dict:
    out: dict = {}
    for key, pat in (
        ("sigma", r"-s(\d+)"),
        ("kappa", r"-k(\d+)"),
        ("reference", r"-ref(\d+)"),
        ("rate", r"-r(\d+)"),
        ("run", r"-run(\d+)"),
    ):
        m = re.search(pat, name)
        if m:
            out[key] = int(m.group(1))
    return out


def wipe_bench_artifacts(bench_dir: Path, *, logs_only: bool = False, tag: str = "exp2") -> None:
    """Remove leftover bench outputs so collect/plot never mix prior runs."""
    targets = ["logs", "manta_result/logs"] if logs_only else [
        "logs",
        "results",
        "manta_result",
        "data/latest",
        "data/paper-data",
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


def wipe_remote_repo_logs(
    *,
    hosts: list[dict],
    username: str,
    remote_key: str,
    repo_names: list[str],
    tag: str = "exp2",
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


def collect_figure10a_10b(bench_dir: Path, suite: dict, out_dir: Path) -> int:
    """Parse summaries into consensus_summary.csv only (plot input for 10a/10b)."""
    design = suite["design_tag"]
    network = suite["network_tag"]
    load = suite["load_tag"]
    root = bench_dir / "manta_result" / design / network / load
    out_dir.mkdir(parents=True, exist_ok=True)
    # Drop any previous raw dumps; plots only need the aggregate CSV.
    stale_raw = out_dir / "raw"
    if stale_raw.exists():
        shutil.rmtree(stale_raw)

    rows: list[dict] = []
    if not root.exists():
        print(f"[exp2] no result root yet: {root}", flush=True)
    else:
        for summary in sorted(root.rglob("summary.txt")):
            run_dir = summary.parent
            text = summary.read_text(encoding="utf-8", errors="replace")
            tps, lat = parse_summary_metrics(text)
            meta = _meta_from_dirname(run_dir.name)
            meta.update({k: v for k, v in _meta_from_run_dir(run_dir).items() if v is not None})
            if tps is None or lat is None:
                print(f"[exp2] skip (missing metrics): {summary}", flush=True)
                continue
            if not all(k in meta for k in ("sigma", "kappa", "reference")):
                print(f"[exp2] skip (missing sigma/kappa/ref): {summary}", flush=True)
                continue
            row = {
                "sigma": int(meta["sigma"]),
                "kappa": int(meta["kappa"]),
                "reference": int(meta["reference"]),
                "input_rate": int(meta.get("rate", suite.get("rate", 100000))),
                "run": int(meta.get("run", 0)),
                "consensus_tps": int(tps),
                "consensus_latency_ms": int(lat),
            }
            rows.append(row)
            print(
                f"[exp2] row sigma={row['sigma']} kappa={row['kappa']} "
                f"ref={row['reference']} run={row['run']}",
                flush=True,
            )

    csv_path = out_dir / "consensus_summary.csv"
    fieldnames = [
        "sigma",
        "kappa",
        "reference",
        "input_rate",
        "run",
        "consensus_tps",
        "consensus_latency_ms",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r["sigma"], r["kappa"], r["reference"], r["run"])):
            writer.writerow(row)
    print(f"[exp2] wrote {csv_path} ({len(rows)} rows)", flush=True)
    return len(rows)


def collect_figure10c(bench_dir: Path, suite: dict, out_dir: Path, cells: list[dict]) -> int:
    """Copy only latency.csv into results/Figure10c/{label}/ (plot input for 10c)."""
    design = suite["design_tag"]
    network = suite["network_tag"]
    load = suite["load_tag"]
    root = bench_dir / "manta_result" / design / network / load
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    if not root.exists():
        print(f"[exp2] no result root yet: {root}", flush=True)
        return 0

    for cell in cells:
        kappa = cell["kappa"]
        reference = cell["reference"]
        label = cell["label"]  # k2-ref4
        dest = out_dir / label
        # Prefer newest matching run dir that has latency.csv.
        candidates = []
        for latency in root.rglob("latency.csv"):
            run_dir = latency.parent
            meta = _meta_from_dirname(run_dir.name)
            meta.update(_meta_from_run_dir(run_dir))
            if int(meta.get("kappa", -1)) != int(kappa):
                continue
            if int(meta.get("reference", -1)) != int(reference):
                continue
            # Directory name often embeds -k{κ}-ref{r}-.
            if f"-k{kappa}-ref{reference}" not in run_dir.name and (
                "kappa" not in meta or "reference" not in meta
            ):
                continue
            candidates.append(run_dir)
        if not candidates:
            print(f"[exp2] Figure10c: no run dir for {label}", flush=True)
            continue
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        src = candidates[0]
        src_latency = src / "latency.csv"
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_latency, dest / "latency.csv")
        count += 1
        print(f"[exp2] Figure10c collected {src.name}/latency.csv -> {dest}", flush=True)
    return count


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Controller Experiment 2 / Figure 10 runner")
    p.add_argument("--workdir", type=Path, required=True, help="Uploaded experiment_reproduced/experiment2 dir")
    p.add_argument("--settings", type=Path, required=True, help="Controller-local cloudlab_settings.json")
    p.add_argument("--matrix", type=Path, required=True)
    p.add_argument("--monorepo", type=Path, required=True, help="$HOME/manta-nsdi27 on controller")
    p.add_argument("--remote-key", required=True, help="SSH key path on controller for replica access")
    p.add_argument(
        "--only-suite",
        choices=["figure10a_10b", "figure10c"],
        default=None,
    )
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
    branch = matrix.get("branch", "experiment2")
    monorepo_name = matrix.get("monorepo_name", "manta-nsdi27")
    port = int(settings.get("port", 5000))
    password = settings.get("ssh_key_password") or (settings.get("key") or {}).get("password")
    defaults = matrix.get("defaults") or {}

    reset_remote_benchmark_processes(
        hosts=hosts, username=username, remote_key=args.remote_key, tag="exp2"
    )
    # Phase 1: fresh controller monorepo on experiment2.
    if not args.skip_prepare:
        ensure_monorepo(args.monorepo, repo_url, branch)
        prepare_controller_monorepo(args.monorepo)

    prepare_script = args.workdir / "scripts" / "prepare_repo.sh"
    prepare_replicas(
        hosts=hosts,
        username=username,
        remote_key=args.remote_key,
        repo_url=repo_url,
        branch=branch,
        monorepo_name=monorepo_name,
        prepare_script=prepare_script,
        skip_prepare=args.skip_prepare,
    )

    results_root = args.workdir / "results"
    results_root.mkdir(parents=True, exist_ok=True)
    logs_dir = args.workdir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    bench_dir = args.monorepo / "benchmark"
    if not bench_dir.is_dir():
        raise SystemExit(f"missing benchmark dir on controller: {bench_dir}")

    venv_py = bench_dir / ".venv" / "bin" / "python"
    if not venv_py.exists():
        run(["python3", "-m", "venv", str(bench_dir / ".venv")])
        run([str(venv_py), "-m", "pip", "install", "--upgrade", "pip"])
        run([str(venv_py), "-m", "pip", "install", "-r", "requirements.txt"], cwd=bench_dir)
        run([str(venv_py), "-m", "pip", "install", "pyyaml"], cwd=bench_dir)
    py = str(venv_py)

    # Phase 2: apply WAN once (geo).
    network = matrix["network"]
    wan_path = args.workdir / network["wan_profile"]
    wan_profile = json.loads(wan_path.read_text(encoding="utf-8"))
    merged = merge_wan_settings(
        hosts_all,
        wan_profile,
        args.remote_key,
        port,
        monorepo_name,
        branch,
        repo_url,
    )
    if password:
        merged["ssh_key_password"] = password
    wan_settings = logs_dir / "settings_geo.json"
    wan_settings.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    (bench_dir / "cloudlab_settings.json").write_text(
        wan_settings.read_text(encoding="utf-8"), encoding="utf-8"
    )
    print(f"[exp2] phase wan: apply {network['tag']} ...", flush=True)
    switch_wan(bench_dir, bench_dir / "cloudlab_settings.json", py, network_tag=network["tag"])
    print("[exp2] phase wan: complete", flush=True)

    suites = matrix["suites"]
    if args.only_suite:
        suites = {args.only_suite: suites[args.only_suite]}

    suite_cells: dict[str, list[dict]] = {}
    total_cells = 0
    for suite_name, suite in suites.items():
        cells = expand_cells(suite_name, suite, defaults)
        suite_cells[suite_name] = cells
        total_cells += len(cells)
    print(
        f"[exp2] phase bench: plan total_cells={total_cells} suites={list(suites)}",
        flush=True,
    )
    cell_i = 0

    for suite_name, suite in suites.items():
        reset_remote_benchmark_processes(
            hosts=hosts, username=username, remote_key=args.remote_key, tag="exp2"
        )
        cells = suite_cells[suite_name]
        print(f"[exp2] suite={suite_name} cells={len(cells)}", flush=True)
        wipe_bench_artifacts(bench_dir, logs_only=False, tag="exp2")
        wipe_remote_repo_logs(
            hosts=hosts,
            username=username,
            remote_key=args.remote_key,
            repo_names=[monorepo_name],
            tag="exp2",
        )
        for cell in cells:
            cell_i += 1
            reset_remote_benchmark_processes(
                hosts=hosts,
                username=username,
                remote_key=args.remote_key,
                tag="exp2",
            )
            progress(
                "exp2",
                cell_i,
                total_cells,
                f"START suite={suite_name} label={cell['label']} "
                f"sigma={cell['sigma']} kappa={cell['kappa']} ref={cell['reference']}",
            )
            print(
                f"[exp2] run suite={suite_name} label={cell['label']} "
                f"sigma={cell['sigma']} kappa={cell['kappa']} ref={cell['reference']} "
                f"coverage={cell['coverage']} rate={cell['rate']} runs={cell['runs']}",
                flush=True,
            )
            wipe_bench_artifacts(bench_dir, logs_only=True, tag="exp2")
            wipe_remote_repo_logs(
                hosts=hosts,
                username=username,
                remote_key=args.remote_key,
                repo_names=[monorepo_name],
                tag="exp2",
            )
            run_cell(
                bench_dir=bench_dir,
                settings_path=wan_settings,
                cell=cell,
                py=py,
                password=password,
            )
            progress(
                "exp2",
                cell_i,
                total_cells,
                f"DONE suite={suite_name} label={cell['label']}",
            )

        figure = suite["figure"]
        out_dir = results_root / figure
        print(f"[exp2] phase collect: {figure} ...", flush=True)
        if suite_name == "figure10a_10b" or figure == "Figure10a_10b":
            n = collect_figure10a_10b(bench_dir, suite, out_dir)
            print(f"[exp2] {figure}: collected {n} summary rows into {out_dir}", flush=True)
        else:
            n = collect_figure10c(bench_dir, suite, out_dir, cells)
            print(f"[exp2] {figure}: collected {n} run dirs into {out_dir}", flush=True)
        print(f"[exp2] phase collect: complete ({n})", flush=True)

    print("[exp2] phase all: complete", flush=True)
    print("[exp2] done", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"[exp2] command failed: {exc}", file=sys.stderr)
        raise SystemExit(exc.returncode)
