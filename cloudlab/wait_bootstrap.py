#!/usr/bin/env python3
"""Wait until CloudLab/APT node bootstrap scripts finish."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


DEFAULT_SETTINGS = Path("cloudlab_settings.json")
DEFAULT_BUILD_DIR = Path("build")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wait for CloudLab/APT bootstrap completion.")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    parser.add_argument("--timeout", type=int, default=3600, help="Maximum wait time in seconds.")
    parser.add_argument("--interval", type=int, default=20, help="Polling interval in seconds.")
    parser.add_argument("--connect-timeout", type=int, default=10, help="SSH connection timeout in seconds.")
    parser.add_argument("--username")
    parser.add_argument("--key", type=Path, help="SSH private key path.")
    return parser.parse_args()


def load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
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
    explicit = nested(settings, "key", "private")
    if explicit:
        return Path(explicit).expanduser()
    pubkey = nested(settings, "key", "pubkey")
    if pubkey and str(pubkey).endswith(".pub"):
        return Path(str(pubkey)[:-4]).expanduser()
    key = nested(settings, "key", "path")
    if key:
        return Path(key).expanduser()
    return Path("~/.ssh/id_rsa").expanduser()


def default_username(settings: dict) -> str:
    hosts = settings.get("hosts", [])
    if hosts and isinstance(hosts[0], dict) and hosts[0].get("username"):
        return hosts[0]["username"]
    return "ubuntu"


def read_nodes(path: Path) -> list[str]:
    if not path.exists():
        raise SystemExit(f"node list does not exist: {path}")
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def ssh_command(host: str, username: str, key: Path, connect_timeout: int, remote_command: str) -> list[str]:
    return [
        "ssh",
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
        f"{username}@{host}",
        remote_command,
    ]


def bootstrap_status(host: str, username: str, key: Path, connect_timeout: int) -> str:
    command = ssh_command(
        host,
        username,
        key,
        connect_timeout,
        "if [ -f /local/bootstrap.failed ]; then echo FAILED; "
        "elif [ -f /local/bootstrap.done ]; then echo DONE; "
        "else echo WAIT; fi",
    )
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)
    if result.returncode != 0:
        return "WAIT_SSH"
    return result.stdout.strip() or "WAIT"


def main() -> int:
    args = parse_args()
    settings = load_settings(args.settings)
    username = args.username or default_username(settings)
    key = (args.key or default_key(settings)).expanduser()
    nodes = read_nodes(args.build_dir / "nodes")

    print(f"[cloudlab] waiting for bootstrap on {len(nodes)} node(s) as {username}")
    print(f"[cloudlab] ssh key: {key}")

    deadline = time.monotonic() + args.timeout
    done: set[str] = set()
    failed: set[str] = set()
    while time.monotonic() < deadline:
        for host in nodes:
            if host in done or host in failed:
                continue
            status = bootstrap_status(host, username, key, args.connect_timeout)
            if status == "DONE":
                done.add(host)
            elif status == "FAILED":
                failed.add(host)
            print(f"[cloudlab] bootstrap: {host}: {status}")

        if failed:
            print(f"[cloudlab] error: bootstrap failed on: {', '.join(sorted(failed))}")
            return 1
        if len(done) == len(nodes):
            print("[cloudlab] bootstrap completed on all nodes")
            return 0
        time.sleep(args.interval)

    pending = [host for host in nodes if host not in done]
    print(f"[cloudlab] error: bootstrap did not finish on: {', '.join(pending)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
