#!/usr/bin/env python3
"""Run a lightweight local functional test for the artifact.

The test checks that the local environment can build an implementation branch
and run a small local deployment. It is intentionally small and does not
attempt to reproduce paper-scale performance results.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from urllib.parse import urlparse
from pathlib import Path


IMPLEMENTATION_BRANCHES = {
    "manta": "manta",
    "tusk": "tusk",
    "mahi-mahi": "mahi-mahi",
    "chitu": "chitu",
}

BENCHMARK_TMUX_PREFIXES = ("client-", "primary-", "worker-")
BENCHMARK_PROCESS_PATTERNS = (
    "[.]/benchmark_client",
    "[.]/node .*run --keys .*primary",
    "[.]/node .*run --keys .*worker",
)
PROXY_ENV_NAMES = (
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "all_proxy",
    "ALL_PROXY",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent


def command_exists(command: str, env: dict[str, str]) -> bool:
    return shutil.which(command, path=env.get("PATH")) is not None


def sanitize_proxy_env(env: dict[str, str]) -> str | None:
    for name in PROXY_ENV_NAMES:
        value = env.get(name)
        if not value:
            continue

        parsed = urlparse(value)
        host = parsed.hostname
        port = parsed.port
        if host not in {"127.0.0.1", "localhost", "::1"} or port is None:
            continue

        try:
            with socket.create_connection((host, port), timeout=1):
                return None
        except OSError:
            for proxy_name in PROXY_ENV_NAMES:
                env.pop(proxy_name, None)
            return f"disabled unreachable local proxy {host}:{port}"

    return None


def run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int | None = None,
    log_file: Path | None = None,
    verbose: bool = False,
) -> None:
    printable = " ".join(command)
    output = None
    try:
        if log_file is not None and not verbose:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            output = log_file.open("a", encoding="utf-8")
            output.write(f"\n[functional-test] $ {printable}\n")
            output.flush()
            stdout = output
            stderr = subprocess.STDOUT
        else:
            print(f"[functional-test] $ {printable}", flush=True)
            stdout = None
            stderr = None

        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise subprocess.TimeoutExpired(command, timeout) from exc

        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, command)
    finally:
        if output is not None:
            output.close()


def check_environment(
    root: Path,
    env: dict[str, str],
    *,
    log_file: Path | None,
    verbose: bool,
) -> None:
    required_commands = ["git", "rustc", "cargo", "python3"]
    missing = [command for command in required_commands if not command_exists(command, env)]
    if missing:
        raise RuntimeError(
            "missing required command(s): "
            + ", ".join(missing)
            + ". Please run ./scripts/environment_setup.sh first."
        )

    venv_python = root / "venv" / "bin" / "python"
    if venv_python.exists():
        run(
            [
                str(venv_python),
                "-c",
                "import boto3, fabric, matplotlib; print('Python benchmark packages: OK')",
            ],
            cwd=root,
            env=env,
            timeout=30,
            log_file=log_file,
            verbose=verbose,
        )


def cleanup_benchmark_tmux_sessions(env: dict[str, str]) -> None:
    if not command_exists("tmux", env):
        return

    sessions = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if sessions.returncode != 0:
        return

    for session_name in sessions.stdout.splitlines():
        if session_name.startswith(BENCHMARK_TMUX_PREFIXES):
            subprocess.run(
                ["tmux", "kill-session", "-t", session_name],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

    time.sleep(1)


def benchmark_tmux_sessions(env: dict[str, str]) -> list[str]:
    if not command_exists("tmux", env):
        return []

    sessions = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if sessions.returncode != 0:
        return []

    return [
        session_name
        for session_name in sessions.stdout.splitlines()
        if session_name.startswith(BENCHMARK_TMUX_PREFIXES)
    ]


def benchmark_processes(env: dict[str, str]) -> list[str]:
    running = []
    for pattern in BENCHMARK_PROCESS_PATTERNS:
        result = subprocess.run(
            ["pgrep", "-af", pattern],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            running.extend(result.stdout.splitlines())
    return running


def cleanup_benchmark_processes(env: dict[str, str]) -> None:
    cleanup_benchmark_tmux_sessions(env)

    for pattern in BENCHMARK_PROCESS_PATTERNS:
        subprocess.run(
            ["pkill", "-TERM", "-f", pattern],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    time.sleep(2)

    for pattern in BENCHMARK_PROCESS_PATTERNS:
        subprocess.run(
            ["pkill", "-KILL", "-f", pattern],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    cleanup_benchmark_tmux_sessions(env)


def ensure_benchmark_clean(env: dict[str, str], timeout: int = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        cleanup_benchmark_processes(env)
        sessions = benchmark_tmux_sessions(env)
        processes = benchmark_processes(env)
        if not sessions and not processes:
            return
        time.sleep(1)

    sessions = benchmark_tmux_sessions(env)
    processes = benchmark_processes(env)
    details = []
    if sessions:
        details.append("tmux sessions: " + ", ".join(sessions))
    if processes:
        details.append("processes: " + " | ".join(processes))
    raise RuntimeError("benchmark cleanup did not finish; " + "; ".join(details))


def make_runner(worktree: Path, nodes: int, rate: int, duration: int) -> Path:
    runner = worktree / "benchmark" / "_functional_test_runner.py"
    runner.write_text(
        textwrap.dedent(
            f"""
            from benchmark.local import LocalBench

            bench_params = {{
                'faults': 0,
                'nodes': {nodes},
                'workers': 1,
                'rate_type': 'balanced',
                'rate': {rate},
                'tx_size': 512,
                'duration': {duration},
                'runs': 1,
            }}

            node_params = {{
                'header_size': 1000,
                'max_header_delay': 200,
                'gc_depth': 50,
                'sync_retry_delay': 1000,
                'sync_retry_nodes': max(1, {nodes} - 1),
                'batch_size': 500_000,
                'max_batch_delay': 200,
                'sigma': 1,
                'kappa': 3,
                'reference': min(4, {nodes}),
                'coverage': max(1, {nodes} - 1),
                's': 0.99,
                'allow_cross_step_weak_edges': True,
                'enable_fast_coin': False,
                'solid_commit_trigger_on_solid_step': False,
                'fast_coin_candidate_threshold': 0,
                'solid_candidate_threshold': 0,
                'enable_wait': True,
            }}

            result = LocalBench(bench_params, node_params).run(debug=False)
            if hasattr(result, 'result'):
                print(result.result())
            print('FUNCTIONAL_TEST_OK')
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return runner


