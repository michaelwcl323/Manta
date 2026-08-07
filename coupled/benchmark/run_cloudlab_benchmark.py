#!/usr/bin/env python3
"""
Run CloudLab benchmarks and post-process logs using the current run directory layout.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Add benchmark directory to path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from benchmark.logs import LogParser, ParseError
from benchmark.origin_mapping import build_origin_mapping_from_files
from benchmark.utils import PathMaker, Print

try:
    from time_storage_from_logs import process_node_log, export_round_end_pivot_table

    TIME_STORAGE_AVAILABLE = True
except ImportError:
    TIME_STORAGE_AVAILABLE = False


def _default_run_context(run_id=None):
    return {
        'run_id': run_id or PathMaker.run_id(),
        'network_tag': 'default',
        'workload_tag': 'default',
        'nodes': 10,
    }


def load_run_context(run_id=None):
    context_path = Path(PathMaker.run_context_file())
    if context_path.exists():
        try:
            with context_path.open('r') as handle:
                data = json.load(handle)
            if run_id and 'run_id' not in data:
                data['run_id'] = run_id
            return data
        except Exception as e:
            Print.warn(f'Failed to load run context from {context_path}: {e}')
    return _default_run_context(run_id=run_id)


def _ensure_experiment_dir(run_context):
    experiment_dir = Path(
        PathMaker.experiment_path(
            run_context['network_tag'],
            run_context['workload_tag'],
            run_context['run_id'],
        )
    )
    experiment_dir.mkdir(parents=True, exist_ok=True)
    return experiment_dir


def _summary_path(run_context):
    return Path(
        PathMaker.summary_file(
            run_context['network_tag'],
            run_context['workload_tag'],
            run_context['run_id'],
        )
    )


def _analysis_path(run_context, experiment_group=None):
    return Path(
        PathMaker.analysis_csv_file(
            run_context['network_tag'],
            run_context['workload_tag'],
            run_context['run_id'],
            experiment_group=experiment_group,
        )
    )


def _pivot_path(run_context, experiment_group=None):
    return Path(
        PathMaker.pivot_csv_file(
            run_context['network_tag'],
            run_context['workload_tag'],
            run_context['run_id'],
            experiment_group=experiment_group,
        )
    )


def _build_origin_mapping_entries(settings_file='cloudlab_settings.json'):
    committee_path = Path(PathMaker.committee_file())
    settings_path = Path(settings_file)

    if not committee_path.exists() or not settings_path.exists():
        return []

    try:
        return build_origin_mapping_from_files(committee_path, settings_path)
    except Exception as e:
        Print.warn(f'Failed to build origin mapping: {e}')
        return []


def _annotate_summary_with_run_context(summary_text, run_context):
    config_marker = ' + CONFIG:\n'
    if config_marker not in summary_text:
        return summary_text

    def _format_number_list(values, decimals=None, as_percent=False):
        parts = []
        for value in values:
            if isinstance(value, float) and decimals is not None:
                rendered = f'{value * 100:.{decimals}f}%' if as_percent else f'{value:.{decimals}f}'
            else:
                rendered = str(value)
            parts.append(rendered)
        return '[' + ', '.join(parts) + ']'

    context_lines = []
    network_tag = run_context.get('network_tag')
    workload_tag = run_context.get('workload_tag')
    rate_type = run_context.get('rate_type')
    workload_details = run_context.get('workload_details') or {}
    if network_tag is not None:
        context_lines.append(f' Network tag: {network_tag}\n')
    if workload_tag is not None:
        context_lines.append(f' Workload tag: {workload_tag}\n')
    if rate_type is not None:
        context_lines.append(f' Rate type: {rate_type}\n')

    percentages_raw = workload_details.get('percentages_raw')
    if percentages_raw is None:
        percentages_raw = run_context.get('percentages')
    if percentages_raw is not None:
        context_lines.append(f' Workload percentages: {percentages_raw}\n')

    base_total_rate = workload_details.get('base_total_rate')
    if base_total_rate is None:
        base_total_rate = run_context.get('rate')
    if base_total_rate is not None:
        context_lines.append(f' Workload base total rate: {base_total_rate} tx/s\n')

    extra_rate_total = workload_details.get('extra_rate_total')
    if extra_rate_total is None:
        extra_rate_total = run_context.get('extra_rate')
    if extra_rate_total is not None:
        context_lines.append(
            f' Workload extra rate total: {extra_rate_total} tx/s\n'
        )

    effective_total_rate = workload_details.get('effective_total_rate')
    if effective_total_rate is not None:
        context_lines.append(f' Workload effective total rate: {effective_total_rate} tx/s\n')

    normalized = workload_details.get('percentages_normalized')
    if normalized is not None:
        context_lines.append(
            ' Workload normalized shares: '
            + _format_number_list(normalized, decimals=2, as_percent=True)
            + '\n'
        )

    zipf_s = workload_details.get('zipf_s')
    if zipf_s is not None:
        context_lines.append(f' Workload skew s: {zipf_s}\n')

    extreme_x = workload_details.get('extreme_x')
    if extreme_x is None:
        extreme_x = run_context.get('extreme_x')
    if extreme_x is not None:
        context_lines.append(f' Workload extreme_x: {extreme_x}\n')

    node_rates = workload_details.get('node_rates')
    if node_rates is not None:
        context_lines.append(f' Workload node rates (tx/s): {node_rates}\n')

    base_node_rates = workload_details.get('base_node_rates')
    if base_node_rates is not None:
        context_lines.append(f' Workload base node rates (tx/s): {base_node_rates}\n')

    percentage_node_rates = workload_details.get('percentage_node_rates')
    if percentage_node_rates is not None:
        context_lines.append(
            f' Workload percentage node rates (tx/s): {percentage_node_rates}\n'
        )

    worker_rates = workload_details.get('worker_rates')
    if worker_rates is not None:
        context_lines.append(f' Workload worker/client rates (tx/s): {worker_rates}\n')

    if not context_lines:
        return summary_text

    return summary_text.replace(
        config_marker,
        config_marker + ''.join(context_lines),
        1,
    )


def run_fab_command(task='cloudlab-remote', debug=False, env=None):
    fab_cmd = ['fab', task]
    if debug:
        fab_cmd.append('debug=True')

    Print.info(f'Running: {" ".join(fab_cmd)}')
    Print.info('=' * 60)

    try:
        result = subprocess.run(
            fab_cmd,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=env,
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        Print.warn('fab command not found. Please install fabric')
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


def process_logs(run_context, faults=0, save_to_file=True, logs_dir=None, settings_file='cloudlab_settings.json'):
    logs_dir = logs_dir or PathMaker.logs_path()

    if not os.path.exists(logs_dir):
        Print.warn(f'Logs directory not found: {logs_dir}')
        return False

    Print.info('=' * 60)
    Print.info(f"Processing logs for {run_context['run_id']}...")
    Print.info('=' * 60)

    try:
        parser = LogParser.process(logs_dir, faults=faults)
        parser.origin_mapping = _build_origin_mapping_entries(settings_file=settings_file)
        if parser.origin_mapping:
            parser.origin_mapping_note = (
                'Origin map derived from the current committee/settings files.'
            )
        terminal_result = _annotate_summary_with_run_context(
            parser.result(
                include_vertex_stats=False,
                include_origin_mapping=False,
            ),
            run_context,
        )
        print(terminal_result)

        result = _annotate_summary_with_run_context(
            parser.result(include_origin_mapping=False),
            run_context,
        )

        if save_to_file:
            _ensure_experiment_dir(run_context)

            summary_file = _summary_path(run_context)
            with summary_file.open('w') as handle:
                handle.write(result)
            Print.info(f'\nResults saved to: {summary_file}')

        return True
    except ParseError as e:
        Print.warn(f'Failed to parse logs: {e}')
        Print.warn('This may be because some log files are empty or incomplete.')
        return False
    except Exception as e:
        Print.warn(f'Error processing logs: {e}')
        return False


def generate_round_end_time_pivot(
    run_context,
    num_nodes=10,
    experiment_group=None,
    logs_dir=None,
    settings_file='cloudlab_settings.json',
):
    if not TIME_STORAGE_AVAILABLE:
        Print.warn('time_storage_from_logs module not available, skipping CSV generation')
        return False

    logs_dir = logs_dir or PathMaker.logs_path()
    csv_filename = _analysis_path(run_context, experiment_group=experiment_group)
    pivot_filename = _pivot_path(run_context, experiment_group=experiment_group)

    Print.info('=' * 60)
    Print.info(f"Generating CSV artifacts for {run_context['run_id']}...")
    Print.info('=' * 60)

    try:
        _ensure_experiment_dir(run_context)
        origin_mapping = _build_origin_mapping_entries(settings_file=settings_file)

        if csv_filename.exists():
            csv_filename.unlink()

        Print.info(f'Processing {num_nodes} nodes from logs directory: {logs_dir}')
        Print.info('')

        for node_id in range(num_nodes):
            process_node_log(
                node_id,
                str(csv_filename),
                num_nodes,
                logs_dir=logs_dir,
                origin_mapping=origin_mapping,
            )

        Print.info('=' * 60)
        Print.info(f'Analysis complete! Results saved to: {csv_filename}')

        Print.info('\nGenerating round end time pivot table...')
        export_round_end_pivot_table(str(csv_filename), str(pivot_filename))

        Print.info(f'Round end time pivot table saved to: {pivot_filename}')
        Print.info('=' * 60)
        return True
    except Exception as e:
        Print.warn(f'Error generating CSV artifacts: {e}')
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Run CloudLab benchmark and process logs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument('--no-run', action='store_true', help='Skip running fab cloudlab-remote')
    parser.add_argument(
        '--download-only',
        action='store_true',
        help='Only download logs from remote nodes, do not process them',
    )
    parser.add_argument('--debug', action='store_true', help='Run benchmark in debug mode')
    parser.add_argument('--faults', type=int, default=0, help='Number of faulty nodes')
    parser.add_argument('--no-save', action='store_true', help='Do not save summary to file')
    parser.add_argument(
        '--max-workers',
        type=int,
        default=1,
        help='Maximum number of workers per node for log download',
    )
    parser.add_argument(
        '--settings',
        default='cloudlab_settings.json',
        help='Path to CloudLab settings file',
    )
    parser.add_argument('--num-nodes', type=int, default=10, help='Number of nodes to process')
    parser.add_argument(
        '--experiment-groups',
        type=int,
        nargs='+',
        default=None,
        help='Optional experiment group identifiers',
    )
    parser.add_argument('--no-pivot', action='store_true', help='Skip CSV / pivot generation')
    parser.add_argument('--logs-dir', default=None, help='Custom logs directory path')

    args = parser.parse_args()

    Print.heading('CloudLab Benchmark Runner')
    Print.info('=' * 60)

    env_run_id = os.environ.get('NARWHAL_BENCH_RUN_ID')
    if not args.no_run and not args.download_only:
        base_run_id = env_run_id or PathMaker.run_id()
    else:
        base_run_id = env_run_id
    run_context = load_run_context(run_id=base_run_id)
    success = True

    if not args.no_run and not args.download_only:
        run_env = os.environ.copy()
        run_env['NARWHAL_BENCH_RUN_ID'] = run_context['run_id']
        success = run_fab_command('cloudlab_remote', debug=args.debug, env=run_env)
        if not success:
            Print.warn('Benchmark run completed with errors, but continuing to process logs...')
        run_context = load_run_context(run_id=run_context['run_id'])

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
            settings_file=args.settings,
        ) and success
    else:
        success = process_logs(
            run_context,
            faults=args.faults,
            save_to_file=False,
            logs_dir=args.logs_dir,
            settings_file=args.settings,
        ) and success

    if not args.no_pivot:
        if args.experiment_groups:
            for exp_group in args.experiment_groups:
                exp_success = generate_round_end_time_pivot(
                    run_context,
                    num_nodes=args.num_nodes,
                    experiment_group=exp_group,
                    logs_dir=args.logs_dir,
                    settings_file=args.settings,
                )
                success = exp_success and success
        else:
            pivot_success = generate_round_end_time_pivot(
                run_context,
                num_nodes=args.num_nodes,
                logs_dir=args.logs_dir,
                settings_file=args.settings,
            )
            success = pivot_success and success

    Print.info('=' * 60)
    if success:
        Print.info('✓ All operations completed successfully')
        return 0

    Print.warn('⚠ Some operations completed with errors')
    return 1


if __name__ == '__main__':
    sys.exit(main())

