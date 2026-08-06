#!/usr/bin/env python3
"""Deploy Getting Started on all CloudLab nodes via the controller.

Flow (all driven from the controller, the last host in ``build/nodes``):

1. Getting Started on every node: clone, check out the artifact branch, run
   ``scripts/environment_setup.sh``.
2. CloudLab remote functional test: run a short ``CloudLabBench`` experiment
   for the ``tusk`` branch on the replica ``hosts`` from
   ``cloudlab_settings.json`` (10 nodes, 10s duration by default) over the
   experiment LAN (``10.10.1.0/24``).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path


DEFAULT_SETTINGS = Path("cloudlab_settings.json")
DEFAULT_BUILD_DIR = Path("build")

IMPLEMENTATION_BRANCHES = {
    "tusk": "tusk",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy Getting Started and run CloudLab remote functional tests via the controller."
    )
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    parser.add_argument("--username")
    parser.add_argument("--key", type=Path, help="SSH private key path.")
    parser.add_argument("--connect-timeout", type=int, default=20)
    parser.add_argument("--nodes", type=int, default=10, help="Functional-test consensus node count.")
    parser.add_argument("--duration", type=int, default=10, help="Functional-test duration in seconds.")
    parser.add_argument("--rate", type=int, default=1_000, help="Functional-test input rate.")
    parser.add_argument(
        "--skip-functional-test",
        action="store_true",
        help="Only run Getting Started setup; skip cloudlab_remote functional tests.",
    )
    parser.add_argument(
        "--skip-getting-started",
        action="store_true",
        help="Skip per-node Getting Started setup; only run cloudlab_remote functional tests.",
    )
    return parser.parse_args()


def load_settings(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"settings file does not exist: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("cloudlab", data)


def nested(settings: dict, *keys, default=None):
    value = settings
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def default_key(settings: dict) -> Path:
    explicit = nested(settings, "key", "private") or nested(settings, "key", "path")
    if explicit:
        return Path(explicit).expanduser()
    pubkey = nested(settings, "key", "pubkey")
    if pubkey and str(pubkey).endswith(".pub"):
        return Path(str(pubkey)[:-4]).expanduser()
    return Path("~/.ssh/id_rsa").expanduser()


def default_username(settings: dict) -> str:
    hosts = settings.get("hosts", [])
    if hosts and isinstance(hosts[0], dict) and hosts[0].get("username"):
        return hosts[0]["username"]
    return "ubuntu"


def read_nodes(path: Path) -> list[str]:
    if not path.exists():
        raise SystemExit(f"node list does not exist: {path}")
    nodes = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not nodes:
        raise SystemExit(f"node list is empty: {path}")
    return nodes


def ensure_ssh_agent(key: Path, password: str | None = None) -> None:
    listed = subprocess.run(
        ["ssh-add", "-l"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if listed.returncode == 2:
        agent = subprocess.run(
            ["ssh-agent", "-s"],
            check=True,
            capture_output=True,
            text=True,
        )
        for line in agent.stdout.splitlines():
            if line.startswith("SSH_AUTH_SOCK=") or line.startswith("SSH_AGENT_PID="):
                assignment = line.split(";")[0]
                name, value = assignment.split("=", 1)
                os.environ[name] = value

    check = subprocess.run(["ssh-add", "-l"], capture_output=True, text=True, check=False)
    if str(key) in check.stdout or key.name in check.stdout:
        return

    env = os.environ.copy()
    askpass_path = None
    if password:
        import tempfile

        askpass = tempfile.NamedTemporaryFile("w", delete=False, prefix="manta-askpass-")
        askpass.write("#!/bin/sh\n")
        askpass.write(f"echo {shlex.quote(password)}\n")
        askpass.close()
        askpass_path = askpass.name
        os.chmod(askpass_path, 0o700)
        env["SSH_ASKPASS"] = askpass_path
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env["DISPLAY"] = env.get("DISPLAY") or ":0"
        env.pop("SSH_AUTH_SOCK_ASKPASS", None)

    try:
        added = subprocess.run(
            ["ssh-add", str(key)],
            check=False,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    finally:
        if askpass_path:
            try:
                os.unlink(askpass_path)
            except OSError:
                pass

    if added.returncode != 0:
        detail = (added.stderr or "").strip()
        raise SystemExit(
            f"failed to add SSH key to agent: {key}"
            + (f" ({detail})" if detail else "")
            + ". Set ssh_key_password in cloudlab_settings.json for encrypted keys."
        )


def fab_settings(settings: dict, branch: str, remote_key_path: str) -> dict:
    hosts = settings.get("hosts")
    if not isinstance(hosts, list) or not hosts:
        raise SystemExit("cloudlab_settings.json hosts must be a non-empty list")
    repo_url = nested(settings, "repo", "url")
    repo_name = nested(settings, "repo", "name", default="manta-nsdi27")
    if not repo_url:
        raise SystemExit("repo.url is required in cloudlab_settings.json")
    data = {
        "key": {"path": remote_key_path},
        "port": settings.get("port", 5000),
        "repo": {"name": repo_name, "url": repo_url, "branch": branch},
        "hosts": hosts,
    }
    # CloudLabBench reads top-level ssh_key_password for encrypted private keys.
    password = settings.get("ssh_key_password") or nested(settings, "key", "password")
    if password:
        data["ssh_key_password"] = password
    return data


def node_setup_script(repo_url: str, branch: str, repo_name: str) -> str:
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -euxo pipefail
        export PATH="$HOME/.cargo/bin:$PATH"
        repo_dir="$HOME/{repo_name}"
        if [ ! -d "$repo_dir/.git" ]; then
          git clone {shlex.quote(repo_url)} "$repo_dir"
        fi
        cd "$repo_dir"
        git fetch --tags origin
        git checkout {shlex.quote(branch)}
        git pull --ff-only origin {shlex.quote(branch)} || true
        chmod +x ./scripts/environment_setup.sh
        ./scripts/environment_setup.sh
        """
    )


