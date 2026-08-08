#!/usr/bin/env python3
"""Controller-side Experiment 4 / Figure 12 (+ Table 2) orchestrator.

Runs on the CloudLab controller. Replicas are driven only via SSH/Fabric from here.

Uses the flat manta protocol on branch ``experiment4`` (repo root = protocol tree)
with resource monitors that write ``resource_usage_summary.txt`` per run.

Prepare runs in parallel across all replicas and must finish before any WAN/delay
setup or benchmark cells.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

CONSENSUS_TPS_RE = re.compile(r"Consensus TPS:\s*([\d,]+)")
CONSENSUS_LAT_RE = re.compile(r"Consensus latency:\s*([\d,]+)\s*ms")
CPU_RE = re.compile(r"cpu avg/max\s*:\s*([\d.]+)%\s*/\s*([\d.]+)%")
RX_RE = re.compile(r"rx avg/max\s*:\s*([\d.]+)\s*/\s*([\d.]+)\s*Mbps")
TX_RE = re.compile(r"tx avg/max\s*:\s*([\d.]+)\s*/\s*([\d.]+)\s*Mbps")
SUITE_CHOICES = ["figure12_complete", "figure12_noflexible"]


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
    print("[exp4]", " ".join(cmd), flush=True)
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


def merge_wan_settings(
    base_hosts: list[dict],
    wan_profile: dict,
    remote_key: str,
    port: int,
    repo_name: str,
    branch: str,
    repo_url: str,
) -> dict:
    """Merge paper WAN profile onto replica hosts only."""
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
    if not (repo_dir / ".git").exists():
        run(["git", "clone", repo_url, str(repo_dir)])
    run(["git", "fetch", "--tags", "origin", branch], cwd=repo_dir, check=False)
    run(["git", "checkout", branch], cwd=repo_dir)
    run(["git", "pull", "--ff-only", "origin", branch], cwd=repo_dir, check=False)


def prepare_controller_monorepo(repo_dir: Path) -> None:
    """Mirror prepare_repo.sh locally on the controller monorepo."""
    print(f"[exp4] prepare controller monorepo={repo_dir}", flush=True)
    env = os.environ.copy()
    cargo_bin = Path.home() / ".cargo" / "bin"
    env["PATH"] = f"{cargo_bin}:{env.get('PATH', '')}"

    node = repo_dir / "target" / "release" / "node"
    client = repo_dir / "target" / "release" / "benchmark_client"
    if node.is_file() and os.access(node, os.X_OK) and client.is_file() and os.access(client, os.X_OK):
        print("[exp4] release binaries already present; skip cargo build", flush=True)
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
    print("[exp4] controller monorepo prepared", flush=True)


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
    print(f"[exp4] prepare start host={host}", flush=True)
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
    print(f"[exp4] prepare done host={host}", flush=True)
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
    if skip_prepare:
        print("[exp4] skip prepare", flush=True)
        print("[exp4] phase prepare: skipped", flush=True)
        return

    remote_prep = f"/tmp/prepare_repo_exp4_{int(time.time())}.sh"
    jobs = []
    for h in hosts:
        host = h["hostname"]
        user = h.get("username") or username
        jobs.append((host, user))

    total = len(jobs)
    print(f"[exp4] phase prepare: {total} hosts (parallel)", flush=True)
    print(
        f"[exp4] parallel prepare on {total} hosts "
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
                progress("exp4", completed, total, f"prepare done host={host}")
            except Exception as exc:  # noqa: BLE001
                failures.append((host, str(exc)))
                completed += 1
                progress("exp4", completed, total, f"prepare FAILED host={host}")
                print(f"[exp4] prepare FAILED host={host}: {exc}", file=sys.stderr, flush=True)

    if failures:
        detail = "; ".join(f"{h}: {err}" for h, err in failures)
        raise SystemExit(f"prepare failed on {len(failures)} host(s): {detail}")

    print("[exp4] phase prepare: complete", flush=True)
    print("[exp4] all replicas prepared; applying delay profile and running experiments", flush=True)


def switch_wan(bench_dir: Path, settings_path: Path, py: str, *, network_tag: str) -> None:
    print(f"[exp4] apply delay profile network={network_tag}", flush=True)
    code = f"""
