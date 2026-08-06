#!/usr/bin/env python3
"""Manage APT/CloudLab experiments through the official Portal API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


DEFAULT_SETTINGS = Path("cloudlab_settings.json")
DEFAULT_DISK_IMAGE = "urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU22-64-STD"
PROXY_ENV_NAMES = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")

PROFILE_TEMPLATE = '''"""CloudLab profile for the MANTA NSDI'27 artifact.

Creates a small-lan style shared LAN so replica nodes are reachable at
10.10.1.1 .. 10.10.1.N (controller is the last node).
"""

import geni.portal as portal
import geni.rspec.pg as rspec


pc = portal.Context()
pc.defineParameter(
    "nodes",
    "Number of physical nodes",
    portal.ParameterType.INTEGER,
    {default_nodes},
    longDescription="Number of CloudLab raw PCs to reserve.",
)
pc.defineParameter(
    "repo_url",
    "Artifact repository URL",
    portal.ParameterType.STRING,
    "{repo_url}",
)
pc.defineParameter(
    "ref",
    "Git ref to checkout",
    portal.ParameterType.STRING,
    "{default_ref}",
)
pc.defineParameter(
    "node_type",
    "CloudLab hardware type",
    portal.ParameterType.STRING,
    "{node_type}",
    longDescription="Optional CloudLab hardware type. Leave empty to let CloudLab choose any available raw PC.",
)

params = pc.bindParameters()
request = pc.makeRequestRSpec()

bootstrap = (
    "sudo bash -lc "
    "'set -euxo pipefail; "
    "rm -rf /local/repository; "
    "git clone {{repo_url}} /local/repository; "
    "cd /local/repository; "
    "git fetch --tags origin; "
    "git checkout {{ref}}; "
    "./scripts/environment_setup.sh; "
    "touch /local/bootstrap.done' "
    "> /local/bootstrap.log 2>&1 || "
    "(touch /local/bootstrap.failed; exit 1)"
)

# Small-lan style shared LAN for intra-experiment traffic (10.10.1.0/24).
lan = request.LAN("lan")

for index in range(params.nodes):
    node = request.RawPC("node-%d" % index)
    node.disk_image = "{disk_image}"
    if params.node_type:
        node.hardware_type = params.node_type
    iface = node.addInterface("eth1")
    iface.addAddress(rspec.IPv4Address("10.10.1.%d" % (index + 1), "255.255.255.0"))
    lan.addInterface(iface)
    node.addService(
        rspec.Execute(
            shell="bash",
            command=bootstrap.format(repo_url=params.repo_url, ref=params.ref),
        )
    )

pc.printRequestRSpec(request)
'''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage experiments through the Portal API.")
    parser.add_argument(
        "command",
        choices=("create-profile", "start", "status", "manifests", "terminate"),
        help="Portal API operation to run.",
    )
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--build-dir", type=Path)
    return parser.parse_args()


def load_settings(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"settings file does not exist: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("cloudlab", data)


def disable_proxy_env() -> None:
    removed = [name for name in PROXY_ENV_NAMES if os.environ.pop(name, None)]
    if removed:
        print(f"[cloudlab] portal: disabled proxy environment ({', '.join(removed)})")


def nested(settings: dict, *keys, default=None):
    value = settings
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def path_setting(settings: dict, *keys, default=None) -> Path:
    value = nested(settings, *keys, default=default)
    if value is None:
        raise SystemExit(f"missing setting: {'.'.join(keys)}")
    return Path(value).expanduser()


def read_token(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"Portal API token file does not exist: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" in line:
            line = line.split("=", 1)[1].strip().strip("\"'")
        return line
    raise SystemExit(f"Portal API token file is empty: {path}")


def api_url(settings: dict, path: str) -> str:
    base = nested(settings, "portal", "url", default=os.environ.get("PORTAL_HTTP"))
    if not base:
        raise SystemExit("missing portal.url setting or PORTAL_HTTP")
    return base.rstrip("/") + path


def api_token(settings: dict) -> str:
    if os.environ.get("PORTAL_TOKEN"):
        return os.environ["PORTAL_TOKEN"]
    return read_token(path_setting(settings, "portal", "token"))


def portal_request(settings: dict, method: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Api-Token": api_token(settings),
    }
    url = api_url(settings, path)
    try:
        for _ in range(5):
            request = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    payload = response.read()
                    if not payload:
                        return None
                    return json.loads(payload.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code not in (307, 308):
                    raise
                location = exc.headers.get("Location")
                if not location:
                    raise
                url = urllib.parse.urljoin(url, location)
        raise SystemExit("[cloudlab] portal error: too many redirects")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        location = exc.headers.get("Location")
        location_note = f" location={location}" if location else ""
        raise SystemExit(f"[cloudlab] portal error {exc.code} {exc.reason}:{location_note} {detail}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"[cloudlab] portal connection error: {exc}") from None


def generate_profile_script(settings: dict) -> Path:
    path = path_setting(settings, "portal", "profile_script")
    nodes = int(nested(settings, "experiment", "nodes", default=10))
    node_type = nested(settings, "experiment", "node_type", default="") or ""
    disk_image = nested(settings, "experiment", "disk_image", default=DEFAULT_DISK_IMAGE)
    repo_url = nested(settings, "repo", "url")
    ref = nested(settings, "repo", "branch")
    if not repo_url or not ref:
        raise SystemExit("missing repo.url or repo.branch in cloudlab_settings.json")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        PROFILE_TEMPLATE.format(
            default_nodes=nodes,
            repo_url=repo_url,
            default_ref=ref,
            node_type=node_type,
            disk_image=disk_image,
        ),
        encoding="utf-8",
    )
    print(f"[cloudlab] profile script written from cloudlab_settings.json: {path}")
    return path


def profile_script(settings: dict) -> str:
    path = generate_profile_script(settings)
    return path.read_text(encoding="utf-8")


def profile_name(settings: dict) -> str:
    return nested(settings, "portal", "profile_name", default=nested(settings, "repo", "name"))


def profile_project(settings: dict) -> str:
    return nested(settings, "portal", "profile_project", default=nested(settings, "portal", "project"))


def experiment_name(settings: dict) -> str:
    return nested(settings, "experiment", "name")


def experiment_project(settings: dict) -> str:
    return nested(settings, "portal", "project")


def build_dir(args: argparse.Namespace, settings: dict) -> Path:
    if args.build_dir:
        return args.build_dir
    return Path(nested(settings, "build_dir", default="build"))


def find_profile(settings: dict) -> dict | None:
    query = urllib.parse.urlencode(
        {
            "project": profile_project(settings),
            "profile_name": profile_name(settings),
        }
    )
    profiles = portal_request(settings, "GET", f"/profiles/?{query}") or {}
    for profile in profiles.get("profiles", []):
        if profile.get("project") == profile_project(settings) and profile.get("name") == profile_name(settings):
            return profile
    return None


def create_or_update_profile(settings: dict) -> dict:
    body = {
        "name": profile_name(settings),
        "project": profile_project(settings),
        "script": profile_script(settings),
        "public": bool(nested(settings, "portal", "profile_public", default=False)),
        "project_writable": bool(nested(settings, "portal", "profile_project_writable", default=False)),
    }
    profile = find_profile(settings)
    if profile:
        profile_id = profile["id"]
        updated = portal_request(
            settings,
            "PATCH",
            f"/profiles/{urllib.parse.quote(profile_id, safe='')}/",
            {
                "script": body["script"],
                "public": body["public"],
                "project_writable": body["project_writable"],
            },
        )
        print(f"[cloudlab] portal profile updated: {profile_project(settings)},{profile_name(settings)}")
        return updated

    created = portal_request(settings, "POST", "/profiles/", body)
    print(f"[cloudlab] portal profile created: {profile_project(settings)},{profile_name(settings)}")
    return created


def ssh_public_key(settings: dict) -> str | None:
    pubkey = nested(settings, "key", "pubkey")
    if not pubkey:
        return None
    path = Path(pubkey).expanduser()
    if not path.exists():
        raise SystemExit(f"SSH public key does not exist: {path}")
    return path.read_text(encoding="utf-8").strip()


def experiment_bindings(settings: dict) -> dict[str, str]:
    bindings = {
        "nodes": str(nested(settings, "experiment", "nodes")),
        "repo_url": str(nested(settings, "repo", "url")),
        "ref": str(nested(settings, "repo", "branch")),
        "node_type": str(nested(settings, "experiment", "node_type", default="") or ""),
    }
    return bindings


def start_experiment(settings: dict, args: argparse.Namespace) -> dict:
    create_or_update_profile(settings)
    body = {
        "name": experiment_name(settings),
        "project": experiment_project(settings),
        "profile_name": profile_name(settings),
        "profile_project": profile_project(settings),
        "duration": int(nested(settings, "portal", "duration_hours", default=24)),
        "bindings": experiment_bindings(settings),
    }
    pubkey = ssh_public_key(settings)
    if pubkey:
        body["sshpubkey"] = pubkey

    experiment = portal_request(settings, "POST", "/experiments/", body)
    out_dir = build_dir(args, settings)
    out_dir.mkdir(parents=True, exist_ok=True)
    id_path = path_setting(settings, "portal", "experiment_id", default=out_dir / "portal-experiment-id")
    json_path = path_setting(settings, "portal", "experiment_json", default=out_dir / "portal-experiment.json")
    id_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    id_path.write_text(experiment["id"] + "\n", encoding="utf-8")
    json_path.write_text(json.dumps(experiment, indent=2) + "\n", encoding="utf-8")
    (out_dir / "experiment-name").write_text(experiment_name(settings) + "\n", encoding="utf-8")
    print(f"[cloudlab] portal experiment created: {experiment_name(settings)}")
    print(f"[cloudlab] portal experiment id: {experiment['id']}")
    if experiment.get("url"):
        print(f"[cloudlab] portal url: {experiment['url']}")
    return experiment


def experiment_id(settings: dict, args: argparse.Namespace) -> str:
    id_path = path_setting(settings, "portal", "experiment_id", default=build_dir(args, settings) / "portal-experiment-id")
    if id_path.exists():
        return id_path.read_text(encoding="utf-8").strip()
    return f"{experiment_project(settings)},{experiment_name(settings)}"


def status(settings: dict, args: argparse.Namespace) -> dict:
    exp_id = urllib.parse.quote(experiment_id(settings, args), safe="")
    experiment = portal_request(settings, "GET", f"/experiments/{exp_id}/")
    print(json.dumps(experiment, indent=2))
    return experiment


def parse_login_hosts(manifest_text: str) -> list[str]:
    try:
        root = ET.fromstring(manifest_text)
    except ET.ParseError:
        return []
    hosts = []
    for element in root.iter():
        if element.tag.endswith("login"):
            hostname = element.attrib.get("hostname")
            if hostname and hostname not in hosts:
                hosts.append(hostname)
    return hosts


def write_manifests(settings: dict, args: argparse.Namespace) -> dict:
    exp_id = urllib.parse.quote(experiment_id(settings, args), safe="")
    manifests = portal_request(settings, "GET", f"/experiments/{exp_id}/manifests/") or {}
    out_dir = build_dir(args, settings)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "portal-manifests.json").write_text(json.dumps(manifests, indent=2) + "\n", encoding="utf-8")

    all_hosts: list[str] = []
    xml_parts = []
    for aggregate, manifest_text in manifests.items():
        xml_parts.append(f"<!-- {aggregate} -->\n{manifest_text}")
        for host in parse_login_hosts(manifest_text):
            if host not in all_hosts:
                all_hosts.append(host)

    (out_dir / "manifest.xml").write_text("\n".join(xml_parts) + "\n", encoding="utf-8")
    if all_hosts:
        controller = all_hosts[-1]
        (out_dir / "nodes").write_text("\n".join(all_hosts) + "\n", encoding="utf-8")
        (out_dir / "controller").write_text(controller + "\n", encoding="utf-8")
        # Keep head-node as an alias of the controller for older scripts.
        (out_dir / "head-node").write_text(controller + "\n", encoding="utf-8")
        print(f"[cloudlab] controller (last node): {controller}")
        print(f"[cloudlab] node list written to {out_dir / 'nodes'}")
    else:
        print("[cloudlab] warning: no login hostnames found in portal manifests", file=sys.stderr)
    print(f"[cloudlab] portal manifests written to {out_dir / 'portal-manifests.json'}")
    return manifests


def terminate(settings: dict, args: argparse.Namespace) -> None:
    exp_id = urllib.parse.quote(experiment_id(settings, args), safe="")
    portal_request(settings, "DELETE", f"/experiments/{exp_id}/")
    print(f"[cloudlab] portal terminate submitted: {experiment_id(settings, args)}")


def main() -> int:
    args = parse_args()
    disable_proxy_env()
    settings = load_settings(args.settings)
    if args.command == "create-profile":
        create_or_update_profile(settings)
    elif args.command == "start":
        start_experiment(settings, args)
    elif args.command == "status":
        status(settings, args)
    elif args.command == "manifests":
        write_manifests(settings, args)
    elif args.command == "terminate":
        terminate(settings, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
