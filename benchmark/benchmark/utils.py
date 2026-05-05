from datetime import datetime
from os import makedirs
from os.path import join
from os.path import dirname


class BenchError(Exception):
    def __init__(self, message, error):
        assert isinstance(error, Exception)
        self.message = message
        self.cause = error
        super().__init__(message)


class PathMaker:
    @staticmethod
    def _tag_segment(value, default):
        value = str(value).strip() if value is not None else ''
        value = value.replace(' ', '_')
        return value or default

    @staticmethod
    def binary_path():
        return join('..', 'target', 'release')

    @staticmethod
    def node_crate_path():
        return join('..', 'node')

    @staticmethod
    def committee_file():
        return '.committee.json'

    @staticmethod
    def parameters_file():
        return '.parameters.json'

    @staticmethod
    def key_file(i):
        assert isinstance(i, int) and i >= 0
        return f'.node-{i}.json'

    @staticmethod
    def db_path(i, j=None):
        assert isinstance(i, int) and i >= 0
        assert (isinstance(j, int) and i >= 0) or j is None
        worker_id = f'-{j}' if j is not None else ''
        return f'.db-{i}{worker_id}'

    @staticmethod
    def logs_path():
        return 'logs'

    @staticmethod
    def primary_log_file(i):
        assert isinstance(i, int) and i >= 0
        return join(PathMaker.logs_path(), f'primary-{i}.log')

    @staticmethod
    def worker_log_file(i, j):
        assert isinstance(i, int) and i >= 0
        assert isinstance(j, int) and i >= 0
        return join(PathMaker.logs_path(), f'worker-{i}-{j}.log')

    @staticmethod
    def client_log_file(i, j):
        assert isinstance(i, int) and i >= 0
        assert isinstance(j, int) and i >= 0
        return join(PathMaker.logs_path(), f'client-{i}-{j}.log')

    @staticmethod
    def results_path():
        return 'results'

    @staticmethod
    def summary_path(design_tag=None, network_tag=None):
        return join(
            PathMaker._tag_segment(design_tag, 'untagged_design'),
            PathMaker._tag_segment(network_tag, 'untagged_network'),
        )

    @staticmethod
    def summary_file(
        faults,
        nodes,
        workers,
        collocate,
        rate,
        tx_size,
        run,
        design_tag=None,
        network_tag=None,
        timestamp=None,
    ):
        design_segment = PathMaker._tag_segment(design_tag, 'untagged_design')
        network_segment = PathMaker._tag_segment(network_tag, 'untagged_network')
        stamp = timestamp or datetime.now().strftime('%Y%m%d_%H%M%S')
        return join(
            PathMaker.summary_path(design_segment, network_segment),
            (
                f'summary_design-{design_segment}_network-{network_segment}'
                f'_f{faults}_n{nodes}_w{workers}_c{collocate}'
                f'_r{rate}_tx{tx_size}_run{run}_{stamp}.txt'
            ),
        )

    @staticmethod
    def plots_path():
        return 'plots'

    @staticmethod
    def agg_file(type, faults, nodes, workers, collocate, rate, tx_size, max_latency=None):
        if max_latency is None:
            name = f'{type}-bench-{faults}-{nodes}-{workers}-{collocate}-{rate}-{tx_size}.txt'
        else:
            name = f'{type}-{max_latency}-bench-{faults}-{nodes}-{workers}-{collocate}-{rate}-{tx_size}.txt'
        return join(PathMaker.plots_path(), name)

    @staticmethod
    def plot_file(name, ext):
        return join(PathMaker.plots_path(), f'{name}.{ext}')


class Color:
    HEADER = '\033[95m'
    OK_BLUE = '\033[94m'
    OK_GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


class Print:
    @staticmethod
    def heading(message):
        assert isinstance(message, str)
        print(f'{Color.OK_GREEN}{message}{Color.END}')

    @staticmethod
    def info(message):
        assert isinstance(message, str)
        print(message)

    @staticmethod
    def warn(message):
        assert isinstance(message, str)
        print(f'{Color.BOLD}{Color.WARNING}WARN{Color.END}: {message}')

    @staticmethod
    def error(e):
        assert isinstance(e, BenchError)
        print(f'\n{Color.BOLD}{Color.FAIL}ERROR{Color.END}: {e}\n')
        causes, current_cause = [], e.cause
        while isinstance(current_cause, BenchError):
            causes += [f'  {len(causes)}: {e.cause}\n']
            current_cause = current_cause.cause
        causes += [f'  {len(causes)}: {type(current_cause)}\n']
        causes += [f'  {len(causes)}: {current_cause}\n']
        print(f'Caused by: \n{"".join(causes)}\n')


def write_failure_summary(
    filename,
    *,
    design_tag,
    network_tag,
    faults,
    nodes,
    workers,
    collocate,
    rate,
    tx_size,
    run,
    error,
):
    assert isinstance(filename, str)
    parent = dirname(filename)
    if parent:
        makedirs(parent, exist_ok=True)

    content = (
        '\n'
        '-----------------------------------------\n'
        ' SUMMARY:\n'
        '-----------------------------------------\n'
        ' + CONFIG:\n'
        f' Design tag: {design_tag or "N/A"}\n'
        f' Network tag: {network_tag or "N/A"}\n'
        f' Faults: {faults} node(s)\n'
        f' Committee size: {nodes} node(s)\n'
        f' Worker(s) per node: {workers} worker(s)\n'
        f' Collocate primary and workers: {collocate}\n'
        f' Input rate: {rate:,} tx/s\n'
        f' Transaction size: {tx_size:,} B\n'
        f' Run: {run}\n'
        '\n'
        ' + RESULTS:\n'
        ' Status: FAILED\n'
        f' Error: {error}\n'
        '-----------------------------------------\n'
    )

    with open(filename, 'w') as f:
        f.write(content)


def progress_bar(iterable, prefix='', suffix='', decimals=1, length=30, fill='█', print_end='\r'):
    total = len(iterable)

    def printProgressBar(iteration):
        formatter = '{0:.'+str(decimals)+'f}'
        percent = formatter.format(100 * (iteration / float(total)))
        filledLength = int(length * iteration // total)
        bar = fill * filledLength + '-' * (length - filledLength)
        print(f'\r{prefix} |{bar}| {percent}% {suffix}', end=print_end)

    printProgressBar(0)
    for i, item in enumerate(iterable):
        yield item
        printProgressBar(i + 1)
    print()
