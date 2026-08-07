# Copyright(C) Facebook, Inc. and its affiliates.
import subprocess
from math import ceil
from os.path import basename, splitext
from time import sleep

from benchmark.imbalanced_rate import ZipfAllocator, ExtremeAllocator, ParetoAllocator, TwoHeavyAllocator, ExtremeXAllocator, CustomAllocator
from benchmark.commands import CommandMaker
from benchmark.config import Key, LocalCommittee, NodeParameters, BenchParameters, ConfigError
from benchmark.logs import LogParser, ParseError
from benchmark.utils import Print, BenchError, PathMaker


class LocalBench:
    BASE_PORT = 3000

    def __init__(self, bench_parameters_dict, node_parameters_dict):
        try:
            self.bench_parameters = BenchParameters(bench_parameters_dict)
            self.node_parameters = NodeParameters(node_parameters_dict)
            if bench_parameters_dict['rate_type'] == 'imbalanced':
                self.s = node_parameters_dict['s']
            self.solid_step_length = node_parameters_dict.get('solid_step_length', 2)
            self.solid_step_number = node_parameters_dict.get('solid_step_number', 1)
            self.solid_reference = node_parameters_dict.get('reference', 3)
        except ConfigError as e:
            raise BenchError('Invalid nodes or bench parameters', e)

    def __getattr__(self, attr):
        return getattr(self.bench_parameters, attr)

    def _background_run(self, command, log_file):
        name = splitext(basename(log_file))[0]
        cmd = f'{command} 2> {log_file}'
        subprocess.run(['tmux', 'new', '-d', '-s', name, cmd], check=True)

    def _kill_nodes(self):
        try:
            cmd = CommandMaker.kill().split()
            subprocess.run(cmd, stderr=subprocess.DEVNULL)
        except subprocess.SubprocessError as e:
            raise BenchError('Failed to kill testbed', e)

    def run(self, debug=False):
        assert isinstance(debug, bool)
        Print.heading('Starting local benchmark')

        # Kill any previous testbed.
        self._kill_nodes()

        try:
            Print.info('Setting up testbed...')
            nodes, rate, rate_type = self.nodes[0], self.rate[0], self.rate_type

            # Cleanup all files.
            cmd = f'{CommandMaker.clean_logs()} ; {CommandMaker.cleanup()}'
            subprocess.run([cmd], shell=True, stderr=subprocess.DEVNULL)
            sleep(0.5)  # Removing the store may take time.

            # Recompile the latest code.
            cmd = CommandMaker.compile().split()
            subprocess.run(cmd, check=True, cwd=PathMaker.node_crate_path())

            # Create alias for the client and nodes binary.
            cmd = CommandMaker.alias_binaries(PathMaker.binary_path())
            subprocess.run([cmd], shell=True)

            # Generate configuration files.
            keys = []
            key_files = [PathMaker.key_file(i) for i in range(nodes)]
            for filename in key_files:
                cmd = CommandMaker.generate_key(filename).split()
                subprocess.run(cmd, check=True)
                keys += [Key.from_file(filename)]

            names = [x.name for x in keys]
            committee = LocalCommittee(names, self.BASE_PORT, self.workers, self.solid_step_length, self.solid_step_number, self.solid_reference)
            committee.print(PathMaker.committee_file())

            self.node_parameters.print(PathMaker.parameters_file())

            # Run the clients (they will wait for the nodes to be ready).
            workers_addresses = committee.workers_addresses(self.faults)
            num_nodes = len(workers_addresses)

            if rate_type == 'balanced':
                rate_share = ceil(rate / committee.workers())
                for i, addresses in enumerate(workers_addresses):
                    for (id, address) in addresses:
                        cmd = CommandMaker.run_client(
                            address,
                            self.tx_size,
                            rate_share,
                            [x for y in workers_addresses for _, x in y]
                        )
                        log_file = PathMaker.client_log_file(i, id)
                        self._background_run(cmd, log_file)
            elif rate_type == 'extreme':
                # Extreme workload: first node gets ~99%, remaining nodes split the rest
                try:
                    node_rates = ExtremeAllocator(rate, num_nodes).allocate()
                except Exception as e:
                    raise BenchError('Failed to allocate extreme node rates', e)
                print(f'Node rates (Extreme: first node ~99%, others share the rest): {node_rates}')
                # Each worker in a node uses the same node rate
                for i, addresses in enumerate(workers_addresses):
                    node_rate = node_rates[i]
                    for (id, address) in addresses:
                        cmd = CommandMaker.run_client(
                            address,
                            self.tx_size,
                            node_rate,
                            [x for y in workers_addresses for _, x in y]
                        )
                        log_file = PathMaker.client_log_file(i, id)
                        self._background_run(cmd, log_file)
            elif rate_type == 'extreme_x':
                # Extreme(x): first x nodes each get 20%, others share the rest
                x = getattr(self.bench_parameters, 'extreme_x', None)
                if x is None:
                    raise BenchError('rate_type=extreme_x requires bench parameter "extreme_x"', ConfigError('missing extreme_x'))
                try:
                    node_rates = ExtremeXAllocator(rate, num_nodes, x).allocate()
                except Exception as e:
                    raise BenchError('Failed to allocate extreme_x node rates', e)
                print(f'Node rates (ExtremeX x={x}): {node_rates}')
                for i, addresses in enumerate(workers_addresses):
                    node_rate = node_rates[i]
                    for (id, address) in addresses:
                        cmd = CommandMaker.run_client(
                            address,
                            self.tx_size,
                            node_rate,
                            [x for y in workers_addresses for _, x in y]
                        )
                        log_file = PathMaker.client_log_file(i, id)
                        self._background_run(cmd, log_file)
            elif rate_type == 'pareto':
                # Pareto-style workload: top3 share 75%, others share 25%
                try:
                    node_rates = ParetoAllocator(rate, num_nodes).allocate()
                except Exception as e:
                    raise BenchError('Failed to allocate pareto node rates', e)
                print(f'Node rates (Pareto top3=75%, others=25%): {node_rates}')
                for i, addresses in enumerate(workers_addresses):
                    node_rate = node_rates[i]
                    for (id, address) in addresses:
                        cmd = CommandMaker.run_client(
                            address,
                            self.tx_size,
                            node_rate,
                            [x for y in workers_addresses for _, x in y]
                        )
                        log_file = PathMaker.client_log_file(i, id)
                        self._background_run(cmd, log_file)
            elif rate_type == 'twoheavy':
                # Two-heavy workload: first two nodes share 70%, others share 30%
                try:
                    node_rates = TwoHeavyAllocator(rate, num_nodes).allocate()
                except Exception as e:
                    raise BenchError('Failed to allocate twoheavy node rates', e)
                print(f'Node rates (TwoHeavy first2=70%, others=30%): {node_rates}')
                for i, addresses in enumerate(workers_addresses):
                    node_rate = node_rates[i]
                    for (id, address) in addresses:
                        cmd = CommandMaker.run_client(
                            address,
                            self.tx_size,
                            node_rate,
                            [x for y in workers_addresses for _, x in y]
                        )
                        log_file = PathMaker.client_log_file(i, id)
                        self._background_run(cmd, log_file)
            elif rate_type == 'custom':
                # Custom workload: allocate based on specified percentages
                percentages = getattr(self.bench_parameters, 'percentages', None)
                extra_rate = getattr(self.bench_parameters, 'extra_rate', None)
                if percentages is None:
                    raise BenchError('rate_type=custom requires bench parameter "percentages"', ConfigError('missing percentages'))
                try:
                    allocator = CustomAllocator(rate, extra_rate, num_nodes, percentages)
                    node_rates = allocator.allocate()
                except Exception as e:
                    raise BenchError('Failed to allocate custom node rates', e)
                print(
                    f'Node rates (Custom mode={allocator.mode}, base_total={allocator.base_total_tps}, '
                    f'extra_total={allocator.extra_tps}, percentages={percentages}): '
                    f'{node_rates}'
                )
                for i, addresses in enumerate(workers_addresses):
                    node_rate = node_rates[i]
                    for (id, address) in addresses:
                        cmd = CommandMaker.run_client(
                            address,
                            self.tx_size,
                            node_rate,
                            [x for y in workers_addresses for _, x in y]
                        )
                        log_file = PathMaker.client_log_file(i, id)
                        self._background_run(cmd, log_file)
            else:
                # generate a list of rate with zipf (imbalanced)
                zipf_allocator = ZipfAllocator(rate, committee.workers(), self.s)
                rates = zipf_allocator.allocate()
                print(rates)
                # run the clients with the generated rate
                for i, addresses in enumerate(workers_addresses):
                    for (id, address) in addresses:
                        cmd = CommandMaker.run_client(
                            address,
                            self.tx_size,
                            rates[i],
                            [x for y in workers_addresses for _, x in y]
                        )
                        log_file = PathMaker.client_log_file(i, id)
                        self._background_run(cmd, log_file)

            # Run the primaries (except the faulty ones).
            for i, address in enumerate(committee.primary_addresses(self.faults)):
                cmd = CommandMaker.run_primary(
                    PathMaker.key_file(i),
                    PathMaker.committee_file(),
                    PathMaker.db_path(i),
                    PathMaker.parameters_file(),
                    debug=debug
                )
                log_file = PathMaker.primary_log_file(i)
                self._background_run(cmd, log_file)

            # Run the workers (except the faulty ones).
            for i, addresses in enumerate(workers_addresses):
                for (id, address) in addresses:
                    cmd = CommandMaker.run_worker(
                        PathMaker.key_file(i),
                        PathMaker.committee_file(),
                        PathMaker.db_path(i, id),
                        PathMaker.parameters_file(),
                        id,  # The worker's id.
                        debug=debug
                    )
                    log_file = PathMaker.worker_log_file(i, id)
                    self._background_run(cmd, log_file)

            # Wait for all transactions to be processed.
            Print.info(f'Running benchmark ({self.duration} sec)...')
            sleep(self.duration)
            self._kill_nodes()

            # Parse logs and return the parser.
            Print.info('Parsing logs...')
            return LogParser.process(PathMaker.logs_path(), faults=self.faults)

        except (subprocess.SubprocessError, ParseError) as e:
            self._kill_nodes()
            raise BenchError('Failed to run benchmark', e)
