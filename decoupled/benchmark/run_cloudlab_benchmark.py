#!/usr/bin/env python3
"""
Script to run CloudLab benchmark and post-process logs.

Generated artifacts are stored under:
benchmark/decouple/<network_tag>/<workload_tag>/<run_id>/
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Add benchmark directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from benchmark.logs import LogParser, ParseError
from benchmark.utils import PathMaker, Print

try:
    from time_storage_from_logs import process_node_log

    TIME_STORAGE_AVAILABLE = True
except ImportError:
    TIME_STORAGE_AVAILABLE = False


def _default_run_context():
    return {
        'network_tag': 'default_network',
        'workload_tag': 'default_workload',
        'run_id': 'default_network_default_workload_default_run_n0_r0_run1',
        'nodes': 10,
    }


def load_run_context():
    context_path = Path(PathMaker.run_context_file())
    if context_path.exists():
        try:
            with context_path.open('r') as handle:
                data = json.load(handle)
            if 'run_id' not in data:
                data['run_id'] = PathMaker.run_folder_name(
                    data.get('network_tag', 'default_network'),
                    data.get('workload_tag', 'default_workload'),
                    data.get('base_run_id', 'legacy_run'),
                    data.get('nodes', 0),
                    data.get('rate', 0),
                    data.get('run_index', 1),
                )
            return data
        except Exception as e:
            Print.warn(f'Failed to load run context from {context_path}: {e}')
    return _default_run_context()


def _results_dir(run_context):
    directory = Path(
        PathMaker.experiment_path(
            run_context['network_tag'],
            run_context['workload_tag'],
            run_context.get('run_id'),
        )
    )
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _summary_path(run_context):
    return Path(
        PathMaker.summary_file(
            run_context['network_tag'],
            run_context['workload_tag'],
            run_context.get('run_id'),
        )
    )


def _analysis_path(run_context, experiment_group=None):
    return Path(
        PathMaker.analysis_csv_file(
            run_context['network_tag'],
            run_context['workload_tag'],
            run_context.get('run_id'),
            experiment_group=experiment_group,
        )
    )


def _annotate_summary_with_run_context(summary_text, run_context):
    config_marker = ' + CONFIG:\n'
    if config_marker not in summary_text:
        return summary_text

    context_lines = []

    def _format_percentages(values):
        return '[' + ', '.join(f'{value:.2%}' for value in values) + ']'

    network_tag = run_context.get('network_tag')
    if network_tag:
        context_lines.append(f' Network tag: {network_tag}\n')

    workload_tag = run_context.get('workload_tag')
    if workload_tag:
        context_lines.append(f' Workload tag: {workload_tag}\n')

    rate_type = run_context.get('rate_type')
    if rate_type:
        context_lines.append(f' Rate type: {rate_type}\n')

    run_index = run_context.get('run_index')
    runs_total = run_context.get('runs_total')
    if run_index is not None and runs_total is not None:
        context_lines.append(f' Run index: {run_index}/{runs_total}\n')

    run_id = run_context.get('run_id')
    if run_id:
        context_lines.append(f' Run folder: {run_id}\n')

    if rate_type == 'custom':
        raw_percentages = run_context.get('custom_percentages')
        if raw_percentages:
            context_lines.append(
                f' Custom percentages (raw): {raw_percentages}\n'
            )

        normalized_percentages = run_context.get('custom_percentages_normalized')
        if normalized_percentages:
            context_lines.append(
                ' Custom percentages (normalized): '
                f'{_format_percentages(normalized_percentages)}\n'
            )

        actual_node_loads = run_context.get('custom_actual_node_loads')
        if actual_node_loads:
            context_lines.append(
                f' Custom actual node loads (tx/s): {actual_node_loads}\n'
            )

    if not context_lines:
        return summary_text

    return summary_text.replace(
        config_marker,
        config_marker + ''.join(context_lines),
        1,
    )


def run_fab_command(task='cloudlab_remote', debug=False):
    fab_cmd = ['fab', task]
    if debug:
        fab_cmd.append('debug=True')

    Print.info(f'Running: {" ".join(fab_cmd)}')
    Print.info('=' * 60)

    try:
        result = subprocess.run(
            fab_cmd,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            stderr = (result.stderr or '').strip()
            stdout = (result.stdout or '').strip()
            details = stderr or stdout
            if details:
                Print.warn(details[-2000:])
        return result.returncode == 0
    except FileNotFoundError:
        Print.warn('fab command not found. Please install fabric: pip install fabric')
        return False
    except Exception as e:
        Print.warn(f'Failed to run fab command: {e}')
        return False


def download_logs_if_needed(settings_file='cloudlab_settings.json', max_workers=1):
    logs_dir = Path(PathMaker.logs_path())

    primary_logs = list(logs_dir.glob('primary-*.log'))
    worker_logs = list(logs_dir.glob('worker-*.log'))
    client_logs = list(logs_dir.glob('client-*.log'))

    if primary_logs or worker_logs or client_logs:
        Print.info(
            f'Found existing logs: {len(primary_logs)} primary, '
            f'{len(worker_logs)} worker, {len(client_logs)} client'
        )
        return True

    Print.info('No local logs found, attempting to download from remote nodes...')
    try:
        from download_logs import download_logs

        return download_logs(settings_file, max_workers)
    except ImportError:
        Print.warn('download_logs.py not found, skipping download')
        return False


def process_logs(run_context, faults=0, save_to_file=True, logs_dir=None):
    logs_dir = logs_dir or PathMaker.logs_path()

    if not os.path.exists(logs_dir):
        Print.warn(f'Logs directory not found: {logs_dir}')
        return False

    try:
        parser = LogParser.process(logs_dir, faults=faults)
        result = _annotate_summary_with_run_context(parser.result(), run_context)
        print(result)

        if save_to_file:
            _results_dir(run_context)
            summary_file = _summary_path(run_context)
            summary_file.write_text(result)

        return True
    except ParseError as e:
        Print.warn(f'Failed to parse logs: {e}')
        Print.warn('This may be because some log files are empty or incomplete.')
        return False
    except Exception as e:
        Print.warn(f'Error processing logs: {e}')
        return False


def generate_analysis_csv(run_context, num_nodes=10, experiment_group=None, logs_dir=None):
    if not TIME_STORAGE_AVAILABLE:
        Print.warn('time_storage_from_logs module not available, skipping CSV generation')
        return False

    logs_dir = logs_dir or PathMaker.logs_path()
    csv_filename = _analysis_path(run_context, experiment_group=experiment_group)

    try:
        benchmark_dir = os.path.dirname(os.path.abspath(__file__))
        original_cwd = os.getcwd()
        os.chdir(benchmark_dir)

        try:
            _results_dir(run_context)

            if csv_filename.exists():
                csv_filename.unlink()

            Print.info(f'Processing {num_nodes} nodes from logs directory: {logs_dir}')
            Print.info('')

            for node_id in range(num_nodes):
                process_node_log(node_id, str(csv_filename), num_nodes, logs_dir=logs_dir)

            return True
        finally:
            os.chdir(original_cwd)
    except Exception as e:
        Print.warn(f'Error generating analysis CSV: {e}')
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Run CloudLab benchmark and process logs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        '--no-run',
        action='store_true',
        help='Skip running fab cloudlab_remote, only process existing logs',
    )
    parser.add_argument(
        '--download-only',
        action='store_true',
        help='Only download logs from remote nodes, do not run benchmark or process',
    )
    parser.add_argument('--debug', action='store_true', help='Run benchmark in debug mode')
    parser.add_argument('--faults', type=int, default=0, help='Number of faulty nodes (default: 0)')
    parser.add_argument('--no-save', action='store_true', help='Do not save results to file')
    parser.add_argument(
        '--max-workers',
        type=int,
        default=1,
        help='Maximum number of workers per node for log download (default: 1)',
    )
    parser.add_argument(
        '--settings',
        default='cloudlab_settings.json',
        help='Path to CloudLab settings file (default: cloudlab_settings.json)',
    )
    parser.add_argument(
        '--num-nodes',
        type=int,
        default=10,
        help='Number of nodes to process for analysis CSV (default: 10)',
    )
    parser.add_argument(
        '--experiment-groups',
        type=int,
        nargs='+',
        default=None,
        help='Experiment group numbers to process (e.g., --experiment-groups 1 2 3).',
    )
    parser.add_argument(
        '--no-pivot',
        action='store_true',
        help='Deprecated; pivot generation has been removed and only analysis CSV is generated',
    )
    parser.add_argument(
        '--logs-dir',
        default=None,
        help='Custom logs directory path (default: uses PathMaker.logs_path() or "logs")',
    )

    args = parser.parse_args()

    Print.heading('CloudLab Benchmark Runner')

    run_context = load_run_context()
    success = True

    if not args.no_run and not args.download_only:
        success = run_fab_command('cloudlab_remote', debug=args.debug)
        if not success:
            Print.warn('Benchmark run completed with errors, but continuing to process logs...')
        run_context = load_run_context()

    if not args.download_only:
        download_logs_if_needed(args.settings, args.max_workers)
    else:
        Print.info('Download-only mode: downloading logs from remote nodes...')
        download_logs_if_needed(args.settings, args.max_workers)
        Print.info('Download complete. Exiting.')
        return 0

    if not args.no_save:
        success = process_logs(
            run_context,
            faults=args.faults,
            save_to_file=True,
            logs_dir=args.logs_dir,
        ) and success
    else:
        success = process_logs(
            run_context,
            faults=args.faults,
            save_to_file=False,
            logs_dir=args.logs_dir,
        ) and success

    if args.experiment_groups:
        for exp_group in args.experiment_groups:
            exp_logs_dir = args.logs_dir
            if exp_logs_dir is None:
                candidate = f'logs_exp{exp_group}'
                exp_logs_dir = candidate if os.path.exists(candidate) else None

            csv_success = generate_analysis_csv(
                run_context,
                num_nodes=args.num_nodes,
                experiment_group=exp_group,
                logs_dir=exp_logs_dir,
            )
            success = csv_success and success
    else:
        csv_success = generate_analysis_csv(
            run_context,
            num_nodes=args.num_nodes,
            logs_dir=args.logs_dir,
        )
        success = csv_success and success

    if success:
        Print.info('✓ All operations completed successfully')
        return 0

    Print.warn('⚠ Some operations completed with errors')
    return 1


if __name__ == '__main__':
    sys.exit(main())