def branch_runner_script(nodes: int, rate: int, duration: int) -> str:
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import json
        import os
        import sys
        from pathlib import Path
        from types import SimpleNamespace

        from benchmark.cloudlab_remote import CloudLabBench
        from benchmark.utils import BenchError, Print

        class ConnectKwargs(dict):
            # dict that also accepts Fabric-style attribute assignment.
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

        # CloudLabBench expects ctx.connect_kwargs like Fabric provides.
        settings_path = Path(__file__).resolve().parent / "cloudlab_settings.json"
        if settings_path.exists():
            settings_data = json.loads(settings_path.read_text(encoding="utf-8"))
            password = settings_data.get("ssh_key_password") or ""
            if password and not os.environ.get("SSH_KEY_PASSWORD"):
                os.environ["SSH_KEY_PASSWORD"] = password

        ctx = SimpleNamespace(connect_kwargs=ConnectKwargs())

        bench_params = {{
            "faults": 0,
            "nodes": [{nodes}],
            "workers": 1,
            "collocate": True,
            "rate_type": "balanced",
            "rate": [{rate}],
            "tx_size": 512,
            "duration": {duration},
            "runs": 1,
        }}
        node_params = {{
            "header_size": 1000,
            "max_header_delay": 200,
            "gc_depth": 50,
            "sync_retry_delay": 1000,
            "sync_retry_nodes": max(1, {nodes} - 1),
            "batch_size": 500_000,
            "max_batch_delay": 200,
            "sigma": 1,
            "kappa": 3,
            "reference": min(4, {nodes}),
            "coverage": max(1, {nodes} - 1),
            "s": 0.99,
            "allow_cross_step_weak_edges": True,
            "enable_fast_coin": False,
            "solid_commit_trigger_on_solid_step": False,
            "fast_coin_candidate_threshold": 0,
            "solid_candidate_threshold": 0,
            "enable_wait": True,
        }}

        try:
            CloudLabBench(ctx).run(bench_params, node_params, False)
        except BenchError as exc:
            Print.error(exc)
            sys.exit(1)
        """
    )


def functional_orchestrator_py(names: list[str], git_branches: list[str]) -> str:
    return textwrap.dedent(
        f"""\
        import json
        import os
        import shutil
        import subprocess
        import sys
        from pathlib import Path

        repo_dir = Path(sys.argv[1])
        workdir = Path(sys.argv[2])
        nodes = int(sys.argv[3])
        rate = int(sys.argv[4])
        duration = int(sys.argv[5])
        settings_by_branch = json.loads((workdir / "settings_by_branch.json").read_text())
        branches = list(zip({names!r}, {git_branches!r}))

        def run(cmd, cwd=None):
            print("[deploy]", " ".join(cmd), flush=True)
            return subprocess.run(cmd, cwd=cwd, check=False)

        failures = []
        venv_python = repo_dir / "venv" / "bin" / "python"
        py = str(venv_python) if venv_python.exists() else "python3"
        env = os.environ.copy()
        env["PATH"] = f"{{repo_dir / 'venv' / 'bin'}}:{{Path.home() / '.cargo' / 'bin'}}:{{env.get('PATH', '')}}"
        # Prefer env var that CloudLabBench checks first for encrypted keys.
        for sample in settings_by_branch.values():
            password = sample.get("ssh_key_password") or ""
            if password:
                env["SSH_KEY_PASSWORD"] = password
                break

        for name, git_branch in branches:
            print(
                f"[deploy] functional {{name}}: RUNNING "
                f"(branch={{git_branch}}, nodes={{nodes}}, duration={{duration}}s)",
                flush=True,
            )
            wt = workdir / f"wt-{{git_branch}}"
            if wt.exists():
                shutil.rmtree(wt)
            add = run(["git", "worktree", "add", "--force", str(wt), git_branch], cwd=repo_dir)
            if add.returncode != 0:
                add = run(
                    ["git", "worktree", "add", "--force", str(wt), f"origin/{{git_branch}}"],
                    cwd=repo_dir,
                )
            if add.returncode != 0:
                print(f"[deploy] functional {{name}}: FAIL (worktree)", flush=True)
                failures.append(name)
                continue

            bench = wt / "benchmark"
            if not bench.is_dir():
                print(f"[deploy] functional {{name}}: FAIL (no benchmark/)", flush=True)
                failures.append(name)
                continue

            (bench / "cloudlab_settings.json").write_text(
                json.dumps(settings_by_branch[name], indent=2) + "\\n",
                encoding="utf-8",
            )
            runner_path = bench / "_cloudlab_functional_runner.py"
            runner_path.write_text(
                (workdir / "run_branch.py").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            result = subprocess.run([py, str(runner_path)], cwd=str(bench), env=env, check=False)
            if result.returncode == 0:
                print(f"[deploy] functional {{name}}: PASS", flush=True)
            else:
                print(f"[deploy] functional {{name}}: FAIL", flush=True)
                failures.append(name)
            run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo_dir)

        if failures:
            print("[deploy] functional failed: " + ", ".join(failures), flush=True)
            sys.exit(1)
        print("[deploy] CloudLab remote functional tests passed on all branches.", flush=True)
        """
    )


def controller_driver_script(
    *,
    username: str,
    nodes: list[str],
    node_setup: str,
    connect_timeout: int,
    repo_name: str,
    repo_url: str,
    remote_key_path: str,
    settings_by_branch: dict[str, dict],
    ft_nodes: int,
    ft_rate: int,
    ft_duration: int,
    run_getting_started: bool,
    run_functional_test: bool,
) -> str:
    nodes_literal = "\n".join(nodes)
    branch_list = " ".join(shlex.quote(b) for b in IMPLEMENTATION_BRANCHES.values())
    names = list(IMPLEMENTATION_BRANCHES.keys())
    git_branches = list(IMPLEMENTATION_BRANCHES.values())
    settings_blob = json.dumps(settings_by_branch)
    runner = branch_runner_script(ft_nodes, ft_rate, ft_duration)
    orchestrator = functional_orchestrator_py(names, git_branches)

    setup_block = ""
    if run_getting_started:
        setup_block = f"""
