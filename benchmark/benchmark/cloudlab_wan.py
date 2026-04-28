# Copyright(C) Facebook, Inc. and its affiliates.
"""
CloudLab WAN — symmetric RTT emulation between logical sites using tc netem.

Matrix values are round-trip times (ms). Each host adds one-way delay RTT/2 on
marked egress to peers (symmetric path).

Default site from 10.{octet}.* (override with host field wan_site or cloudlab_wan.octet2_site):

    10.1.x.x -> europe
    10.2.x.x -> north_america
    10.3.x.x -> asia
    10.4.x.x -> asia

Default RTT table (ms), symmetric:
    Europe–Europe 20, NA–NA 20, Asia–Asia 0
    Europe–NA 90, Europe–Asia 200, NA–Asia 120

Requires: sudo, tc (sch_htb sch_netem), iptables mangle. TCP/22 is excluded from delay.

Optional cloudlab_settings.json:

    "cloudlab_wan": {
        "octet2_site": {"1": "europe", "2": "north_america", "3": "asia", "4": "asia"},
        "rtt_ms": {"europe,north_america": 90}
    }
"""

from __future__ import annotations

import base64
import ipaddress
import json
import textwrap
from pathlib import Path

from fabric import Connection, ThreadingGroup as Group
from fabric.exceptions import GroupException
from paramiko import RSAKey
from paramiko.ssh_exception import PasswordRequiredException, SSHException

from benchmark.cloudlab_instance import CloudLabInstanceManager
from benchmark.utils import Print, BenchError

WAN_SITES = ('europe', 'north_america', 'asia')

DEFAULT_RTT_RULES: dict[str, int] = {
    'europe,europe': 20,
    'europe,north_america': 90,
    'europe,asia': 200,
    'north_america,north_america': 20,
    'north_america,asia': 120,
    'asia,asia': 0,
}

DEFAULT_OCTET2_SITE = {'1': 'europe', '2': 'north_america', '3': 'asia', '4': 'asia'}

_REMOTE_PY = textwrap.dedent(
    '''
    import json, os, re, subprocess
    from collections import defaultdict
    from pathlib import Path

    data = json.loads(os.environ["WAN_JSON"])
    my_ip = data["my_ip"]
    peers = data["peers"]

    def route_dev(dst):
        r = subprocess.run(["ip", "-4", "route", "get", dst], capture_output=True, text=True)
        if r.returncode != 0:
            return None
        m = re.search(r"\\bdev\\s+(\\S+)", r.stdout)
        return m.group(1) if m else None

    dev = data.get("dev")
    if not dev and peers:
        dev = route_dev(peers[0]["ip"])
    if not dev:
        r = subprocess.run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True)
        m = re.search(r"\\bdev\\s+(\\S+)", r.stdout)
        dev = m.group(1) if m else None
    if not dev:
        raise SystemExit("cloudlab_wan: could not determine egress interface")

    buckets = defaultdict(list)
    for p in peers:
        buckets[int(p["owd_ms"])].append(p["ip"])

    chain = "CLOUDLAB_WAN"
    subprocess.run(["sudo", "iptables", "-t", "mangle", "-D", "OUTPUT", "-j", chain], capture_output=True)
    subprocess.run(["sudo", "iptables", "-t", "mangle", "-F", chain], capture_output=True)
    subprocess.run(["sudo", "iptables", "-t", "mangle", "-X", chain], capture_output=True)
    subprocess.run(["sudo", "iptables", "-t", "mangle", "-N", chain], check=True)

    subprocess.run(
        ["sudo", "iptables", "-t", "mangle", "-A", chain, "-p", "tcp", "--sport", "22", "-j", "RETURN"],
        check=True,
    )
    subprocess.run(
        ["sudo", "iptables", "-t", "mangle", "-A", chain, "-p", "tcp", "--dport", "22", "-j", "RETURN"],
        check=True,
    )

    mark = 0x100
    owd_to_mark = {}
    positive_owds = sorted(owd for owd in buckets if owd > 0)
    for owd in positive_owds:
        owd_to_mark[owd] = mark
        for ip in buckets[owd]:
            subprocess.run(
                [
                    "sudo",
                    "iptables",
                    "-t",
                    "mangle",
                    "-A",
                    chain,
                    "-d",
                    ip + "/32",
                    "-j",
                    "MARK",
                    "--set-mark",
                    str(mark),
                ],
                check=True,
            )
        mark += 0x100
        if mark > 0xF000:
            raise SystemExit("cloudlab_wan: too many delay buckets")

    subprocess.run(["sudo", "iptables", "-t", "mangle", "-A", "OUTPUT", "-j", chain], check=True)

    subprocess.run(["sudo", "tc", "qdisc", "del", "dev", dev, "root"], capture_output=True)

    subprocess.run(
        ["sudo", "tc", "qdisc", "add", "dev", dev, "root", "handle", "1:", "htb", "default", "999"],
        check=True,
    )
    subprocess.run(
        [
            "sudo",
            "tc",
            "class",
            "add",
            "dev",
            dev,
            "parent",
            "1:",
            "classid",
            "1:999",
            "htb",
            "rate",
            "10gbit",
            "ceil",
            "10gbit",
        ],
        check=True,
    )
    subprocess.run(
        ["sudo", "tc", "qdisc", "add", "dev", dev, "parent", "1:999", "handle", "999:", "pfifo_fast"],
        check=True,
    )

    class_id = 10
    for owd in positive_owds:
        m = owd_to_mark[owd]
        cid = "1:%d" % class_id
        h = "%d:" % class_id
        subprocess.run(
            [
                "sudo",
                "tc",
                "class",
                "add",
                "dev",
                dev,
                "parent",
                "1:",
                "classid",
                cid,
                "htb",
                "rate",
                "10gbit",
                "ceil",
                "10gbit",
            ],
            check=True,
        )
        subprocess.run(
            ["sudo", "tc", "qdisc", "add", "dev", dev, "parent", cid, "handle", h, "netem", "delay", "%dms" % owd],
            check=True,
        )
        subprocess.run(
            [
                "sudo",
                "tc",
                "filter",
                "add",
                "dev",
                dev,
                "parent",
                "1:",
                "protocol",
                "ip",
                "prio",
                str(class_id),
                "handle",
                str(m),
                "fw",
                "flowid",
                cid,
            ],
            check=True,
        )
        class_id += 1

    Path("/tmp/cloudlab_wan.state").write_text(json.dumps({"dev": dev, "my_ip": my_ip}))
    print("cloudlab_wan: dev=%s my_ip=%s positive_owds=%s" % (dev, my_ip, positive_owds))
'''
).strip()


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
    ipaddress.ip_address(host)
    return host


