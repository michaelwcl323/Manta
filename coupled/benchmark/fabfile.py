# Copyright(C) Facebook, Inc. and its affiliates.
from fabric import task
from pathlib import Path
import re
import subprocess
from ast import literal_eval

from benchmark.local import LocalBench
from benchmark.logs import ParseError, LogParser
from benchmark.utils import BenchError, Print
from benchmark.cloudlab_wan import CloudLabWan
# Import AWS instance module only when needed (lazy import - CloudLab doesn't need it)
try:
    from benchmark.instance import InstanceManager
except ImportError:
    InstanceManager = None

# Import plot module only when needed (lazy import)
try:
    from benchmark.plot import Ploter, PlotError
except ImportError:
    Ploter = None
    PlotError = None


def _get_aws_bench():
    from benchmark.remote import Bench
    return Bench



def _get_cloudlab_instance_manager():
    from benchmark.cloudlab_instance import CloudLabInstanceManager
    return CloudLabInstanceManager


def _get_cloudlab_bench():
    from benchmark.cloudlab_remote import CloudLabBench
    return CloudLabBench


def _local_bench_params():
    return {
        'faults': 0,
        'nodes': 10,
        'workers': 1,
        # 'rate_type': 'balanced',
        # 'rate_type': 'imbalanced',
        'rate_type': 'custom',
        'percentages': [8, 8, 8, 8, 8, 1, 1, 1, 1, 1],
        'rate': [40000],
        # 'rate': 20000,
        'tx_size': 512,
        'duration': 90,
        # 'trigger_attack': True
    }

def _local_node_params():
    return {
        'header_size': 16_000,  # bytes, used when max_header_batches is not set
        # 'max_header_batches': _fair_header_batches(),  # fair comparison mode
        'max_header_delay': 15,  # ms
        'gc_depth': 50,  # rounds
        'sync_retry_delay': 10_000,  # ms
        'sync_retry_nodes': 4,  # number of nodes
        'batch_size': 16_000,  # bytes
        'max_batch_delay': 10,  # ms
        's': 2.5  # skew factor
    }


def _cloudlab_bench_params():
    return {
        'faults': 0,
        'nodes': [10],
        'workers': 1,
        'collocate': True,
        # 'rate_type': 'balanced',
        'rate_type': 'custom',
        'percentages': [1, 1, 1, 1, 6, 6, 6, 6, 6, 6],
         # 'percentages': [1, 1, 1, 1, 6, 6, 6, 6, 6, 6], # custom-high-3
        # 'percentages': [1, 1, 1, 1, 30, 30, 30, 30, 30, 30], # custom-high-5
        'rate': [20000,40000, 60000,80000,100000,120000,140000],
        'tx_size': 512,
        'duration': 120,
        'runs': 2,
        'workload_tag': 'custom-high-3',
        'network_tag': 'geo',
        # 'trigger_attack': [True],
    }


