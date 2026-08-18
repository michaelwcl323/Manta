import ipaddress
import os
from collections import OrderedDict
from json import load, JSONDecodeError


class SettingsError(Exception):
    pass


def _validate_ip(s):
    s = str(s).strip()
    if not s:
        raise SettingsError('Empty IP in hosts list')
    try:
        ipaddress.ip_address(s)
    except ValueError as e:
        raise SettingsError(f'Invalid IP address: {s!r}') from e
    return s


class Settings:
    def __init__(self, key_name, key_path, base_port, repo_name, repo_url,
                 branch, instance_type, aws_regions,
                 static_hosts_by_region=None, repo_subdir='',
                 key_passphrase=None):
        inputs_str = [
            key_name, key_path, repo_name, repo_url, branch, instance_type
        ]
        if isinstance(aws_regions, list):
            regions = aws_regions
        else:
            regions = [aws_regions]
        inputs_str += regions
        ok = all(isinstance(x, str) for x in inputs_str)
        ok &= isinstance(base_port, int)
        ok &= len(regions) > 0
        if not ok:
            raise SettingsError('Invalid settings types')

        self.key_name = key_name
        self.key_path = key_path
        self.key_passphrase = key_passphrase or None

        self.base_port = base_port

        self.repo_name = repo_name
        self.repo_subdir = repo_subdir
        self.repo_path = (
            f'{repo_name}/{repo_subdir}' if repo_subdir else repo_name
        )
        self.repo_url = repo_url
        self.branch = branch

        self.instance_type = instance_type
        self.aws_regions = regions
        self.static_hosts_by_region = static_hosts_by_region or OrderedDict()

    @classmethod
    def load(cls, filename, repo_subdir=None):
        try:
            with open(filename, 'r') as f:
                data = load(f)

            if 'regions' in data and 'ssh' in data and 'benchmark' in data:
                return cls._from_deployment_config(data, filename, repo_subdir)

            static_hosts_by_region = cls._parse_hosts_field(data.get('hosts'))

            return cls(
                data['key']['name'],
                data['key']['path'],
                data['port'],
                data['repo']['name'],
                data['repo']['url'],
                data['repo']['branch'],
                data['instances']['type'],
                data['instances']['regions'],
                static_hosts_by_region,
                data['repo'].get('subdir', ''),
                data['key'].get('passphrase'),
            )
        except (OSError, JSONDecodeError) as e:
            raise SettingsError(str(e))

        except KeyError as e:
            raise SettingsError(f'Malformed settings: missing key {e}')

    @classmethod
    def _from_deployment_config(cls, data, filename, repo_subdir=None):
        config_dir = os.path.dirname(os.path.abspath(filename))
        workspace_dir = os.path.dirname(config_dir)
        nodes_path = data['instances']['nodes_file']
        if not os.path.isabs(nodes_path):
            nodes_path = os.path.join(workspace_dir, nodes_path)

        hosts = OrderedDict((entry['name'], []) for entry in data['regions'])
        try:
            with open(nodes_path, 'r') as nodes_file:
                for line_number, line in enumerate(nodes_file, 1):
                    fields = line.split()
                    if not fields:
                        continue
                    if len(fields) != 4:
                        raise SettingsError(
                            f'Malformed {nodes_path}:{line_number}: expected '
                            'REGION INSTANCE_ID PUBLIC_IP PRIVATE_IP'
                        )
                    region, _, public_ip, _ = fields
                    if region not in hosts:
                        raise SettingsError(
                            f'Unknown region {region!r} in {nodes_path}:{line_number}'
                        )
                    hosts[region].append(_validate_ip(public_ip))
        except OSError as e:
            raise SettingsError(str(e)) from e

        hosts = OrderedDict((region, ips) for region, ips in hosts.items() if ips)
        if not hosts:
            raise SettingsError(
                f'No instance addresses found in {nodes_path}; run collect-addresses first'
            )

        public_key = os.path.expanduser(data['ssh']['public_key'])
        key_path = public_key[:-4] if public_key.endswith('.pub') else public_key
        repo = data['benchmark']['repo']
        return cls(
            data['ssh']['key_name'],
            key_path,
            data['benchmark']['base_port'],
            repo['name'],
            repo['url'],
            repo['branch'],
            data['instances']['type'],
            list(hosts.keys()),
            hosts,
            repo_subdir if repo_subdir is not None else repo.get('subdir', ''),
            data['ssh'].get('private_key_passphrase'),
        )

    @staticmethod
    def _parse_hosts_field(raw):
        '''Build region -> [ip] from settings "hosts".

        Supported shapes:
        - ["10.0.0.1", "10.0.0.2", ...]  — only IPs, single group `static`
        - {"ap-east-1": ["10.0.0.1", ...], ...}  — IPs grouped by region name
        - [{"region": "r", "ips": ["10.0.0.1", ...]}, ...]  — legacy list form
        '''
        if raw is None:
            return OrderedDict()
        out = OrderedDict()

        if isinstance(raw, list):
            if len(raw) == 0:
                return out
            if isinstance(raw[0], str):
                ips = [_validate_ip(x) for x in raw]
                out['static'] = ips
                return out
            if isinstance(raw[0], dict):
                for entry in raw:
                    if 'ips' in entry:
                        region = entry['region']
                        ips = [_validate_ip(x) for x in entry['ips']]
                    elif 'range' in entry:
                        raise SettingsError(
                            'Host "range" is no longer supported; use a list of IP strings only.'
                        )
                    else:
                        raise SettingsError(
                            'Each hosts[] item must include "region" and "ips" (list of IP strings).'
                        )
                    out[region] = ips
                return out
            raise SettingsError('hosts[] must be a list of IP strings, or list of {region, ips} objects.')

        if isinstance(raw, dict):
            for region, ips in raw.items():
                if not isinstance(ips, list):
                    raise SettingsError(
                        f'hosts.{region} must be a list of IP strings.'
                    )
                out[region] = [_validate_ip(x) for x in ips]
            return out

        raise SettingsError('hosts must be a list of IPs, a list of {region, ips}, or an object of region -> ips.')