cat > "$workdir/node_setup.sh" <<'EOF_NODE_SETUP'
{node_setup}
EOF_NODE_SETUP
chmod +x "$workdir/node_setup.sh"

mapfile -t NODES <<'EOF_NODES'
{nodes_literal}
EOF_NODES

echo "[deploy] controller=$(hostname -f 2>/dev/null || hostname)"
echo "[deploy] deploying Getting Started to ${{#NODES[@]}} node(s)"

pids=()
for host in "${{NODES[@]}}"; do
  log="$workdir/$host.log"
  echo "[deploy] starting: $host"
  (
    ssh -o BatchMode=yes \\
        -o StrictHostKeyChecking=accept-new \\
        -o "ConnectTimeout=$CONNECT_TIMEOUT" \\
        "$USERNAME@$host" \\
        "bash -s" < "$workdir/node_setup.sh" \\
      >"$log" 2>&1
    rc=$?
    if [ "$rc" -eq 0 ]; then
      echo "[deploy] OK: $host"
    else
      echo "[deploy] FAIL: $host" >&2
      tail -n 50 "$log" >&2 || true
    fi
    exit "$rc"
  ) &
  pids+=("$!")
done

failed=0
for pid in "${{pids[@]}}"; do
  if ! wait "$pid"; then
    failed=1
  fi
