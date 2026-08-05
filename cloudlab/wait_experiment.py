#!/usr/bin/env python3
"""Wait until all allocated CloudLab/APT nodes accept SSH."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


DEFAULT_SETTINGS = Path("cloudlab_settings.json")
DEFAULT_BUILD_DIR = Path("build")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wait for allocated nodes to accept SSH.")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    parser.add_argument("--timeout", type=int, default=1800, help="Maximum wait time in seconds.")
    parser.add_argument("--interval", type=int, default=15, help="Polling interval in seconds.")
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


def ssh_ready(host: str, username: str, key: Path, connect_timeout: int) -> bool:
    command = [
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
        "true",
    ]
    return subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0


def main() -> int:
    args = parse_args()
    settings = load_settings(args.settings)
    username = args.username or default_username(settings)
    key = (args.key or default_key(settings)).expanduser()
    nodes = read_nodes(args.build_dir / "nodes")

    print(f"[cloudlab] waiting for SSH on {len(nodes)} node(s) as {username}")
    print(f"[cloudlab] ssh key: {key}")

    deadline = time.monotonic() + args.timeout
    ready: set[str] = set()
    while time.monotonic() < deadline:
        for host in nodes:
            if host in ready:
                continue
            if ssh_ready(host, username, key, args.connect_timeout):
                ready.add(host)
                print(f"[cloudlab] ssh: {host}: READY")
            else:
                print(f"[cloudlab] ssh: {host}: WAIT")

        if len(ready) == len(nodes):
            print("[cloudlab] all nodes accept SSH")
            return 0
        time.sleep(args.interval)

    missing = [host for host in nodes if host not in ready]
    print(f"[cloudlab] error: SSH did not become ready on: {', '.join(missing)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
