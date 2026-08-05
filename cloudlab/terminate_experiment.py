#!/usr/bin/env python3
"""Terminate a CloudLab experiment created for the artifact."""

from __future__ import annotations

import argparse
import getpass
import importlib
import json
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path


DEFAULT_SETTINGS = Path("cloudlab_settings.json")
PROXY_ENV_NAMES = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")


def require_geni_lib():
    try:
        import geni.aggregate.cloudlab as cloudlab
        import geni.util as util
    except ImportError as exc:
        raise SystemExit(
            "geni-lib is required. Install it and create a CloudLab context first:\n"
            "  python3 -m pip install geni-lib\n"
            "  build-context --type cloudlab --cert /path/to/cloudlab.pem "
            "--pubkey /path/to/id_rsa.pub --project <project>\n"
            f"Import error: {exc}"
        ) from exc
    return cloudlab, util


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
    parser = argparse.ArgumentParser(description="Terminate a CloudLab experiment.")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--name", help="CloudLab experiment/slice name.")
    parser.add_argument("--aggregate", help="CloudLab aggregate host.")
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
    args.name = setting(args, settings, "name", nested(settings, "experiment", "name"))
    args.aggregate = setting(args, settings, "aggregate", nested(settings, "experiment", "aggregate", default="cloudlab.us"))
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

    default_context = args.build_dir / "cloudlab-context.json"
    if default_context.exists():
        return default_context

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
    output = default_context
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


def experiment_name(args: argparse.Namespace) -> str:
    if args.name:
        return args.name

    name_file = args.build_dir / "experiment-name"
    if not name_file.exists():
        raise SystemExit("missing --name and build/experiment-name does not exist")
    return name_file.read_text(encoding="utf-8").strip()


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


def cloudlab_error_message(exc: Exception) -> str:
    chain = []
    current = exc
    while current is not None:
        chain.append(f"{current.__class__.__name__}: {current}")
        current = current.__cause__ or current.__context__
    detail = " | ".join(chain)

    if "SSLEOFError" in detail or "UNEXPECTED_EOF" in detail or "SSLError" in detail:
        return (
            "[cloudlab] error: CloudLab/Emulab XMLRPC SSL connection failed. "
            "This usually means the request was sent through a proxy, the network/firewall "
            "closed the mutual-TLS connection, or the CloudLab credential was rejected. "
            f"Details: {detail}"
        )
    return f"[cloudlab] error: CloudLab API request failed. Details: {detail}"


def main() -> int:
    args = apply_settings(parse_args())
    name = experiment_name(args)
    force_utc_timezone()
    disable_proxy_env()
    cloudlab, util = require_geni_lib()
    patch_geni_xmlrpc_bytes()
    ctx_path = context_path(args)
    key_passphrase = getpass.getpass("CloudLab private-key passphrase: ") if args.ask_passphrase else None
    context = load_context(util, ctx_path, key_passphrase)
    remove_invalid_credential_cache(context, name)
    aggregate = cloudlab.CloudLabAM(name="cloudlab", host=args.aggregate)

    print(f"[cloudlab] terminating experiment {name}")
    try:
        aggregate.deletesliver(context, name)
    except Exception as exc:
        raise SystemExit(cloudlab_error_message(exc)) from None
    print("[cloudlab] terminate request submitted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
