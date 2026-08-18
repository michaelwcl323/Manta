#!/usr/bin/env python3

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROTOCOLS = {
    'manta': {
        'directory': 'manta',
        'result_directory': 'Manta-result',
        'patterns': ('*/*/summary_*.txt',),
    },
    'chitu': {
        'directory': 'chitu',
        'result_directory': 'Chitu-results',
        'patterns': ('results/*.txt',),
    },
    'tusk': {
        'directory': 'tusk',
        'result_directory': 'Tusk-result',
        'patterns': ('*/*/summary_*.txt',),
    },
    'dag-rider': {
        'directory': 'tusk',
        'result_directory': 'DAG-rider-result',
        'patterns': ('*/*/summary_*.txt',),
    },
    'mahi-mahi': {
        'directory': 'mahi-mahi',
        'result_directory': 'Mahi-mahi-result',
        'patterns': ('results/*.txt',),
    },
}


def load_yaml(path):
    try:
        import yaml
    except ImportError as error:
        raise RuntimeError(
            'PyYAML is required; run: python -m pip install PyYAML'
        ) from error
    with path.open(encoding='utf-8') as config_file:
        config = yaml.safe_load(config_file) or {}
    enabled = config.get('protocols_enabled')
    if not isinstance(enabled, list) or not enabled:
        raise ValueError('experiment.yml must contain a non-empty protocols_enabled list')
    if not isinstance(config.get('protocols'), dict):
        raise ValueError('experiment.yml must contain a protocols mapping')
    return config


def result_files(benchmark_dir, patterns):
    files = set()
    for pattern in patterns:
        files.update(path for path in benchmark_dir.glob(pattern) if path.is_file())
    return files


def fingerprints(paths):
    return {path: (path.stat().st_mtime_ns, path.stat().st_size) for path in paths}


def archive_changed(benchmark_dir, protocol, before, results_root):
    metadata = PROTOCOLS[protocol]
    current = result_files(benchmark_dir, metadata['patterns'])
    changed = [
        path for path in current
        if path not in before
        or before[path] != (path.stat().st_mtime_ns, path.stat().st_size)
    ]
    destination = results_root / metadata['result_directory']
    destination.mkdir(parents=True, exist_ok=True)
    archived = []
    for source in sorted(changed):
        target = destination / source.name
        shutil.copy2(source, target)
        archived.append(target)
        print(f'Archived {source} -> {target}')
    return archived


def parse_args():
    parser = argparse.ArgumentParser(
        description='Run the five Figure 11(b) protocols sequentially'
    )
    parser.add_argument('--config', default='experiment.yml')
    parser.add_argument(
        '--plot', action='store_true',
        help='Generate the comparison PDF after all selected runs succeed',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Validate configuration and print selected protocols without running Fabric',
    )
    return parser.parse_args()


def main():
    args = parse_args()
    repo_root = Path(__file__).resolve().parent
    config_path = (repo_root / args.config).resolve()
    config = load_yaml(config_path)
    results_root = repo_root / 'results'
    results_root.mkdir(parents=True, exist_ok=True)
    for metadata in PROTOCOLS.values():
        (results_root / metadata['result_directory']).mkdir(
            parents=True, exist_ok=True
        )

    selected = config['protocols_enabled']
    unknown = [protocol for protocol in selected if protocol not in PROTOCOLS]
    if unknown:
        raise ValueError(f'unknown protocols in protocols_enabled: {unknown}')
    if len(selected) != len(set(selected)):
        raise ValueError('protocols_enabled must not contain duplicates')
    environment = os.environ.copy()
    environment['MANTA_EXPERIMENT_CONFIG'] = str(config_path)
    successful = []
    failed = []
    for protocol in selected:
        protocol_config = config['protocols'].get(protocol)
        if protocol_config is None:
            raise ValueError(f'protocol {protocol!r} is missing from {config_path}')
        metadata = PROTOCOLS[protocol]
        benchmark_dir = repo_root / metadata['directory'] / 'benchmark'
        if args.dry_run:
            bench = dict(config.get('benchmark', {}) or {})
            bench.update(protocol_config.get('benchmark', {}) or {})
            print(
                f'{protocol}: cwd={benchmark_dir}, '
                f'nodes={bench.get("nodes", "fabfile default")}, '
                f'rates={bench.get("rate", "fabfile default")}, '
                f'runs={bench.get("runs", "fabfile default")}'
            )
            continue
        try:
            protocol_environment = environment.copy()
            protocol_environment['MANTA_PROTOCOL'] = protocol
            before_paths = result_files(benchmark_dir, metadata['patterns'])
            before = fingerprints(before_paths)
            print(f'\n=== Running {protocol} ===', flush=True)
            print(f'--- {protocol}: fab install ---', flush=True)
            subprocess.run(
                ['fab', 'install'], cwd=benchmark_dir,
                env=protocol_environment, check=True
            )
            print(f'--- {protocol}: fab remote ---', flush=True)
            subprocess.run(
                ['fab', 'remote'], cwd=benchmark_dir,
                env=protocol_environment, check=True
            )
            archived = archive_changed(
                benchmark_dir, protocol, before, results_root
            )
            if not archived:
                raise RuntimeError(
                    f'{protocol} produced no new or updated summaries'
                )
            failed_summaries = [
                path for path in archived
                if 'Status: FAILED' in path.read_text(
                    encoding='utf-8', errors='replace'
                )
            ]
            if failed_summaries:
                names = ', '.join(path.name for path in failed_summaries)
                raise RuntimeError(
                    f'{protocol} produced failed summaries: {names}'
                )
            successful.append(protocol)
            print(f'=== Completed {protocol} successfully ===', flush=True)
        except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
            failed.append((protocol, str(error)))
            print(
                f'=== FAILED {protocol}: {error}; continuing to next protocol ===',
                file=sys.stderr,
                flush=True,
            )

    if args.plot and not args.dry_run:
        try:
            subprocess.run(
                [sys.executable, str(results_root / 'plot_mean_tps_latency.py')],
                cwd=repo_root,
                check=True,
            )
        except subprocess.CalledProcessError as error:
            failed.append(('plot', str(error)))

    if not args.dry_run:
        print(f'\nSuccessful protocols: {" ".join(successful) or "none"}')
        print(
            'Failed protocols: '
            + ('; '.join(f'{name}: {error}' for name, error in failed) or 'none')
        )
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
