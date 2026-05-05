from fabric import task

from benchmark.local import LocalBench
from benchmark.logs import ParseError, LogParser
from benchmark.run_artifacts import create_run_dir, write_run_artifacts
from benchmark.utils import PathMaker, Print
from benchmark.cloudlab_instance import CloudLabInstanceManager
from benchmark.cloudlab_remote import CloudLabBench
from benchmark.cloudlab_wan import CloudLabWan
from benchmark.utils import BenchError

# Import AWS remote benchmark module only when needed (lazy import).
try:
    from benchmark.remote import Bench
except ImportError:
    Bench = None

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


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {'0', 'false', 'no', 'off'}


@task
def local(
    ctx,
    debug=False,
    enable_wait=True,
    nodes=10,
    rate=40000,
    duration=20,
    runs=1,
    network_tag='auto',
    rtt_tag='local',
):
    ''' Run benchmarks on localhost '''
    enable_wait = _as_bool(enable_wait)
    nodes = int(nodes)
    rate = int(rate)
    duration = int(duration)
    runs = int(runs)
    network_tag = str(network_tag).strip()
    if not network_tag or network_tag == 'auto':
        network_tag = 'local-wait' if enable_wait else 'local-nowait'

    bench_params = {
        'faults': 0,
        'nodes': nodes,
        'workers': 1,
        'rate_type': 'balanced',
        'rate': rate,
        'tx_size': 512,
        'duration': duration,
        'runs': runs,
    }
    node_params = {
        'header_size': 1000,  # bytes
        'max_header_delay': 50,  # ms
        'gc_depth': 50,  # rounds
        'sync_retry_delay': 1000,  # ms
        'sync_retry_nodes': 7,  # number of nodes
        'batch_size': 500_000,  # bytes
        'max_batch_delay': 50,  # ms
        'sigma': 1,
        'kappa': 3,
        'reference': 4,
        'coverage': 7,
        's': 0.99,
        'enable_wait': enable_wait,
    }
    try:
        for run_index in range(runs):
            if runs > 1:
                Print.heading(
                    f'\nRunning local benchmark: nodes={nodes}, rate={rate}, run={run_index+1}/{runs}'
                )

            bench = LocalBench(bench_params, node_params)
            ret = bench.run(debug)
            print(ret.result())
            ret.print(
                PathMaker.result_file(
                    bench.bench_parameters.faults,
                    nodes,
                    bench.bench_parameters.workers,
                    bench.bench_parameters.collocate,
                    rate,
                    bench.bench_parameters.tx_size,
                )
            )

            run_dir = create_run_dir(network_tag, nodes, rate, run_index, runs)
            write_run_artifacts(
                run_dir,
                ret,
                bench.bench_parameters,
                nodes,
                rate,
                run_index,
                network_tag,
                rtt_tag,
            )
            Print.info(f'Per-run artifacts saved to: {run_dir}')
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
    ''' Display connect information about all the available machines (AWS) '''
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
    if Bench is None:
        Print.error('AWS benchmark support is not available (remote dependencies may not be installed)')
        return
    try:
        Bench(ctx).install()
    except BenchError as e:
        Print.error(e)


@task
def remote(ctx, debug=False):
    ''' Run benchmarks on AWS '''
    if Bench is None:
        Print.error('AWS benchmark support is not available (remote dependencies may not be installed)')
        return
    bench_params = {
        'faults': 3,
        'nodes': [10],
        'workers': 1,
        'collocate': True,
        'rate': [10_000, 110_000],
        'tx_size': 512,
        'duration': 300,
        'runs': 2,
    }
    node_params = {
        'header_size': 1_000,  # bytes
        'max_header_delay': 200,  # ms
        'gc_depth': 50,  # rounds
        'sync_retry_delay': 10_000,  # ms
        'sync_retry_nodes': 3,  # number of nodes
        'batch_size': 500_000,  # bytes
        'max_batch_delay': 200,  # ms
        'solid_step_length': 2,
        'solid_step_number': 1,
        'reference': 3,
    }
    try:
        Bench(ctx).run(bench_params, node_params, debug)
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
def kill(ctx):
    ''' Stop execution on all machines (AWS) '''
    if Bench is None:
        Print.error('AWS benchmark support is not available (remote dependencies may not be installed)')
        return
    try:
        Bench(ctx).kill()
    except BenchError as e:
        Print.error(e)


@task
def logs(ctx):
    ''' Print a summary of the logs '''
    try:
        print(LogParser.process('./logs', faults='?').result())
    except ParseError as e:
        Print.error(BenchError('Failed to parse logs', e))


# CloudLab tasks (same as experiment1 / experiment2)
@task
def cloudlab_info(ctx):
    ''' Display connect information about all CloudLab nodes '''
    try:
        CloudLabInstanceManager.make().print_info()
    except BenchError as e:
        Print.error(e)


@task
def cloudlab_test(ctx):
    ''' Test SSH connections to all CloudLab nodes '''
    try:
        CloudLabBench(ctx).test_connections()
    except BenchError as e:
        Print.error(e)


@task
def cloudlab_install(ctx):
    ''' Install the codebase on all CloudLab nodes '''
    try:
        CloudLabBench(ctx).install()
    except BenchError as e:
        Print.error(e)


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
def cloudlab_remote(ctx, debug=False, sigma=1, kappa=3, enable_wait=True):
    ''' Run benchmarks on CloudLab '''
    bench_params = {
        'faults': 0,
        'nodes': [50],
        'workers': 1,
        'collocate': True,
        'rate_type': 'balanced',
        'rate': [20000],
        # 'rate': [80000, 100000, 120000, 140000],
        'tx_size': 512,
        'duration': 120,
        'runs': 1,
        'network_tag': 'chitu-ack',
        'rtt_tag': 'no-delay',
    }
    node_params = {
        'header_size': 1_000,  # bytes
        'max_header_delay': 200,  # ms
        'gc_depth': 5000,  # rounds
        'sync_retry_delay': 1000,  # ms
        'sync_retry_nodes': 7,  # number of nodes
        'batch_size': 500_000,  # bytes
        'max_batch_delay': 200,  # ms
        'sigma': 1,
        'kappa': 3,
        'reference': 33,
        'coverage': 33,
        's': 0.99,
        'enable_wait': _as_bool(enable_wait),
    }
    try:
        CloudLabBench(ctx).run(bench_params, node_params, debug=debug)
    except BenchError as e:
        Print.error(e)


@task
def cloudlab_status(ctx):
    ''' Check if benchmark processes are running on CloudLab nodes '''
    try:
        CloudLabBench(ctx).status()
    except BenchError as e:
        Print.error(e)


@task
def cloudlab_debug(ctx):
    ''' Debug: Check tmux sessions and capture error messages from CloudLab nodes '''
    try:
        CloudLabBench(ctx).debug_sessions()
    except BenchError as e:
        Print.error(e)


@task
def cloudlab_kill(ctx):
    ''' Stop execution on all CloudLab nodes '''
    try:
        CloudLabBench(ctx).kill()
    except BenchError as e:
        Print.error(e)


@task
def cloudlab_download_primary_logs(ctx, nodes='0,1,2,3'):
    ''' Download primary logs from specified CloudLab nodes (default: 0,1,2,3) '''
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    from download_logs import download_primary_logs

    try:
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
