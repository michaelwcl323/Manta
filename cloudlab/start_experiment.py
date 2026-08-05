#!/usr/bin/env python3
"""Start a CloudLab experiment for the artifact using geni-lib."""

from __future__ import annotations

import argparse
import getpass
import importlib
import json
import os
import shlex
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path


DEFAULT_REPO_URL = "https://github.com/michaelwcl323/manta-nsdi27.git"
DEFAULT_DISK_IMAGE = "urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU20-64-STD"
DEFAULT_SETTINGS = Path("cloudlab_settings.json")
PROXY_ENV_NAMES = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")


def require_geni_lib():
    try:
        import geni.aggregate.cloudlab as cloudlab
        import geni.rspec.pg as pg
        import geni.util as util
    except ImportError as exc:
        raise SystemExit(
            "geni-lib is required. Install it and create a CloudLab context first:\n"
            "  python3 -m pip install geni-lib\n"
            "  build-context --type cloudlab --cert /path/to/cloudlab.pem "
            "--pubkey /path/to/id_rsa.pub --project <project>\n"
            f"Import error: {exc}"
        ) from exc
    return cloudlab, pg, util


def disable_proxy_env() -> None:
    removed = [name for name in PROXY_ENV_NAMES if os.environ.pop(name, None)]
    if removed:
        print(
            "[cloudlab] proxy: disabled proxy environment for CloudLab client-certificate API "
            f"({', '.join(removed)})"
        )


def force_utc_timezone() -> None:
    os.environ["TZ"] = "UTC"
    if hasattr(time, "tzset"):
        time.tzset()


def patch_geni_xmlrpc_bytes() -> None:
    import geni.aggregate.frameworks as frameworks
    import geni.minigcf.util as minigcf_util

    original_rpcpost = minigcf_util._rpcpost
    original_get_user_credentials = frameworks.CHAPI2.getUserCredentials
    original_get_slice_credentials = frameworks.CHAPI2.getSliceCredentials

    def rpcpost_bytes(url, req_data, cert, root_bundle):
        if isinstance(req_data, str):
            req_data = req_data.encode("utf-8")
        return original_rpcpost(url, req_data, cert, root_bundle)

    def credential_bytes(value):
        return value.encode("utf-8") if isinstance(value, str) else value

    def get_user_credentials_bytes(self, owner_urn):
        return credential_bytes(original_get_user_credentials(self, owner_urn))

    def get_slice_credentials_bytes(self, context, slicename):
        return credential_bytes(original_get_slice_credentials(self, context, slicename))

    minigcf_util._rpcpost = rpcpost_bytes
    for module_name in ("geni.minigcf.amapi2", "geni.minigcf.chapi2"):
        module = importlib.import_module(module_name)
        module._rpcpost = rpcpost_bytes
    frameworks.CHAPI2.getUserCredentials = get_user_credentials_bytes
    frameworks.CHAPI2.getSliceCredentials = get_slice_credentials_bytes


def remove_invalid_xml_cache(path: Path) -> None:
    if not path.exists():
        return
    try:
        if path.stat().st_size > 0:
            ET.parse(path)
            return
    except ET.ParseError:
        pass
    path.unlink()
    print(f"[cloudlab] cache: removed invalid credential cache {path}")