def _parse_rtt_matrix(raw: dict | None) -> dict[tuple[str, str], int]:
    rules = dict(DEFAULT_RTT_RULES)
    if raw:
        for k, v in raw.items():
            if isinstance(k, str) and ',' in k:
                a, b = k.split(',', 1)
                a, b = a.strip(), b.strip()
                rules[f'{a},{b}'] = int(v)
                rules[f'{b},{a}'] = int(v)
    out: dict[tuple[str, str], int] = {}
    for key, ms in rules.items():
        a, b = key.split(',')
        out[(a, b)] = ms
    return out


def _site_for_ip(ip: str, octet2_site: dict[str, str], host_override: str | None) -> str:
    if host_override:
        s = str(host_override).strip().lower().replace('-', '_')
        if s == 'northamerica':
            s = 'north_america'
        if s not in WAN_SITES:
            raise BenchError(f'Invalid wan_site {host_override!r}; use one of {WAN_SITES}', ValueError(s))
        return s
    parts = ip.split('.')
    if len(parts) != 4:
        raise BenchError(f'Cannot infer WAN site for {ip!r}', ValueError(ip))
    oct2 = parts[1]
    if oct2 not in octet2_site:
        raise BenchError(
            f'No WAN site for 10.{oct2}.*.*; set cloudlab_wan.octet2_site or host wan_site',
            ValueError(oct2),
        )
    site = octet2_site[oct2]
    if site not in WAN_SITES:
        raise BenchError(f'Invalid site {site!r} in octet2_site map', ValueError(site))
    return site


def _group_hosts(host_info: list[dict]):
    by_key: dict[tuple[str, int], list[dict]] = {}
    for host in host_info:
        username = host.get('username', 'root')
        hostname = host['hostname']
        port = int(host.get('port', 22))
        key = (username, port)
        by_key.setdefault(key, []).append(host)
    return by_key


def _build_plan(
    host_info: list[dict],
    octet2_site: dict[str, str],
    rtt_map: dict[tuple[str, str], int],
) -> dict[str, dict]:
    rows = []
    for h in host_info:
        ip = _host_ipv4(h['hostname'])
        site = _site_for_ip(ip, octet2_site, h.get('wan_site'))
        rows.append({'ip': ip, 'site': site})

    plan: dict[str, dict] = {}
    for r in rows:
        ip, site = r['ip'], r['site']
        peers = []
        for other in rows:
            if other['ip'] == ip:
                continue
            a, b = site, other['site']
            rtt = rtt_map.get((a, b))
            if rtt is None:
                rtt = rtt_map.get((b, a))
            if rtt is None:
                raise BenchError(f'No RTT for sites {a!r} <-> {b!r}', KeyError((a, b)))
            owd = max(0, int(round(rtt / 2.0)))
            peers.append({'ip': other['ip'], 'owd_ms': owd})
        plan[ip] = {'site': site, 'peers': peers}
    return plan