done

if [ "$failed" -ne 0 ]; then
  echo "[deploy] one or more nodes failed Getting Started setup" >&2
  exit 1
fi
echo "[deploy] Getting Started completed on all nodes"
"""
    else:
        setup_block = """
echo "[deploy] skipping Getting Started setup"
"""

    functional_block = ""
    if run_functional_test:
        functional_block = f"""
echo "[deploy] starting CloudLab remote functional tests on replica hosts"
cd "$HOME/{repo_name}"
git fetch --tags origin
for b in {branch_list}; do
  git fetch origin "$b:$b" 2>/dev/null || git fetch origin "$b" || true
done

ft_workdir="$(mktemp -d /tmp/manta-cloudlab-ft.XXXXXX)"
ft_cleanup() {{ rm -rf "$ft_workdir"; }}
trap ft_cleanup EXIT

python3 -c 'import json,sys; json.dump(json.loads(sys.argv[1]), open(sys.argv[2],"w"), indent=2)' \\
  {shlex.quote(settings_blob)} "$ft_workdir/settings_by_branch.json"

cat > "$ft_workdir/run_branch.py" <<'EOF_RUNNER'
{runner}
EOF_RUNNER

cat > "$ft_workdir/orchestrator.py" <<'EOF_ORCH'
{orchestrator}
EOF_ORCH

chmod 600 {shlex.quote(remote_key_path)} 2>/dev/null || true
python3 "$ft_workdir/orchestrator.py" "$HOME/{repo_name}" "$ft_workdir" {int(ft_nodes)} {int(ft_rate)} {int(ft_duration)}
"""

    return f"""#!/usr/bin/env bash
set -euo pipefail
USERNAME={shlex.quote(username)}
CONNECT_TIMEOUT={int(connect_timeout)}
REPO_URL={shlex.quote(repo_url)}
REPO_NAME={shlex.quote(repo_name)}
workdir="$(mktemp -d /tmp/manta-deploy.XXXXXX)"
cleanup() {{ rm -rf "$workdir"; }}
trap cleanup EXIT