def remove_invalid_credential_cache(context, experiment_name: str) -> None:
    datadir = Path(context.datadir)
    names = [
        f"{context.cf.name}-{context.uname}-usercred.xml",
        f"{context.cf.name}-{context.project}-{experiment_name}-scred.xml",
    ]
    for name in names:
        remove_invalid_xml_cache(datadir / name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start a CloudLab experiment.")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--name", help="CloudLab experiment/slice name.")
    parser.add_argument("--nodes", type=int, help="Number of CloudLab raw PCs.")
    parser.add_argument("--ref", help="Git ref to checkout on each node.")
    parser.add_argument("--repo-url")
    parser.add_argument("--aggregate", help="CloudLab aggregate host.")
    parser.add_argument("--disk-image")
    parser.add_argument("--node-type", help="CloudLab hardware type, such as c220g5.")
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--context", type=Path, help="Existing geni-lib context JSON path.")
    parser.add_argument("--cert", type=Path, help="CloudLab x509 credential PEM path.")
    parser.add_argument("--key", type=Path, help="Private key path if not included in --cert.")
    parser.add_argument("--pubkey", type=Path, help="SSH public key path.")
    parser.add_argument("--project", help="CloudLab project name.")
    parser.add_argument(
        "--ask-passphrase",
        action="store_true",
        default=None,
        help="Prompt for the CloudLab private-key passphrase.",
    )
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
    args.name = setting(args, settings, "name", nested(settings, "experiment", "name"))
    args.nodes = setting(args, settings, "nodes", nested(settings, "experiment", "nodes", default=host_count or None))
    args.ref = setting(args, settings, "ref", nested(settings, "repo", "branch"))
    args.repo_url = setting(args, settings, "repo_url", nested(settings, "repo", "url", default=DEFAULT_REPO_URL))
    args.aggregate = setting(args, settings, "aggregate", nested(settings, "experiment", "aggregate", default="cloudlab.us"))
    args.disk_image = setting(args, settings, "disk_image", nested(settings, "experiment", "disk_image", default=DEFAULT_DISK_IMAGE))
    args.node_type = setting(args, settings, "node_type", nested(settings, "experiment", "node_type"))
    args.build_dir = Path(setting(args, settings, "build_dir", "build"))
    args.context = setting(args, settings, "context", nested(settings, "credential", "context"))
    args.cert = setting(args, settings, "cert", nested(settings, "credential", "cert", default=settings.get("cloudlab_cert")))
    args.key = args.key if args.key is not None else nested(settings, "credential", "key")
    default_pubkey = None
    ssh_key = nested(settings, "key", "path")
    if ssh_key:
        default_pubkey = f"{ssh_key}.pub"
    args.pubkey = setting(args, settings, "pubkey", nested(settings, "key", "pubkey", default=default_pubkey))
    args.project = setting(args, settings, "project", nested(settings, "credential", "project"))
    args.ask_passphrase = bool(setting(args, settings, "ask_passphrase", nested(settings, "credential", "ask_passphrase", default=False)))

    for path_name in ("context", "cert", "key", "pubkey"):
        value = getattr(args, path_name)
        if value is not None:
            setattr(args, path_name, Path(value))

    missing = [name for name in ("name", "nodes", "ref") if getattr(args, name) is None]
    if missing:
        raise SystemExit(
            "missing required CloudLab setting(s): "
            + ", ".join(missing)
            + f". Add them to {args.settings} or pass them on the command line."
        )
    args.nodes = int(args.nodes)
    return args


def absolute_path(path: Path) -> str:
    return str(path.expanduser().resolve())


def user_urn_from_cert(cert_path: Path) -> str:
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes(), default_backend())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    for uri in san.value.get_values_for_type(x509.UniformResourceIdentifier):
        if uri.startswith("urn:publicid"):
            return uri
    raise SystemExit(f"could not find GENI user URN in certificate {cert_path}")


def context_path(args: argparse.Namespace) -> Path | None:
    if args.context:
        return args.context.expanduser()

    if not any([args.cert, args.pubkey, args.project]):
        return None

    missing = [
        name
        for name, value in [("--cert", args.cert), ("--pubkey", args.pubkey), ("--project", args.project)]
        if not value
    ]
    if missing:
        raise SystemExit(f"context creation requires: {', '.join(missing)}")

    cert_path = args.cert.expanduser().resolve()
    pubkey_path = args.pubkey.expanduser().resolve()
    key_path = args.key.expanduser().resolve() if args.key else cert_path

    if not cert_path.exists():
        raise SystemExit(f"certificate path does not exist: {cert_path}")
    if not key_path.exists():
        raise SystemExit(f"private key path does not exist: {key_path}")
    if not pubkey_path.exists():
        raise SystemExit(f"SSH public key path does not exist: {pubkey_path}")

    user_urn = user_urn_from_cert(cert_path)
    username = user_urn.split("+")[-1]
    output = args.build_dir / "cloudlab-context.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "framework": "emulab-ch2",
                "cert-path": absolute_path(cert_path),
                "key-path": absolute_path(key_path),
                "user-name": username,
                "user-urn": user_urn,
                "user-pubkeypath": absolute_path(pubkey_path),
                "project": args.project,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[cloudlab] context written to {output}")
    return output


def build_request(pg, *, nodes: int, repo_url: str, ref: str, disk_image: str, node_type: str | None):
    request = pg.Request()
    bootstrap = " ".join(
        [
            "sudo",
            "bash",
            "-lc",
            shlex.quote(
                "set -euxo pipefail; "
                "rm -rf /local/repository; "
                f"git clone {shlex.quote(repo_url)} /local/repository; "
                "cd /local/repository; "
                "git fetch --tags origin; "
                f"git checkout {shlex.quote(ref)}; "
                "./scripts/environment_setup.sh; "
                "touch /local/bootstrap.done"
            ),
        ]
    )
    command = (
        f"{bootstrap} > /local/bootstrap.log 2>&1 || "
        "(touch /local/bootstrap.failed; exit 1)"
    )

    for index in range(nodes):
        node = request.RawPC(f"node-{index}")
        node.disk_image = disk_image
        if node_type:
            node.hardware_type = node_type
        node.addService(pg.Execute(shell="bash", command=command))

    return request


