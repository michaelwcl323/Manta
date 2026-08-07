import json
from pathlib import Path


DEFAULT_SHORT_KEY_LENGTH = 16


def load_json(path):
    with Path(path).open('r') as handle:
        return json.load(handle)


def build_host_metadata(settings_json):
    hosts = settings_json.get('hosts', [])
    metadata = {}

    for node_id, host in enumerate(hosts):
        hostname = host['hostname'].split(':')[0]
        metadata[hostname] = {
            'node_id': node_id,
            'ip': hostname,
            'region': host.get('region', ''),
            'username': host.get('username', ''),
        }

    return metadata


def build_origin_mapping_entries(
    committee_json,
    settings_json,
    short_key_length=DEFAULT_SHORT_KEY_LENGTH,
):
    host_metadata = build_host_metadata(settings_json)
    entries = []

    for full_public_key, authority in committee_json['authorities'].items():
        primary_address = authority['primary']['primary_to_primary']
        ip = primary_address.split(':')[0]
        host_info = host_metadata.get(ip)
        if host_info is None:
            raise ValueError(f'Primary IP {ip} not found in CloudLab settings')

        entries.append({
            'node_id': host_info['node_id'],
            'ip': host_info['ip'],
            'region': host_info['region'],
            'username': host_info['username'],
            'short_public_key': full_public_key[:short_key_length],
            'full_public_key': full_public_key,
            'primary_address': primary_address,
        })

    return sorted(entries, key=lambda item: item['node_id'])


def build_origin_mapping_from_files(
    committee_path,
    settings_path,
    short_key_length=DEFAULT_SHORT_KEY_LENGTH,
):
    committee_json = load_json(committee_path)
    settings_json = load_json(settings_path)
    return build_origin_mapping_entries(
        committee_json,
        settings_json,
        short_key_length=short_key_length,
    )


def resolve_origin(origin_value, mapping_entries):
    if origin_value in ('', None):
        return {
            'node_id': '',
            'ip': '',
            'region': '',
            'short_public_key': '',
            'full_public_key': '',
        }

    exact_matches = [
        entry for entry in mapping_entries
        if origin_value == entry['short_public_key']
        or origin_value == entry['full_public_key']
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        raise ValueError(f'Ambiguous exact origin match for "{origin_value}"')

    prefix_matches = [
        entry for entry in mapping_entries
        if entry['full_public_key'].startswith(origin_value)
        or entry['short_public_key'].startswith(origin_value)
    ]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    if len(prefix_matches) > 1:
        raise ValueError(f'Ambiguous origin prefix "{origin_value}"')

    return {
        'node_id': 'UNKNOWN',
        'ip': 'UNKNOWN',
        'region': 'UNKNOWN',
        'short_public_key': origin_value,
        'full_public_key': origin_value,
    }


def mapping_payload(mapping_entries):
    return {'entries': mapping_entries}