def _remote_setup_shell(b64_payload: str) -> str:
    return f'''set -e
export WAN_JSON=$(echo {b64_payload} | base64 -d)
python3 << 'WANPY'
{_REMOTE_PY}
WANPY
echo CLOUDLAB_WAN_OK
'''


_REMOTE_CLEAR_PY = textwrap.dedent(
    '''
    import json, os, subprocess
    from pathlib import Path

    chain = "CLOUDLAB_WAN"
    subprocess.run(["sudo", "iptables", "-t", "mangle", "-D", "OUTPUT", "-j", chain], capture_output=True)
    subprocess.run(["sudo", "iptables", "-t", "mangle", "-F", chain], capture_output=True)
    subprocess.run(["sudo", "iptables", "-t", "mangle", "-X", chain], capture_output=True)

    p = Path("/tmp/cloudlab_wan.state")
    if p.is_file():
        try:
            dev = json.loads(p.read_text()).get("dev")
        except (json.JSONDecodeError, OSError):
            dev = None
        if dev:
            subprocess.run(["sudo", "tc", "qdisc", "del", "dev", dev, "root"], capture_output=True)
        p.unlink(missing_ok=True)
    print("CLOUDLAB_WAN_CLEARED")
'''
).strip()


def _remote_clear_shell() -> str:
    return f'''set -e
python3 << 'WANCLR'
{_REMOTE_CLEAR_PY}
WANCLR
'''


class CloudLabWan:
    def __init__(self, settings_file: str = 'cloudlab_settings.json'):
        self.settings_path = Path(settings_file)
        if not self.settings_path.is_absolute():
            self.settings_path = Path(__file__).resolve().parent.parent / settings_file
        self.manager = CloudLabInstanceManager.make(str(self.settings_path))
        self._raw = _load_raw_settings(self.settings_path)
        self._wan_cfg = self._raw.get('cloudlab_wan') or {}

    def _conn_kwargs(self) -> dict:
        return _connect_kwargs_from_settings(self.settings_path)

    def _octet2_site(self) -> dict[str, str]:
        m = dict(DEFAULT_OCTET2_SITE)
        raw = (self._wan_cfg.get('octet2_site') or {}) if isinstance(self._wan_cfg, dict) else {}
        for k, v in raw.items():
            sv = str(v).lower().replace('-', '_')
            if sv == 'northamerica':
                sv = 'north_america'
            m[str(k)] = sv
        return m

    def _rtt_map(self) -> dict[tuple[str, str], int]:
        raw = self._wan_cfg.get('rtt_ms') if isinstance(self._wan_cfg, dict) else None
        if raw is not None and not isinstance(raw, dict):
            raise BenchError('cloudlab_wan.rtt_ms must be an object', TypeError(type(raw)))
        return _parse_rtt_matrix(raw)

    def setup(self) -> None:
        host_info = self.manager.get_host_info()
        oct2 = self._octet2_site()
        rtt_map = self._rtt_map()
        plan = _build_plan(host_info, oct2, rtt_map)

        Print.heading('CloudLab WAN: tc netem (symmetric RTT/2 per hop)')
        Print.info(f'octet2_site map: {oct2}')
        Print.info(
            'RTT samples (ms): EU-NA=%s EU-AS=%s NA-AS=%s'
            % (
                rtt_map.get(('europe', 'north_america')),
                rtt_map.get(('europe', 'asia')),
                rtt_map.get(('north_america', 'asia')),
            )
        )

        conn_kw = self._conn_kwargs()
        for (username, port), hosts in _group_hosts(host_info).items():
            for h in hosts:
                ip = _host_ipv4(h['hostname'])
                payload = {'my_ip': ip, 'peers': plan[ip]['peers']}
                b64 = base64.b64encode(json.dumps(payload).encode()).decode('ascii')
                script = _remote_setup_shell(b64)
                c = Connection(
                    h['hostname'], user=username, port=port, connect_kwargs=conn_kw, connect_timeout=30
                )
                Print.info(f'WAN setup {username}@{ip} site={plan[ip]["site"]} ...')
                r = c.run(script, hide=False, warn=True)
                c.close()
                if not r.ok:
                    raise BenchError(f'WAN setup failed on {ip}: {r.stderr or r.stdout}', RuntimeError('wan'))

        Print.info('Done. Egress to peer experiment IPs is delayed; TCP/22 bypasses netem (iptables).')

    def clear(self) -> None:
        host_info = self.manager.get_host_info()
        conn_kw = self._conn_kwargs()
        Print.heading('CloudLab WAN: clear tc + iptables')
        script = _remote_clear_shell()
        try:
            for (username, port), hosts in _group_hosts(host_info).items():
                names = [h['hostname'] for h in hosts]
                g = Group(*names, user=username, port=port, connect_kwargs=conn_kw, connect_timeout=30)
                g.run(script, hide=True, warn=True)
        except GroupException as e:
            Print.warn(f'cloudlab_wan clear: {e}')
        Print.info('cloudlab_wan: cleared.')
