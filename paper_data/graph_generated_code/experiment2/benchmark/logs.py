# Minimal extract for paper figure regeneration.
import os
from datetime import datetime
from re import search


def _to_posix_utc(string):
    x = datetime.fromisoformat(string.replace('Z', '+00:00'))
    return datetime.timestamp(x)


def parse_primary_log_markers(log_path):
    if not log_path or not os.path.exists(log_path):
        return {}

    markers = {
        'boot_ts': None,
        'first_created_ts': None,
        'attack_start_ts': None,
        'attack_end_ts': None,
    }

    with open(log_path, 'r', errors='replace') as f:
        for line in f:
            if markers['boot_ts'] is None:
                match = search(r'\[(.*Z) .* booted on (\d+.\d+.\d+.\d+)', line)
                if match is not None:
                    markers['boot_ts'] = _to_posix_utc(match.group(1))

            if markers['first_created_ts'] is None:
                match = search(r'\[(.*Z) .* Created B\d+\([^ ]+\) -> ([^ ]+=)', line)
                if match is not None:
                    markers['first_created_ts'] = _to_posix_utc(match.group(1))

            if markers['attack_start_ts'] is None and 'start attack' in line:
                match = search(r'\[(.*Z) ', line)
                if match is not None:
                    markers['attack_start_ts'] = _to_posix_utc(match.group(1))

            if markers['attack_end_ts'] is None and 'end attack' in line:
                match = search(r'\[(.*Z) ', line)
                if match is not None:
                    markers['attack_end_ts'] = _to_posix_utc(match.group(1))

            if all(value is not None for value in markers.values()):
                break

    return markers