def _parse_bool_arg(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in ('1', 'true', 'yes', 'on'):
        return True
    if normalized in ('0', 'false', 'no', 'off'):
        return False
    raise ValueError(f'Invalid boolean value: {value}')


def _parse_percentages_arg(percentages):
    if percentages is None:
        return None
    if isinstance(percentages, list):
        return [int(x) for x in percentages]

    raw = str(percentages).strip()
    if not raw:
        return None

    try:
        parsed = literal_eval(raw)
    except (ValueError, SyntaxError):
        parsed = None

    if isinstance(parsed, (list, tuple)):
        return [int(x) for x in parsed]

    return [int(part.strip()) for part in raw.split(',') if part.strip()]


def _apply_cloudlab_bench_overrides(
    bench_params,
    *,
    network_tag=None,
    workload_tag=None,
    rate_type=None,
    percentages=None,
):
    updated = dict(bench_params)

    if network_tag is not None:
        updated['network_tag'] = str(network_tag)

    if rate_type is not None:
        updated['rate_type'] = str(rate_type)

    parsed_percentages = _parse_percentages_arg(percentages)
    if parsed_percentages is not None:
        updated['percentages'] = parsed_percentages

    active_rate_type = updated.get('rate_type')
    if active_rate_type == 'balanced':
        updated.pop('percentages', None)
        updated['workload_tag'] = str(workload_tag) if workload_tag is not None else 'balanced'
    elif active_rate_type == 'custom':
        if workload_tag is not None:
            updated['workload_tag'] = str(workload_tag)
        if 'percentages' not in updated or updated['percentages'] is None:
            raise ValueError('rate_type=custom requires percentages')
    elif workload_tag is not None:
        updated['workload_tag'] = str(workload_tag)

    return updated


def _cloudlab_node_params():
    return {
        'header_size': 1000,  # bytes, used when max_header_batches is not set
        'max_header_batches': _fair_header_batches(),  # fair comparison mode
        'max_header_delay': 50,  # ms
        'gc_depth': 50,  # rounds
        'sync_retry_delay': 1000,  # ms
        'sync_retry_nodes': 7,  # number of nodes
        'batch_size': 500000,  # bytes
        'max_batch_delay': 50,  # ms
        # 's':3
    }




def _fair_header_batches():
    # Original Narwhal's default `header_size=1000` on digest-only headers is roughly
    # 31 digests per header (1000 B / 32 B digest ~= 31).
    return 1





def _print_vertex_stats(limit=50):
    pattern = re.compile(
        r'VERTEX_STATS round=(?P<round>\d+) payload_entries=(?P<entries>\d+) '
        r'workload_bytes=(?P<workload>\d+) serialized_header_bytes=(?P<size>\d+)'
    )
    primary_logs = sorted(Path('logs').glob('primary-*.log'))
    if not primary_logs:
        Print.warn('No primary logs found under logs/')
        return

    rows_by_source = {}
    for log_path in primary_logs:
        source_rows = []
        with log_path.open('r', errors='replace') as handle:
            for line in handle:
                match = pattern.search(line)
                if match:
                    source_rows.append({
                        'source': log_path.name,
                        'round': int(match.group('round')),
                        'entries': int(match.group('entries')),
                        'workload': int(match.group('workload')),
                        'size': int(match.group('size')),
                    })
        if source_rows:
            rows_by_source[log_path.name] = source_rows

    if not rows_by_source:
        Print.warn('No VERTEX_STATS lines found in primary logs')
        return

    Print.heading('\nVertex stats from primary logs')
    for source, rows in rows_by_source.items():
        Print.info(f'\n[{source}]')
        shown_rows = rows if limit <= 0 else rows[:limit]
        for row in shown_rows:
            Print.info(
                f"round={row['round']}, "
                f"payload_entries={row['entries']}, "
                f"workload_bytes={row['workload']}, "
                f"serialized_header_bytes={row['size']}"
            )
        if limit > 0 and len(rows) > limit:
            Print.info(f'... truncated {len(rows) - limit} additional vertex records for {source}')


@task
def cloudlab_wan(ctx, action='setup', settings_file='cloudlab_settings.json'):
    ''' Emulate WAN RTT between sites (tc netem). action=setup|clear. Optional settings_file=... '''
    try:
        w = CloudLabWan(settings_file=settings_file)
        act = (action or 'setup').lower()
        if act == 'setup':
            w.setup()
        elif act == 'clear':
            w.clear()
        else:
            Print.error('cloudlab_wan: use action=setup or action=clear')
    except BenchError as e:
        Print.error(e)

@task
def local(ctx, debug=True, duration=None):
    ''' Run benchmarks on localhost '''
    bench_params = _local_bench_params()
    node_params = _local_node_params()
    if duration is not None:
        bench_params['duration'] = int(duration)
    try:
        ret = LocalBench(bench_params, node_params).run(debug)
        print(ret.result())
    except BenchError as e:
        Print.error(e)


@task
def local_vertex(ctx, debug=True, duration=None, limit=10):
    ''' Run local benchmark and print per-vertex sizes from primary logs '''
    bench_params = _local_bench_params()
    node_params = _local_node_params()

    if duration is not None:
        bench_params['duration'] = int(duration)
    limit = int(limit)

    Print.heading('Running local vertex inspection')
    Print.info(
        'Workload config: '
        f"rate_type={bench_params['rate_type']}, "
        f"rate={bench_params['rate']}, "
        f"tx_size={bench_params['tx_size']}, "
        f"duration={bench_params['duration']}, "
        f"header_size={node_params['header_size']}, "
        f"max_header_batches={node_params.get('max_header_batches', 'off')}"
    )

    try:
        ret = LocalBench(bench_params, node_params).run(debug)
        print(ret.result())
        _print_vertex_stats(limit=limit)
    except BenchError as e:
        Print.error(e)


@task
def create(ctx, nodes=2):
    ''' Create a testbed'''
    if InstanceManager is None:
        Print.error('InstanceManager is not available (boto3 may not be installed)')
        return
    try:
        InstanceManager.make().create_instances(nodes)
    except BenchError as e:
        Print.error(e)


@task
def destroy(ctx):
    ''' Destroy the testbed '''
    if InstanceManager is None:
        Print.error('InstanceManager is not available (boto3 may not be installed)')
        return
    try:
        InstanceManager.make().terminate_instances()
    except BenchError as e:
        Print.error(e)


@task
def start(ctx, max=2):
    ''' Start at most `max` machines per data center '''
    if InstanceManager is None:
        Print.error('InstanceManager is not available (boto3 may not be installed)')
        return
    try:
        InstanceManager.make().start_instances(max)
    except BenchError as e:
        Print.error(e)


@task
def stop(ctx):
    ''' Stop all machines '''
    if InstanceManager is None:
        Print.error('InstanceManager is not available (boto3 may not be installed)')
        return
    try:
        InstanceManager.make().stop_instances()
    except BenchError as e:
        Print.error(e)


@task
def info(ctx):
    ''' Display connect information about all the available machines '''
    if InstanceManager is None:
        Print.error('InstanceManager is not available (boto3 may not be installed)')
        return
    try:
        InstanceManager.make().print_info()
    except BenchError as e:
        Print.error(e)


@task
def install(ctx):
    ''' Install the codebase on all machines '''
    try:
        _get_aws_bench()(ctx).install()
    except BenchError as e:
        Print.error(e)


@task
def remote(ctx, debug=False):
    ''' Run benchmarks on AWS '''
    bench_params = {
        'faults': 0,
        'nodes': 10,
        'workers': 1,
        'collocate': True,
        'rate_type': 'balanced',
        'rate': 100_000,
        'tx_size': 512,
        'duration': 20,
        'runs': 1,
    }
    node_params = {
        'header_size': 1_000,  # bytes, used when max_header_batches is not set
        'max_header_batches': _fair_header_batches(),  # fair comparison mode
        'max_header_delay': 200,  # ms
        'gc_depth': 50,  # rounds
        'sync_retry_delay': 10_000,  # ms
        'sync_retry_nodes': 3,  # number of nodes
        'batch_size': 500_000,  # bytes
        'max_batch_delay': 200  # ms
    }
    try:
        _get_aws_bench()(ctx).run(bench_params, node_params, debug)
    except BenchError as e:
        Print.error(e)


@task
def plot(ctx):
    ''' Plot performance using the logs generated by "fab remote" '''
    if Ploter is None:
        Print.error('matplotlib is not installed. Please install it: pip install matplotlib')
        return
    plot_params = {
        'faults': [0],
        'nodes': [10, 20, 50],
        'workers': [1],
        'collocate': True,
        'tx_size': 512,
        'max_latency': [3_500, 4_500]
    }
    try:
        Ploter.plot(plot_params)
    except PlotError as e:
        Print.error(BenchError('Failed to plot performance', e))


@task
def plot_round_end(ctx, input=None, output=None, nodes=None, average=True):
    ''' Plot node round-end curves from a pivot CSV '''
    command = ['python3', 'plot_round_end_from_csv.py']
    if input:
        command.extend(['--input', str(input)])
    if output:
        command.extend(['--output', str(output)])
    if nodes:
        command.extend(['--nodes', str(nodes)])
    if not average:
        command.append('--no-average')

    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as e:
        Print.error(BenchError('Failed to plot round-end CSV', e))


@task
def kill(ctx):
    ''' Stop execution on all machines (AWS) '''
    try:
        _get_aws_bench()(ctx).kill()
    except BenchError as e:
        Print.error(e)


@task
def logs(ctx):
    ''' Print a summary of the logs '''
    try:
        print(LogParser.process('./logs', faults='?').result())
    except ParseError as e:
        Print.error(BenchError('Failed to parse logs', e))


# CloudLab tasks
@task
def cloudlab_info(ctx):
    ''' Display connect information about all CloudLab nodes '''
    try:
        _get_cloudlab_instance_manager().make().print_info()
    except BenchError as e:
        Print.error(e)


@task
def cloudlab_test(ctx):
    ''' Test SSH connections to all CloudLab nodes '''
    try:
        _get_cloudlab_bench()(ctx).test_connections()
    except BenchError as e:
        Print.error(e)


@task
def cloudlab_install(ctx):
    ''' Install the codebase on all CloudLab nodes '''
    try:
        _get_cloudlab_bench()(ctx).install()
    except BenchError as e:
        Print.error(e)

# coupled experiment
@task
def cloudlab_remote(
    ctx,
    debug=True,
    network_tag=None,
    workload_tag=None,
    rate_type=None,
    percentages=None,
):
    ''' Run benchmarks on CloudLab '''
    bench_params = _cloudlab_bench_params()
    node_params = _cloudlab_node_params()
    try:
        bench_params = _apply_cloudlab_bench_overrides(
            bench_params,
            network_tag=network_tag,
            workload_tag=workload_tag,
            rate_type=rate_type,
            percentages=percentages,
        )
        debug = _parse_bool_arg(debug)
        _get_cloudlab_bench()(ctx).run(bench_params, node_params, debug)
    except ValueError as e:
        Print.error(BenchError('Invalid cloudlab_remote arguments', e))
    except BenchError as e:
        Print.error(e)


@task
def cloudlab_status(ctx):
    ''' Check if benchmark processes are running on CloudLab nodes '''
    try:
        _get_cloudlab_bench()(ctx).status()
    except BenchError as e:
        Print.error(e)

@task
def cloudlab_debug(ctx):
    ''' Debug: Check tmux sessions and capture error messages from CloudLab nodes '''
    try:
        _get_cloudlab_bench()(ctx).debug_sessions()
    except BenchError as e:
        Print.error(e)


@task
def cloudlab_kill(ctx):
    ''' Stop execution on all CloudLab nodes '''
    try:
        _get_cloudlab_bench()(ctx).kill()
    except BenchError as e:
        Print.error(e)


@task
def cloudlab_download_primary_logs(ctx, nodes='0,1,2,3'):
    ''' Download primary logs from specified CloudLab nodes (default: 0,1,2,3) '''
    import sys
    import os
    # Add benchmark directory to path
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    
    from download_logs import download_primary_logs
    
    try:
        # Parse node indices
        node_indices = [int(x.strip()) for x in nodes.split(',')]
        Print.info(f'Downloading primary logs from nodes: {node_indices}')
        success = download_primary_logs('cloudlab_settings.json', node_indices)
        if success:
            Print.info('✓ Successfully downloaded primary logs')
        else:
            Print.error('✗ Failed to download some primary logs')
        return success
    except ValueError as e:
        Print.error(f'Invalid node indices format: {nodes}. Use comma-separated numbers like "0,1,2,3"')
        return False
    except Exception as e:
        Print.error(f'Failed to download primary logs: {e}')
        return False
