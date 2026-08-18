"""
CloudLab LAN — static routes so nodes on different subnets (e.g. 10.1.x vs 10.2.x)
can reach each other via your experiment router.

This is orthogonal to cloudlab_wan (WAN delay emulation).

Configure in cloudlab_settings.json (optional):

    "cloudlab_lan": {
        "prefix_len": 24,
        "cross_subnet_via": "10.1.1.254",
        "per_subnet_via": { "10.2.1.0/24": "10.1.1.254" }
    }

Or pass cross_subnet_via from fab. If all hosts share one IPv4 subnet, setup no-ops.

Requires: sudo, ip, python3 on nodes.
"""

from __future__ import annotations

import base64
import ipaddress
import json
from pathlib import Path

from fabric import ThreadingGroup as Group
from fabric.exceptions import GroupException
from paramiko import RSAKey
from paramiko.ssh_exception import PasswordRequiredException, SSHException

from benchmark.cloudlab_instance import CloudLabInstanceManager
from benchmark.utils import Print, BenchError


def _load_raw_settings(settings_path: Path) -> dict:
    with open(settings_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _connect_kwargs_from_settings(settings_path: Path) -> dict:
    data = _load_raw_settings(settings_path)
    key_path = data.get('ssh_key_path') or data['key']['path']
    try:
        try:
            return {'pkey': RSAKey.from_private_key_file(key_path)}
        except PasswordRequiredException:
            import os
            password = os.environ.get('SSH_KEY_PASSWORD') or data.get('ssh_key_password')
            if not password:
                raise BenchError(
                    'SSH key is password-protected; set SSH_KEY_PASSWORD or ssh_key_password in settings',
                    PasswordRequiredException('encrypted key'),
                )
            return {'pkey': RSAKey.from_private_key_file(key_path, password=password)}
    except (OSError, SSHException) as e:
        raise BenchError('Failed to load SSH key', e)


def _host_ipv4(hostname: str) -> str:
    host = hostname.split(':')[0].strip()
    try:
        ipaddress.ip_address(host)
    except ValueError as e:
        raise BenchError(f'Host entry must be an IPv4 address for cloudlab_lan: {hostname!r}', e)
    return host


def _networks_for_hosts(host_ips: list[str], prefix_len: int) -> list[ipaddress.IPv4Network]:
    nets: set[ipaddress.IPv4Network] = set()
    for ip_s in host_ips:
        iface = ipaddress.ip_interface((ip_s, prefix_len))
        nets.add(iface.network)
    return sorted(nets, key=lambda n: (int(n.network_address), n.prefixlen))


def _group_hosts(host_info: list[dict]):
    by_key: dict[tuple[str, int], list[str]] = {}
    for host in host_info:
        username = host.get('username', 'root')
        hostname = host['hostname']
        port = int(host.get('port', 22))
        key = (username, port)
        by_key.setdefault(key, []).append(hostname)
    return by_key


def _routes_payload(
    networks: list[ipaddress.IPv4Network],
    default_via: str | None,
    per_net_via: dict[str, str],
) -> dict:
    routes = []
    for n in networks:
        v = per_net_via.get(n.with_prefixlen) or default_via
        if v:
            routes.append({'net': n.with_prefixlen, 'via': v})
    return {'routes': routes}


def _remote_apply_routes_b64(b64_data: str) -> str:
    return f'''set -e
export LOCAL=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{{print $7; exit}}')
[ -n "$LOCAL" ] || export LOCAL=$(hostname -I | awk '{{print $1}}')
[ -n "$LOCAL" ] || {{ echo "cloudlab_lan: no local IPv4"; exit 1; }}
export DATA=$(echo {b64_data} | base64 -d)
python3 - <<'PY'
import json, subprocess, ipaddress, os
local = ipaddress.ip_address(os.environ["LOCAL"].strip())
data = json.loads(os.environ["DATA"])
for r in data["routes"]:
    via = r["via"]
    if not via:
        continue
    net = ipaddress.ip_network(r["net"], strict=False)
    if local in net:
        continue
    p = subprocess.run(
        ["sudo", "ip", "route", "replace", r["net"], "via", via],
        capture_output=True, text=True,
    )
    if p.returncode != 0:
        p = subprocess.run(
            ["sudo", "ip", "route", "add", r["net"], "via", via],
            capture_output=True, text=True,
        )
    if p.returncode != 0:
        raise SystemExit(p.stderr or p.stdout or "ip route failed")
PY
'''


def _remote_clear_routes_b64(b64_data: str) -> str:
    return f'''set -e
export LOCAL=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{{print $7; exit}}')
[ -n "$LOCAL" ] || export LOCAL=$(hostname -I | awk '{{print $1}}')
[ -n "$LOCAL" ] || exit 0
export DATA=$(echo {b64_data} | base64 -d)
python3 - <<'PY'
import json, subprocess, ipaddress, os
local = ipaddress.ip_address(os.environ["LOCAL"].strip())
data = json.loads(os.environ["DATA"])
for r in data["routes"]:
    via = r["via"]
    if not via:
        continue
    net = ipaddress.ip_network(r["net"], strict=False)
    if local in net:
        continue
    subprocess.run(
        ["sudo", "ip", "route", "del", r["net"], "via", via],
        capture_output=True, text=True,
    )
PY
'''


def _remote_verify_b64(b64_ips: str) -> str:
    return f'''set -e
export LOCAL=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{{print $7; exit}}')
[ -n "$LOCAL" ] || export LOCAL=$(hostname -I | awk '{{print $1}}')
export OTHERS_JSON=$(echo {b64_ips} | base64 -d)
python3 - <<'PY'
import json, os, subprocess
local = os.environ["LOCAL"].strip()
others = json.loads(os.environ["OTHERS_JSON"])
for t in others:
    if t == local:
        continue
    r = subprocess.run(["ping", "-c", "1", "-W", "2", t], capture_output=True)
    if r.returncode != 0:
        raise SystemExit("cannot ping %s from %s" % (t, local))
print("OK")
PY
'''


class CloudLabLan:
    def __init__(self, settings_file: str = 'cloudlab_settings.json'):
        self.settings_path = Path(settings_file)
        if not self.settings_path.is_absolute():
            self.settings_path = Path(__file__).resolve().parent.parent / settings_file
        self.manager = CloudLabInstanceManager.make(str(self.settings_path))
        self._raw = _load_raw_settings(self.settings_path)
        self._lan_cfg = self._raw.get('cloudlab_lan') or {}

    def _conn_kwargs(self) -> dict:
        return _connect_kwargs_from_settings(self.settings_path)

    def _prefix_len(self, override: int | None) -> int:
        if override is not None:
            return int(override)
        return int(self._lan_cfg.get('prefix_len', 24))

    def _cross_subnet_via(self, via_cli: str | None) -> str | None:
        if via_cli:
            return via_cli.strip()
        v = self._lan_cfg.get('cross_subnet_via')
        return str(v).strip() if v else None

    def _per_subnet_via(self) -> dict[str, str]:
        raw = self._lan_cfg.get('per_subnet_via') or {}
        return {str(k): str(v) for k, v in raw.items()}

    def setup(self, prefix_len: int | None = None, cross_subnet_via: str | None = None) -> None:
        host_info = self.manager.get_host_info()
        ips = [_host_ipv4(h['hostname']) for h in host_info]
        plen = self._prefix_len(prefix_len)
        networks = _networks_for_hosts(ips, plen)
        via_default = self._cross_subnet_via(cross_subnet_via)
        per_net_via = self._per_subnet_via()

        Print.heading('CloudLab LAN: setup static routes')
        Print.info(f'Host IPv4s: {ips}')
        Print.info(f'Inferred subnets (/{plen}): {[n.with_prefixlen for n in networks]}')

        if len(networks) <= 1:
            Print.info('All hosts appear on one subnet; no cross-subnet routes needed.')
            return

        if not via_default and not per_net_via:
            raise BenchError(
                'Multiple subnets detected but no next-hop. Set cloudlab_lan.cross_subnet_via '
                'in cloudlab_settings.json or pass cross_subnet_via= to fab cloudlab_lan.',
                ValueError('missing cross_subnet_via'),
            )

        payload = _routes_payload(networks, via_default, per_net_via)
        for r in payload['routes']:
            Print.info(f'  route: {r["net"]} via {r["via"]} (on hosts not in that net)')

        b64 = base64.b64encode(json.dumps(payload).encode()).decode('ascii')
        script = _remote_apply_routes_b64(b64)
        conn_kw = self._conn_kwargs()

        try:
            for (username, port), hostnames in _group_hosts(host_info).items():
                g = Group(*hostnames, user=username, port=port, connect_kwargs=conn_kw, connect_timeout=30)
                results = g.run(script, hide=True, warn=True)
                if isinstance(results, dict):
                    for hn, res in results.items():
                        if not res.ok:
                            raise BenchError(
                                f'Route setup failed on {hn}: {res.stderr or res.stdout}',
                                RuntimeError('route failed'),
                            )
                elif not results.ok:
                    raise BenchError(
                        f'Route setup failed: {results.stderr or results.stdout}',
                        RuntimeError('route failed'),
                    )
        except GroupException as e:
            raise BenchError('cloudlab_lan setup failed on one or more hosts', e)

        Print.info('Static routes applied on all hosts.')

    def clear(self, prefix_len: int | None = None, cross_subnet_via: str | None = None) -> None:
        host_info = self.manager.get_host_info()
        ips = [_host_ipv4(h['hostname']) for h in host_info]
        plen = self._prefix_len(prefix_len)
        networks = _networks_for_hosts(ips, plen)
        via_default = self._cross_subnet_via(cross_subnet_via)
        per_net_via = self._per_subnet_via()

        if len(networks) <= 1:
            Print.info('All hosts on one subnet; nothing to clear.')
            return

        if not via_default and not per_net_via:
            Print.warn('No cross_subnet_via / per_subnet_via; cannot know routes to delete. Skipping.')
            return

        payload = _routes_payload(networks, via_default, per_net_via)
        b64 = base64.b64encode(json.dumps(payload).encode()).decode('ascii')
        script = _remote_clear_routes_b64(b64)

        Print.heading('CloudLab LAN: clear static routes')
        conn_kw = self._conn_kwargs()
        for (username, port), hostnames in _group_hosts(host_info).items():
            g = Group(*hostnames, user=username, port=port, connect_kwargs=conn_kw, connect_timeout=30)
            g.run(script, hide=True, warn=True)
        Print.info('Clear completed (missing routes ignored).')

    def verify(self, prefix_len: int | None = None) -> bool:
        host_info = self.manager.get_host_info()
        ips = [_host_ipv4(h['hostname']) for h in host_info]
        b64 = base64.b64encode(json.dumps(ips).encode()).decode('ascii')
        script = _remote_verify_b64(b64)

        conn_kw = self._conn_kwargs()
        Print.heading('CloudLab LAN: verify (ICMP ping all peers)')
        Print.info(f'Settings subnet prefix length: /{self._prefix_len(prefix_len)}')
        ok_all = True
        try:
            for (username, port), hostnames in _group_hosts(host_info).items():
                g = Group(*hostnames, user=username, port=port, connect_kwargs=conn_kw, connect_timeout=30)
                res = g.run(script, hide=True, warn=True)
                if isinstance(res, dict):
                    for hn, r in res.items():
                        if not r.ok or 'OK' not in (r.stdout or ''):
                            Print.warn(f'{hn}: verify failed: {r.stderr or r.stdout}')
                            ok_all = False
                        else:
                            Print.info(f'{hn}: all peers reachable')
                elif not res.ok:
                    Print.warn(f'verify failed: {res.stderr or res.stdout}')
                    ok_all = False
                else:
                    Print.info('verify ok')
        except GroupException as e:
            Print.warn(f'Group verify error: {e}')
            ok_all = False
        return ok_all