def test_branch(
    root: Path,
    implementation_name: str,
    branch_name: str,
    *,
    temp_root: Path,
    log_dir: Path,
    env: dict[str, str],
    nodes: int,
    rate: int,
    duration: int,
    timeout: int | None,
    verbose: bool,
) -> None:
    worktree = temp_root / implementation_name.replace("/", "_")
    log_file = log_dir / f"{implementation_name}.log"
    log_file.write_text(
        f"[functional-test] branch={branch_name} nodes={nodes} rate={rate} duration={duration}\n",
        encoding="utf-8",
    )

    ensure_benchmark_clean(env)
    run(
        ["git", "worktree", "add", "--detach", str(worktree), branch_name],
        cwd=root,
        env=env,
        log_file=log_file,
        verbose=verbose,
    )

    try:
        runner = make_runner(worktree, nodes, rate, duration)
        python = root / "venv" / "bin" / "python"
        python_cmd = str(python) if python.exists() else sys.executable
        run(
            [python_cmd, str(runner)],
            cwd=worktree / "benchmark",
            env=env,
            timeout=timeout,
            log_file=log_file,
            verbose=verbose,
        )
    finally:
        ensure_benchmark_clean(env)
        try:
            run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=root,
                env=env,
                timeout=60,
                log_file=log_file,
                verbose=verbose,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            shutil.rmtree(worktree, ignore_errors=True)
            run(
                ["git", "worktree", "prune"],
                cwd=root,
                env=env,
                timeout=60,
                log_file=log_file,
                verbose=verbose,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a lightweight local functional test. By default this tests the "
            "main Manta implementation; use --all to test every implementation branch."
        )
    )
    parser.add_argument(
        "--branch",
        dest="branches",
        action="append",
        choices=sorted(IMPLEMENTATION_BRANCHES),
        help="Implementation to test. May be passed multiple times.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Test all implementation branches.",
    )
    parser.add_argument("--nodes", type=int, default=4, help="Local node count.")
    parser.add_argument("--rate", type=int, default=1_000, help="Input transaction rate.")
    parser.add_argument("--duration", type=int, default=10, help="Run duration in seconds.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Optional per-branch timeout in seconds, including build time.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print build and benchmark output instead of writing it only to logs.",
    )
    return parser.parse_args()


def selected_implementations(args: argparse.Namespace) -> list[str]:
    if args.all:
        return list(IMPLEMENTATION_BRANCHES)
    return args.branches or ["manta"]


def main() -> int:
    args = parse_args()
    root = repo_root()

    if args.nodes < 4:
        print("[functional-test] error: --nodes must be at least 4", file=sys.stderr)
        return 2

    env = os.environ.copy()
    cargo_bin = Path.home() / ".cargo" / "bin"
    venv_bin = root / "venv" / "bin"
    env["PATH"] = f"{venv_bin}:{cargo_bin}:{env.get('PATH', '')}"
    proxy_note = sanitize_proxy_env(env)
    if proxy_note:
        print(f"[functional-test] proxy: {proxy_note}", flush=True)

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = root / "functional_test_results" / timestamp
    log_dir.mkdir(parents=True, exist_ok=True)

    try:
        check_environment(root, env, log_file=log_dir / "environment.log", verbose=args.verbose)

        selected = selected_implementations(args)
        failures = []

        with tempfile.TemporaryDirectory(prefix="manta-functional-test-") as tmp:
            temp_root = Path(tmp)
            for implementation_name in selected:
                branch_name = IMPLEMENTATION_BRANCHES[implementation_name]
                log_file = log_dir / f"{implementation_name}.log"
                print(f"[functional-test] {implementation_name}: RUNNING", flush=True)
                try:
                    test_branch(
                        root,
                        implementation_name,
                        branch_name,
                        temp_root=temp_root,
                        log_dir=log_dir,
                        env=env,
                        nodes=args.nodes,
                        rate=args.rate,
                        duration=args.duration,
                        timeout=args.timeout,
                        verbose=args.verbose,
                    )
                    print(f"[functional-test] {implementation_name}: PASS")
                except (RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                    failures.append((implementation_name, exc, log_file))
                    print(f"[functional-test] {implementation_name}: FAIL")

        if failures:
            print(f"\n[functional-test] Logs: {log_dir}")
            for implementation_name, exc, log_file in failures:
                print(f"[functional-test] {implementation_name}: {exc} (log: {log_file})")
            return 1

        print(f"\n[functional-test] All selected functional tests passed. Logs: {log_dir}")
        return 0
    except (RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print("[functional-test] environment: FAIL")
        print(f"\n[functional-test] Logs: {log_dir}")
        print(f"[functional-test] environment: {exc} (log: {log_dir / 'environment.log'})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