from benchmark.cloudlab_wan import CloudLabWan
w = CloudLabWan(settings_file={str(settings_path)!r})
w.clear()
w.setup()
print('WAN profile applied:', {network_tag!r}, {str(settings_path)!r})
"""
    run([py, "-c", code], cwd=bench_dir)


def expand_cells(suite: dict, defaults: dict) -> list[dict]:
    """Expand a Figure12 suite into one CloudLabBench cell per input rate."""
    rates = list(suite.get("rates", defaults.get("rates") or []))
    if not rates:
        raise SystemExit("suite/defaults must define rates")
    runs = int(suite.get("runs", defaults.get("runs", 2)))
    duration = int(suite.get("duration", defaults["duration"]))
    nodes = int(suite.get("nodes", defaults["nodes"]))
    workers = int(suite.get("workers", defaults["workers"]))
    tx_size = int(suite.get("tx_size", defaults["tx_size"]))

    node_params = dict(defaults.get("node_params_shared") or {})
    if suite.get("node_params"):
        node_params.update(suite["node_params"])
    else:
        for key in (
            "enable_fast_coin",
            "solid_commit_trigger_on_solid_step",
            "enable_commit_recheck",
        ):
            if key in suite:
                node_params[key] = suite[key]

    node_params.update(
        {
            "sigma": int(suite.get("sigma", defaults["sigma"])),
            "kappa": int(suite.get("kappa", defaults["kappa"])),
            "reference": int(suite.get("reference", defaults["reference"])),
            "coverage": int(suite.get("coverage", defaults["coverage"])),
            "design_tag": suite["design_tag"],
            "network_tag": suite["network_tag"],
            "load_tag": suite["load_tag"],
        }
    )

    cells: list[dict] = []
    for rate in rates:
        cells.append(
            {
                "variant": suite["variant"],
                "rate": int(rate),
                "runs": runs,
                "duration": duration,
                "nodes": nodes,
                "workers": workers,
                "tx_size": tx_size,
                "node_params": dict(node_params),
                "label": f"r{int(rate)}",
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

    runner = bench_dir / "_exp4_cell_runner.py"
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


def parse_resource_summary(text: str) -> list[dict]:
    """Parse per-host averages from resource_usage_summary.txt."""
    hosts: list[dict] = []
    current: dict | None = None
    skip_prefixes = (
        "Resource usage",
        "Generated at",
        "Total rows",
        "No valid",
    )
    for line in text.splitlines():
        raw = line.rstrip()
        if not raw:
            continue
        if not raw.startswith((" ", "\t")):
            if raw.startswith(skip_prefixes):
                continue
            # Host header line like "ubuntu@10.10.1.1:22" or "10.10.1.1".
            if current and "cpu_avg_pct" in current:
                hosts.append(current)
            current = {"host": raw.strip()}
            continue
        if current is None:
            continue
        m = CPU_RE.search(raw)
        if m:
            current["cpu_avg_pct"] = float(m.group(1))
            current["cpu_max_pct"] = float(m.group(2))
            continue
        m = RX_RE.search(raw)
        if m:
            current["rx_avg_mbps"] = float(m.group(1))
            current["rx_max_mbps"] = float(m.group(2))
            continue
        m = TX_RE.search(raw)
        if m:
            current["tx_avg_mbps"] = float(m.group(1))
            current["tx_max_mbps"] = float(m.group(2))
            continue
    if current and "cpu_avg_pct" in current:
        hosts.append(current)
    return hosts


def mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else float("nan")


def wipe_bench_artifacts(bench_dir: Path, *, logs_only: bool = False, tag: str = "exp4") -> None:
    """Remove leftover bench outputs so collect/plot never mix prior runs."""
    targets = ["logs"] if logs_only else [
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


def collect_figure12_suite(bench_dir: Path, suite: dict, out_dir: Path) -> tuple[int, list[dict]]:
    """Copy summary + resource summaries; return (n_summaries, resource rows)."""
    design = suite["design_tag"]
    network = suite["network_tag"]
    load = suite["load_tag"]
    variant = suite["variant"]
    root = bench_dir / "manta_result" / design / network / load
    out_dir.mkdir(parents=True, exist_ok=True)
    resource_rows: list[dict] = []
    count = 0

    if not root.exists():
        print(f"[exp4] no result root yet: {root}", flush=True)
        return 0, resource_rows

    for summary in sorted(root.rglob("summary.txt")):
        run_dir = summary.parent
        meta = _meta_from_dirname(run_dir.name)
        meta.update({k: v for k, v in _meta_from_run_dir(run_dir).items() if v is not None})
        rate = int(meta.get("rate", 0))
        run_idx = int(meta.get("run", 0))
        if rate <= 0:
            print(f"[exp4] skip (missing rate): {summary}", flush=True)
            continue

        text = summary.read_text(encoding="utf-8", errors="replace")
        tps, lat = parse_summary_metrics(text)
        if tps is None or lat is None:
            print(f"[exp4] skip (missing metrics): {summary}", flush=True)
            continue

        flat = f"{run_dir.name}.txt" if run_dir.name else summary.name
        dest = out_dir / flat
        shutil.copy2(summary, dest)
        count += 1
        print(f"[exp4] Figure12/{variant} collected {dest.name}", flush=True)

        res_path = run_dir / "resource_usage_summary.txt"
        if res_path.is_file():
            # Keep a copy next to the summary for Table2 sync.
            shutil.copy2(res_path, out_dir / f"{run_dir.name}.resource_usage_summary.txt")
            host_stats = parse_resource_summary(res_path.read_text(encoding="utf-8", errors="replace"))
            if host_stats:
                resource_rows.append(
                    {
                        "variant": variant,
                        "input_rate": rate,
                        "run": run_idx,
                        "cpu_avg_pct": mean([h["cpu_avg_pct"] for h in host_stats]),
                        "cpu_max_pct": mean([h["cpu_max_pct"] for h in host_stats]),
                        "rx_avg_mbps": mean([h["rx_avg_mbps"] for h in host_stats]),
                        "tx_avg_mbps": mean([h["tx_avg_mbps"] for h in host_stats]),
                        "n_hosts": len(host_stats),
                        "source": str(res_path),
                    }
                )
            else:
                print(f"[exp4] resource summary empty/unparsed: {res_path}", flush=True)
        else:
            print(f"[exp4] missing resource_usage_summary.txt under {run_dir}", flush=True)

    return count, resource_rows


def write_table2(results_root: Path, rows: list[dict]) -> None:
    """Write per-run and mean-over-runs Table 2 CSVs plus a markdown summary."""
    out_dir = results_root / "Table2"
    out_dir.mkdir(parents=True, exist_ok=True)
    per_run = out_dir / "resource_summary.csv"
    mean_path = out_dir / "table2_mean_over_runs.csv"
    md_path = out_dir / "table2.md"

    fieldnames = [
        "variant",
        "input_rate",
        "run",
        "cpu_avg_pct",
        "cpu_max_pct",
        "rx_avg_mbps",
        "tx_avg_mbps",
        "n_hosts",
    ]
    with per_run.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r["variant"], r["input_rate"], r["run"])):
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["variant"], int(row["input_rate"]))].append(row)

    mean_rows: list[dict] = []
    mean_fields = [
        "variant",
        "input_rate",
        "n_runs",
        "cpu_avg_pct",
        "cpu_max_pct",
        "rx_avg_mbps",
        "tx_avg_mbps",
    ]
    for (variant, rate), group in sorted(grouped.items()):
        mean_rows.append(
            {
                "variant": variant,
                "input_rate": rate,
                "n_runs": len(group),
                "cpu_avg_pct": round(mean([g["cpu_avg_pct"] for g in group]), 3),
                "cpu_max_pct": round(mean([g["cpu_max_pct"] for g in group]), 3),
                "rx_avg_mbps": round(mean([g["rx_avg_mbps"] for g in group]), 3),
                "tx_avg_mbps": round(mean([g["tx_avg_mbps"] for g in group]), 3),
            }
        )

    with mean_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=mean_fields)
        writer.writeheader()
        for row in mean_rows:
            writer.writerow(row)

    lines = [
        "| variant | input_rate | CPU avg % | CPU max % | RX avg Mbps | TX avg Mbps |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in mean_rows:
        lines.append(
            f"| {row['variant']} | {row['input_rate']} | {row['cpu_avg_pct']:.2f} | "
            f"{row['cpu_max_pct']:.2f} | {row['rx_avg_mbps']:.2f} | {row['tx_avg_mbps']:.2f} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[exp4] wrote {per_run} ({len(rows)} rows)", flush=True)
    print(f"[exp4] wrote {mean_path} ({len(mean_rows)} rows)", flush=True)
    print(f"[exp4] wrote {md_path}", flush=True)
    print("[exp4] Table 2 (mean over runs):\n" + "\n".join(lines), flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Controller Experiment 4 / Figure 12 runner")
    p.add_argument("--workdir", type=Path, required=True)
    p.add_argument("--settings", type=Path, required=True)
    p.add_argument("--matrix", type=Path, required=True)
    p.add_argument("--monorepo", type=Path, required=True)
    p.add_argument("--remote-key", required=True)
    p.add_argument("--only-suite", choices=SUITE_CHOICES, default=None)
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
    branch = matrix.get("branch", "experiment4")
    monorepo_name = matrix.get("monorepo_name", "manta-nsdi27")
    port = int(settings.get("port", 5000))
    password = settings.get("ssh_key_password") or (settings.get("key") or {}).get("password")
    defaults = matrix.get("defaults") or {}

    ensure_monorepo(args.monorepo, repo_url, branch)
    if not args.skip_prepare:
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
    print(f"[exp4] phase wan: apply {network['tag']} ...", flush=True)
    switch_wan(bench_dir, bench_dir / "cloudlab_settings.json", py, network_tag=network["tag"])
    print("[exp4] phase wan: complete", flush=True)

    suites = matrix["suites"]
    if args.only_suite:
        suites = {args.only_suite: suites[args.only_suite]}

    suite_cells: dict[str, list[dict]] = {}
    total_cells = 0
    for suite_name, suite in suites.items():
        cells = expand_cells(suite, defaults)
        suite_cells[suite_name] = cells
        total_cells += len(cells)
    print(
        f"[exp4] phase bench: plan total_cells={total_cells} suites={list(suites)}",
        flush=True,
    )
    cell_i = 0

    all_resource_rows: list[dict] = []
    for suite_name, suite in suites.items():
        cells = suite_cells[suite_name]
        print(f"[exp4] suite={suite_name} cells={len(cells)}", flush=True)
        wipe_bench_artifacts(bench_dir, logs_only=False, tag="exp4")
        for cell in cells:
            cell_i += 1
            progress(
                "exp4",
                cell_i,
                total_cells,
                f"START suite={suite_name} variant={cell['variant']} rate={cell['rate']}",
            )
            print(
                f"[exp4] run suite={suite_name} variant={cell['variant']} "
                f"rate={cell['rate']} runs={cell['runs']}",
                flush=True,
            )
            wipe_bench_artifacts(bench_dir, logs_only=True, tag="exp4")
            run_cell(
                bench_dir=bench_dir,
                settings_path=wan_settings,
                cell=cell,
                py=py,
                password=password,
            )
            progress(
                "exp4",
                cell_i,
                total_cells,
                f"DONE suite={suite_name} variant={cell['variant']} rate={cell['rate']}",
            )

        variant = suite["variant"]
        out_dir = results_root / "Figure12" / variant
        print(f"[exp4] phase collect: Figure12/{variant} ...", flush=True)
        n, resource_rows = collect_figure12_suite(bench_dir, suite, out_dir)
        all_resource_rows.extend(resource_rows)
        print(f"[exp4] Figure12/{variant}: collected {n} summaries into {out_dir}", flush=True)
        print(f"[exp4] phase collect: complete ({n} summaries)", flush=True)

    print("[exp4] phase collect: Table2 ...", flush=True)
    write_table2(results_root, all_resource_rows)
    print("[exp4] phase collect: Table2 complete", flush=True)
    print("[exp4] phase all: complete", flush=True)
    print("[exp4] done", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"[exp4] command failed: {exc}", file=sys.stderr)
        raise SystemExit(exc.returncode)