export PATH="$HOME/.cargo/bin:$PATH"
if [ -f "$HOME/$REPO_NAME/venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$HOME/$REPO_NAME/venv/bin/activate"
fi
{setup_block}
{functional_block}
echo "[deploy] done"
"""


def scp_to_controller(
    local: Path,
    remote_path: str,
    username: str,
    controller: str,
    key: Path,
    connect_timeout: int,
) -> None:
    command = [
        "scp",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"ConnectTimeout={connect_timeout}",
        "-o", "IdentitiesOnly=yes",
        "-i", str(key),
        str(local),
        f"{username}@{controller}:{remote_path}",
    ]
    if subprocess.run(command, check=False).returncode != 0:
        raise SystemExit(f"failed to scp {local} to {username}@{controller}:{remote_path}")


def main() -> int:
    args = parse_args()
    settings = load_settings(args.settings)
    username = args.username or default_username(settings)
    key = (args.key or default_key(settings)).expanduser()
    nodes = read_nodes(args.build_dir / "nodes")
    controller = nodes[-1]

    repo_url = nested(settings, "repo", "url")
    branch = nested(settings, "repo", "branch", default="artifact-evaluation")
    repo_name = nested(settings, "repo", "name", default="manta-nsdi27")
    if not repo_url:
        raise SystemExit("repo.url is required in cloudlab_settings.json")
    if "/" in repo_name or repo_name.startswith(".") or " " in repo_name:
        raise SystemExit(f"unsafe repo.name: {repo_name}")
    if not key.exists():
        raise SystemExit(f"SSH private key does not exist: {key}")

    hosts = settings.get("hosts", [])
    run_ft = not args.skip_functional_test
    run_setup = not args.skip_getting_started
    if not run_ft and not run_setup:
        raise SystemExit("nothing to do: both Getting Started and functional test are skipped")
    if run_ft and len(hosts) < args.nodes:
        raise SystemExit(
            f"cloudlab_settings.json hosts has {len(hosts)} entries, need at least {args.nodes}"
        )

    remote_key = f"/users/{username}/.ssh/manta_ae_functional"
    settings_by_branch = {
        name: fab_settings(settings, git_branch, remote_key)
        for name, git_branch in IMPLEMENTATION_BRANCHES.items()
    }

    args.build_dir.mkdir(parents=True, exist_ok=True)
    (args.build_dir / "controller").write_text(controller + "\n", encoding="utf-8")
    (args.build_dir / "head-node").write_text(controller + "\n", encoding="utf-8")

    print(f"[deploy] nodes: {len(nodes)}")
    print(f"[deploy] controller (last node): {controller}")
    print(f"[deploy] username: {username}")
    print(f"[deploy] repo: {repo_url} @ {branch}")
    print(f"[deploy] Getting Started: {'yes' if run_setup else 'skipped'}")
    if run_ft:
        print(
            f"[deploy] functional test: {len(IMPLEMENTATION_BRANCHES)} branches, "
            f"hosts from cloudlab_settings.json, nodes={args.nodes}, duration={args.duration}s"
        )
    else:
        print("[deploy] functional test: skipped")

    ensure_ssh_agent(
        key,
        password=settings.get("ssh_key_password") or nested(settings, "key", "password"),
    )

    if run_ft:
        prep = subprocess.run(
            [
                "ssh", "-A",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", f"ConnectTimeout={args.connect_timeout}",
                "-o", "IdentitiesOnly=yes",
                "-i", str(key),
                f"{username}@{controller}",
                "mkdir -p ~/.ssh && chmod 700 ~/.ssh",
            ],
            check=False,
        )
        if prep.returncode != 0:
            raise SystemExit("failed to prepare ~/.ssh on controller")
        scp_to_controller(key, remote_key, username, controller, key, args.connect_timeout)

    driver = controller_driver_script(
        username=username,
        nodes=nodes,
        node_setup=node_setup_script(repo_url, branch, repo_name),
        connect_timeout=args.connect_timeout,
        repo_name=repo_name,
        repo_url=repo_url,
        remote_key_path=remote_key,
        settings_by_branch=settings_by_branch,
        ft_nodes=args.nodes,
        ft_rate=args.rate,
        ft_duration=args.duration,
        run_getting_started=run_setup,
        run_functional_test=run_ft,
    )

    print(f"[deploy] connecting to controller {username}@{controller}")
    result = subprocess.run(
        [
            "ssh", "-A",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"ConnectTimeout={args.connect_timeout}",
            "-o", "IdentitiesOnly=yes",
            "-i", str(key),
            f"{username}@{controller}",
            "bash -s",
        ],
        input=driver,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("[deploy] failed", file=sys.stderr)
        return result.returncode
    print("[deploy] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