def load_context(util, ctx_path: Path | None, key_passphrase: str | None):
    try:
        return (
            util.loadContext(str(ctx_path), key_passphrase=key_passphrase)
            if ctx_path
            else util.loadContext(key_passphrase=key_passphrase)
        )
    except Exception as exc:
        if exc.__class__.__name__ == "KeyDecryptionError":
            raise SystemExit(
                "Could not decrypt the CloudLab credential private key. "
                "Use the passphrase for cloudlab.pem, not the SSH key password; "
                "or set credential.key to an unencrypted private-key file."
            ) from exc
        raise


def exception_detail(exc: Exception) -> str:
    chain = []
    current = exc
    while current is not None:
        chain.append(f"{current.__class__.__name__}: {current}")
        current = current.__cause__ or current.__context__
    return " | ".join(chain)


def cloudlab_error_message(exc: Exception) -> str:
    detail = exception_detail(exc)

    if "SSLEOFError" in detail or "UNEXPECTED_EOF" in detail or "SSLError" in detail:
        return (
            "[cloudlab] error: CloudLab/Emulab XMLRPC SSL connection failed. "
            "This usually means the request was sent through a proxy, the network/firewall "
            "closed the mutual-TLS connection, or the CloudLab credential was rejected. "
            f"Details: {detail}"
        )
    return f"[cloudlab] error: CloudLab API request failed. Details: {detail}"


def ensure_slice_exists(context, experiment_name: str) -> None:
    try:
        context.getSliceInfo(experiment_name)
        return
    except Exception as exc:
        if "No such Slice" not in exception_detail(exc):
            raise

    print(f"[cloudlab] slice: creating slice {experiment_name}")
    context.cf.createSlice(context, experiment_name)
    remove_invalid_credential_cache(context, experiment_name)


def should_fetch_existing_manifest(exc: Exception) -> bool:
    detail = exception_detail(exc)
    existing_messages = (
        "Must delete existing slice first",
        "already exists",
        "already have a sliver",
    )
    return any(message in detail for message in existing_messages)


def manifest_to_text(manifest) -> str:
    if isinstance(manifest, bytes):
        return manifest.decode("utf-8")
    if isinstance(manifest, str):
        return manifest
    text = getattr(manifest, "text", None)
    if isinstance(text, str):
        return text
    if hasattr(manifest, "toXMLString"):
        return manifest.toXMLString(pretty_print=True)
    return str(manifest)


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


def main() -> int:
    args = apply_settings(parse_args())
    if args.nodes <= 0:
        raise SystemExit("--nodes must be positive")

    force_utc_timezone()
    disable_proxy_env()
    cloudlab, pg, util = require_geni_lib()
    patch_geni_xmlrpc_bytes()
    ctx_path = context_path(args)
    key_passphrase = getpass.getpass("CloudLab private-key passphrase: ") if args.ask_passphrase else None
    context = load_context(util, ctx_path, key_passphrase)
    remove_invalid_credential_cache(context, args.name)
    aggregate = cloudlab.CloudLabAM(name="cloudlab", host=args.aggregate)
    request = build_request(
        pg,
        nodes=args.nodes,
        repo_url=args.repo_url,
        ref=args.ref,
        disk_image=args.disk_image,
        node_type=args.node_type,
    )

    print(f"[cloudlab] starting experiment {args.name} with {args.nodes} node(s)")
    try:
        ensure_slice_exists(context, args.name)
        try:
            manifest = aggregate.createsliver(context, args.name, request)
        except Exception as exc:
            if not should_fetch_existing_manifest(exc):
                raise
            print(f"[cloudlab] experiment {args.name} already exists; fetching current manifest")
            manifest = aggregate.listresources(context, args.name)
    except Exception as exc:
        raise SystemExit(cloudlab_error_message(exc)) from None
    manifest_text = manifest_to_text(manifest)

    args.build_dir.mkdir(parents=True, exist_ok=True)
    (args.build_dir / "experiment-name").write_text(args.name + "\n", encoding="utf-8")
    (args.build_dir / "ref").write_text(args.ref + "\n", encoding="utf-8")
    (args.build_dir / "manifest.xml").write_text(manifest_text, encoding="utf-8")

    hosts = parse_login_hosts(manifest_text)
    if hosts:
        (args.build_dir / "nodes").write_text("\n".join(hosts) + "\n", encoding="utf-8")
        (args.build_dir / "head-node").write_text(hosts[0] + "\n", encoding="utf-8")
        print(f"[cloudlab] head node: {hosts[0]}")
        print(f"[cloudlab] node list written to {args.build_dir / 'nodes'}")
    else:
        print("[cloudlab] warning: no login hostnames found in manifest", file=sys.stderr)

    print(f"[cloudlab] manifest written to {args.build_dir / 'manifest.xml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
