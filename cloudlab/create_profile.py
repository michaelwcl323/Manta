#!/usr/bin/env python3
"""Generate the CloudLab profile used by the artifact.

The generated profile is a geni-lib profile script that can be uploaded to the
CloudLab portal or kept in a repository-based CloudLab profile.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_REPO_URL = "https://github.com/michaelwcl323/manta-nsdi27.git"
DEFAULT_OUTPUT = Path("cloudlab/profile.py")
DEFAULT_DISK_IMAGE = "urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU20-64-STD"
DEFAULT_SETTINGS = Path("cloudlab_settings.json")


PROFILE_TEMPLATE = '''"""CloudLab profile for the MANTA NSDI'27 artifact."""

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

for index in range(params.nodes):
    node = request.RawPC("node-%d" % index)
    node.disk_image = "{disk_image}"
    if params.node_type:
        node.hardware_type = params.node_type
    node.addService(
        rspec.Execute(
            shell="bash",
            command=bootstrap.format(repo_url=params.repo_url, ref=params.ref),
        )
    )

pc.printRequestRSpec(request)
'''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the CloudLab profile script.")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--nodes", type=int)
    parser.add_argument("--repo-url")
    parser.add_argument("--ref")
    parser.add_argument("--disk-image")
    parser.add_argument("--node-type")
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


def setting(args: argparse.Namespace, settings: dict, name: str, default=None):
    value = getattr(args, name)
    return value if value is not None else settings.get(name, default)


def apply_settings(args: argparse.Namespace) -> argparse.Namespace:
    settings = load_settings(args.settings)
    host_count = len(settings.get("hosts", []))
    args.nodes = int(setting(args, settings, "nodes", host_count or 10))
    args.repo_url = setting(
        args,
        settings,
        "repo_url",
        nested(settings, "repo", "url", default=DEFAULT_REPO_URL),
    )
    args.ref = setting(
        args,
        settings,
        "ref",
        nested(settings, "repo", "branch", default="refs/tags/nsdi27-ae-v1"),
    )
    args.disk_image = setting(
        args,
        settings,
        "disk_image",
        nested(settings, "experiment", "disk_image", default=DEFAULT_DISK_IMAGE),
    )
    args.node_type = setting(args, settings, "node_type", nested(settings, "experiment", "node_type", default=""))
    if args.node_type is None:
        args.node_type = ""
    return args


def main() -> int:
    args = apply_settings(parse_args())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        PROFILE_TEMPLATE.format(
            default_nodes=args.nodes,
            repo_url=args.repo_url,
            default_ref=args.ref,
            disk_image=args.disk_image,
            node_type=args.node_type,
        ),
        encoding="utf-8",
    )
    print(f"[cloudlab] profile written to {args.output}")
    print("[cloudlab] Upload this file as a CloudLab geni-lib profile, or use it in a repository-based profile.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
