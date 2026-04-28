# Copyright(C) Facebook, Inc. and its affiliates.
"""
CloudLab Settings

Configuration for CloudLab deployment.
"""

from json import load, JSONDecodeError


class CloudLabSettingsError(Exception):
    pass


class CloudLabSettings:
    """Settings for CloudLab deployment"""

    def __init__(self, key_path, base_port, repo_name, repo_url, branch, hosts):
        # Validate inputs
        inputs_str = [key_path, repo_name, repo_url, branch]
        ok = all(isinstance(x, str) for x in inputs_str)
        ok &= isinstance(base_port, int)
        ok &= isinstance(hosts, list)
        ok &= len(hosts) > 0

        # Validate each host entry
        for host in hosts:
            if not isinstance(host, dict):
                ok = False
                break
            if 'hostname' not in host:
                ok = False
                break
            if not isinstance(host['hostname'], str):
                ok = False
                break

        if not ok:
            raise CloudLabSettingsError('Invalid settings types')

        self.key_path = key_path
        self.base_port = base_port
        self.repo_name = repo_name
        self.repo_url = repo_url
        self.branch = branch
        self.hosts = hosts

    @classmethod
    def load(cls, filename='cloudlab_settings.json'):
        """Load settings from JSON file.
        Supports both formats:
        - remote_setup format: ssh_key_path, servers, setup_commands, port, repo
        - legacy format: key.path, hosts, port, repo
        """
        try:
            with open(filename, 'r') as f:
                data = load(f)

            # Support remote_setup format (ssh_key_path, servers)
            key_path = data.get('ssh_key_path') or data['key']['path']
            hosts = data.get('servers') or data['hosts']
            # Map servers to hosts: servers use 'description', hosts use 'region'
            hosts = [
                {**h, 'region': h.get('region') or h.get('description', 'default')}
                for h in hosts
            ]

            return cls(
                key_path,
                data['port'],
                data['repo']['name'],
                data['repo']['url'],
                data['repo']['branch'],
                hosts,
            )
        except (OSError, JSONDecodeError) as e:
            raise CloudLabSettingsError(f'Failed to read settings file: {e}')
        except KeyError as e:
            raise CloudLabSettingsError(f'Malformed settings: missing key {e}')
