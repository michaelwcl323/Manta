from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from fabric import Connection, ThreadingGroup as Group
from fabric.exceptions import GroupException
from paramiko import RSAKey
from paramiko.ssh_exception import PasswordRequiredException, SSHException
from os.path import basename, splitext
from time import sleep
from math import ceil
from copy import deepcopy
from itertools import zip_longest
from inspect import signature
import subprocess

from benchmark.config import Committee, Key, NodeParameters, BenchParameters, ConfigError
from benchmark.utils import BenchError, Print, PathMaker, progress_bar, write_failure_summary
from benchmark.commands import CommandMaker
from benchmark.logs import LogParser, ParseError
from benchmark.instance import InstanceManager
from benchmark.imbalanced_rate import ZipfAllocator


class FabricError(Exception):
    ''' Wrapper for Fabric exception with a meaningfull error message. '''

    def __init__(self, error):
        assert isinstance(error, GroupException)
        message = list(error.result.values())[-1]
        super().__init__(message)


class ExecutionError(Exception):
    pass


class Bench:
    SSH_WORKERS = 10
    SSH_RETRIES = 3

    def __init__(self, ctx):
        self.manager = InstanceManager.make()
        self.settings = self.manager.settings
        try:
            ctx.connect_kwargs.pkey = RSAKey.from_private_key_file(
                self.manager.settings.key_path,
                password=self.manager.settings.key_passphrase,
            )
            self.connect = ctx.connect_kwargs
        except (IOError, PasswordRequiredException, SSHException) as e:
            raise BenchError('Failed to load SSH key', e)

    def _check_stderr(self, output):
        if isinstance(output, dict):
            for x in output.values():
                if x.stderr:
                    raise ExecutionError(x.stderr)
        else:
            if output.stderr:
                raise ExecutionError(output.stderr)

    def _run_on_hosts(self, hosts, command):
        """Run a command with bounded SSH concurrency and connection retries."""
        if not hosts:
            return

        def run_one(host):
            last_error = None
            for attempt in range(1, self.SSH_RETRIES + 1):
                connection = Connection(
                    host,
                    user='ubuntu',
                    connect_kwargs=self.connect,
                    connect_timeout=60,
                )
                try:
                    connection.open()
                    result = connection.run(command, hide=True, warn=True)
                    if not result.ok:
                        details = (result.stderr or result.stdout or '').strip()
                        raise ExecutionError(
                            f'{host} command failed with exit code '
                            f'{result.exited}: {details}'
                        )
                    return
                except ExecutionError:
                    raise
                except Exception as error:
                    last_error = error
                    if attempt < self.SSH_RETRIES:
                        sleep(2 ** (attempt - 1))
                finally:
                    connection.close()
            raise ExecutionError(
                f'{host} unavailable after {self.SSH_RETRIES} attempts: '
                f'{last_error}'
            )

        failures = []
        with ThreadPoolExecutor(
            max_workers=min(self.SSH_WORKERS, len(hosts))
        ) as executor:
            futures = {executor.submit(run_one, host): host for host in hosts}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as error:
                    failures.append(f'{futures[future]}: {error}')
        if failures:
            raise ExecutionError(
                'Remote command failed on the following hosts:\n  '
                + '\n  '.join(sorted(failures))
            )

    def install(self):
        Print.info('Installing rust and cloning the repo...')
        cmd = [
            # Ubuntu cloud images: if systemd-resolved is stopped, 127.0.0.53 refuses DNS
            # and apt/curl/git fail to resolve hosts — enable it before network use.
            'if systemctl cat systemd-resolved.service >/dev/null 2>&1; then '
            'systemctl is-active --quiet systemd-resolved || '
            'sudo systemctl enable --now systemd-resolved; fi',
            'sudo apt-get update',
            # EC2 images sometimes leave linux-aws support packages half-configured; a full
            # upgrade then fails until dependencies are reconciled (see apt message:
            # "Try 'apt --fix-broken install'").
            'sudo DEBIAN_FRONTEND=noninteractive apt-get -y -f install',
            'sudo DEBIAN_FRONTEND=noninteractive apt-get -y upgrade',
            'sudo DEBIAN_FRONTEND=noninteractive apt-get -y autoremove',

            # The following dependencies prevent the error: [error: linker `cc` not found].
            'sudo apt-get -y install build-essential',
            'sudo apt-get -y install cmake',

            # Install rust (non-interactive).
            'curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y',
            'source $HOME/.cargo/env',
            'rustup default stable',

            # This is missing from the Rocksdb installer (needed for Rocksdb).
            'sudo apt-get install -y clang',

            # Clone into the same directory name used by _update / compile (must match repo.name).
            f'(git clone {self.settings.repo_url} {self.settings.repo_name} || (cd {self.settings.repo_name} && git pull))'
        ]
        hosts = self.manager.hosts(flat=True)
        try:
            self._run_on_hosts(hosts, ' && '.join(cmd))
            Print.heading(f'Initialized testbed of {len(hosts)} nodes')
        except (GroupException, ExecutionError) as e:
            e = FabricError(e) if isinstance(e, GroupException) else e
            raise BenchError('Failed to install repo on testbed', e)

    def kill(self, hosts=[], delete_logs=False):
        assert isinstance(hosts, list)
        assert isinstance(delete_logs, bool)
        hosts = hosts if hosts else self.manager.hosts(flat=True)
        delete_logs = CommandMaker.clean_logs() if delete_logs else 'true'
        cmd = [delete_logs, f'({CommandMaker.kill()} || true)']
        try:
            self._run_on_hosts(hosts, ' && '.join(cmd))
        except (GroupException, ExecutionError) as e:
            e = FabricError(e) if isinstance(e, GroupException) else e
            raise BenchError('Failed to kill nodes', e)

    def _select_hosts(self, bench_parameters):
        # Collocate the primary and its workers on the same machine.
        if bench_parameters.collocate:
            nodes = max(bench_parameters.nodes)

            # Ensure there are enough hosts.
            hosts = self.manager.hosts()
            if sum(len(x) for x in hosts.values()) < nodes:
                return []

            # Select the hosts in different data centers.
            ordered = zip_longest(*hosts.values())
            ordered = [x for group in ordered for x in group if x is not None]
            return ordered[:nodes]

        # Spawn the primary and each worker on a different machine. Each
        # authority runs in a single data center.
        else:
            primaries = max(bench_parameters.nodes)

            # Ensure there are enough hosts.
            hosts = self.manager.hosts()
            if len(hosts.keys()) < primaries:
                return []
            for ips in hosts.values():
                if len(ips) < bench_parameters.workers + 1:
                    return []

            # Ensure the primary and its workers are in the same region.
            selected = []
            for region in list(hosts.keys())[:primaries]:
                ips = list(hosts[region])[:bench_parameters.workers + 1]
                selected.append(ips)
            return selected

    def _background_run(self, host, command, log_file):
        name = splitext(basename(log_file))[0]
        cmd = f'tmux new -d -s "{name}" "{command} |& tee {log_file}"'
        c = Connection(host, user='ubuntu', connect_kwargs=self.connect)
        try:
            output = c.run(cmd, hide=True)
            self._check_stderr(output)
        finally:
            c.close()

    def _background_run_many(self, jobs):
        assert isinstance(jobs, list)
        if not jobs:
            return

        # Launch all commands for the same role concurrently to reduce skew
        # between nodes while still keeping role ordering explicit.
        worker_count = min(self.SSH_WORKERS, len(jobs))
        Print.info(
            f'Starting {len(jobs)} processes with up to '
            f'{worker_count} concurrent SSH sessions...'
        )
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(self._background_run, host, command, log_file)
                for host, command, log_file in jobs
            ]
            for future in futures:
                future.result()

    def _update(self, hosts, collocate):
        if collocate:
            ips = list(set(hosts))
        else:
            ips = list(set([x for y in hosts for x in y]))

        Print.info(
            f'Updating {len(ips)} machines (branch "{self.settings.branch}")...'
        )
        cmd = [
            f'(cd {self.settings.repo_name} && git fetch -f)',
            f'(cd {self.settings.repo_name} && git checkout -f {self.settings.branch})',
            f'(cd {self.settings.repo_name} && git pull -f)',
            f'(cd {self.settings.repo_name} && git ls-files --error-unmatch '
            f'{self.settings.repo_subdir}/node/Cargo.toml >/dev/null)',
            'source $HOME/.cargo/env',
            f'(cd {self.settings.repo_path}/node && {CommandMaker.compile()})',
            CommandMaker.alias_binaries(
                f'./{self.settings.repo_path}/target/release/'
            )
        ]
        self._run_on_hosts(ips, ' && '.join(cmd))

    def _config(self, hosts, node_parameters, bench_parameters):
        Print.info('Generating configuration files...')

        # Cleanup all local configuration files.
        cmd = CommandMaker.cleanup()
        subprocess.run([cmd], shell=True, stderr=subprocess.DEVNULL)

        # Recompile the latest code.
        cmd = CommandMaker.compile().split()
        subprocess.run(cmd, check=True, cwd=PathMaker.node_crate_path())

        # Create alias for the client and nodes binary.
        cmd = CommandMaker.alias_binaries(PathMaker.binary_path())
        subprocess.run([cmd], shell=True)

        # Generate configuration files.
        keys = []
        key_files = [PathMaker.key_file(i) for i in range(len(hosts))]
        for filename in key_files:
            cmd = CommandMaker.generate_key(filename).split()
            subprocess.run(cmd, check=True)
            keys += [Key.from_file(filename)]

        names = [x.name for x in keys]

        if bench_parameters.collocate:
            workers = bench_parameters.workers
            addresses = OrderedDict(
                (x, [y] * (workers + 1)) for x, y in zip(names, hosts)
            )
        else:
            addresses = OrderedDict(
                (x, y) for x, y in zip(names, hosts)
            )
        jp = node_parameters.json
        optional_committee_values = {
            'sigma': jp.get('sigma', 1),
            'kappa': jp.get('kappa', 2),
            'reference': jp.get('reference', 4),
            'coverage': jp.get('coverage', 7),
            'candidate_count': jp.get(
                'candidate_count', jp.get('reference', 4)
            ),
        }
        supported_parameters = signature(Committee).parameters
        committee = Committee(
            addresses,
            self.settings.base_port,
            **{
                name: value
                for name, value in optional_committee_values.items()
                if name in supported_parameters
            },
        )
        committee.print(PathMaker.committee_file())

        node_parameters.print(PathMaker.parameters_file())

        # Cleanup all nodes and upload configuration files.
        names = names[:len(names)-bench_parameters.faults]
        def upload_one(item):
            i, name = item
            for ip in committee.ips(name):
                c = Connection(ip, user='ubuntu', connect_kwargs=self.connect)
                try:
                    c.run(f'{CommandMaker.cleanup()} || true', hide=True)
                    c.put(PathMaker.committee_file(), '.')
                    c.put(PathMaker.key_file(i), '.')
                    c.put(PathMaker.parameters_file(), '.')
                finally:
                    c.close()

        Print.info(
            f'Uploading configuration to {len(names)} authorities with up to '
            f'{min(self.SSH_WORKERS, len(names))} concurrent SSH sessions...'
        )
        with ThreadPoolExecutor(
            max_workers=min(self.SSH_WORKERS, len(names))
        ) as executor:
            futures = [executor.submit(upload_one, item) for item in enumerate(names)]
            for future in futures:
                future.result()

        return committee

    def _run_single(self, rate, committee, bench_parameters, node_parameters,
                    debug=False):
        faults = bench_parameters.faults

        hosts = committee.ips()
        self.kill(hosts=hosts, delete_logs=True)
        sleep(3)

        workers_addresses = committee.workers_addresses(faults)

        # Start the service roles before introducing client load.
        Print.info('Booting primaries...')
        primary_jobs = []
        for i, address in enumerate(committee.primary_addresses(faults)):
            host = Committee.ip(address)
            cmd = CommandMaker.run_primary(
                PathMaker.key_file(i),
                PathMaker.committee_file(),
                PathMaker.db_path(i),
                PathMaker.parameters_file(),
                debug=debug
            )
            log_file = PathMaker.primary_log_file(i)
            primary_jobs.append((host, cmd, log_file))
        self._background_run_many(primary_jobs)

        Print.info('Booting workers...')
        worker_jobs = []
        for i, addresses in enumerate(workers_addresses):
            for (id, address) in addresses:
                host = Committee.ip(address)
                cmd = CommandMaker.run_worker(
                    PathMaker.key_file(i),
                    PathMaker.committee_file(),
                    PathMaker.db_path(i, id),
                    PathMaker.parameters_file(),
                    id,  # The worker's id.
                    debug=debug
                )
                log_file = PathMaker.worker_log_file(i, id)
                worker_jobs.append((host, cmd, log_file))
        self._background_run_many(worker_jobs)

        Print.info('Booting clients...')
        workers_total = committee.workers()
        if bench_parameters.rate_type == 'balanced':
            rate_share = ceil(rate / workers_total)
            worker_rates = [rate_share] * workers_total
        elif bench_parameters.rate_type in ('imbalanced', 'imbalance'):
            s = node_parameters.json.get('s')
            if s is None:
                raise BenchError(
                    'rate_type=imbalanced requires node parameter "s"',
                    ValueError('Missing Zipf parameter s'),
                )
            try:
                worker_rates = ZipfAllocator(
                    rate, workers_total, float(s)
                ).allocate()
            except Exception as error:
                raise BenchError(
                    'Failed to allocate imbalanced client rates', error
                ) from error
            Print.info(f'Client rates (Zipf, s={s}): {worker_rates}')
        else:
            raise BenchError(
                f'Unknown rate_type "{bench_parameters.rate_type}"',
                ValueError('Expected balanced or imbalanced'),
            )

        client_jobs = []
        worker_index = 0
        all_workers = [x for y in workers_addresses for _, x in y]
        for i, addresses in enumerate(workers_addresses):
            for (id, address) in addresses:
                host = Committee.ip(address)
                client_rate = worker_rates[
                    min(worker_index, len(worker_rates) - 1)
                ]
                cmd = CommandMaker.run_client(
                    address,
                    bench_parameters.tx_size,
                    client_rate,
                    all_workers,
                )
                log_file = PathMaker.client_log_file(i, id)
                client_jobs.append((host, cmd, log_file))
                worker_index += 1
        self._background_run_many(client_jobs)

        duration = bench_parameters.duration
        for _ in progress_bar(range(20), prefix=f'Running benchmark ({duration} sec):'):
            sleep(duration / 20.0)
        self.kill(hosts=hosts, delete_logs=False)

    def _logs(self, committee, faults, total_rate=None, tx_size=None,
              design_tag=None, network_tag=None):
        # Delete local logs (if any).
        cmd = CommandMaker.clean_logs()
        subprocess.run([cmd], shell=True, stderr=subprocess.DEVNULL)

        # Download log files.
        workers_addresses = committee.workers_addresses(faults)

        def download_worker_logs(item):
            i, addresses = item
            for id, address in addresses:
                host = Committee.ip(address)
                c = Connection(host, user='ubuntu', connect_kwargs=self.connect)
                try:
                    c.get(
                        PathMaker.client_log_file(i, id),
                        local=PathMaker.client_log_file(i, id)
                    )
                    c.get(
                        PathMaker.worker_log_file(i, id),
                        local=PathMaker.worker_log_file(i, id)
                    )
                finally:
                    c.close()

        Print.info('Downloading worker and client logs in parallel...')
        with ThreadPoolExecutor(
            max_workers=min(self.SSH_WORKERS, len(workers_addresses))
        ) as executor:
            futures = [
                executor.submit(download_worker_logs, item)
                for item in enumerate(workers_addresses)
            ]
            for future in futures:
                future.result()

        primary_addresses = committee.primary_addresses(faults)

        def download_primary_log(item):
            i, address = item
            host = Committee.ip(address)
            c = Connection(host, user='ubuntu', connect_kwargs=self.connect)
            try:
                c.get(
                    PathMaker.primary_log_file(i),
                    local=PathMaker.primary_log_file(i)
                )
            finally:
                c.close()

        Print.info('Downloading primary logs in parallel...')
        with ThreadPoolExecutor(
            max_workers=min(self.SSH_WORKERS, len(primary_addresses))
        ) as executor:
            futures = [
                executor.submit(download_primary_log, item)
                for item in enumerate(primary_addresses)
            ]
            for future in futures:
                future.result()

        # Parse logs and return the parser. If clients failed to start, we can
        # still recover a summary from the configured size/rate values.
        Print.info('Parsing logs and computing performance...')
        default_client_rates = None
        if total_rate is not None:
            worker_count = committee.workers()
            if worker_count > 0:
                rate_share = ceil(total_rate / worker_count)
                default_client_rates = [rate_share] * worker_count
        log_arguments = {
            'faults': faults,
            'default_client_size': tx_size,
            'default_client_rates': default_client_rates,
            'design_tag': design_tag,
            'network_tag': network_tag,
        }
        supported = signature(LogParser.process).parameters
        return LogParser.process(
            PathMaker.logs_path(),
            **{k: v for k, v in log_arguments.items() if k in supported},
        )

    def run(self, bench_parameters_dict, node_parameters_dict, debug=False):
        assert isinstance(debug, bool)
        Print.heading('Starting remote benchmark')
        try:
            bench_parameters = BenchParameters(bench_parameters_dict)
            node_parameters = NodeParameters(node_parameters_dict)
        except ConfigError as e:
            raise BenchError('Invalid nodes or bench parameters', e)

        # Select which hosts to use.
        selected_hosts = self._select_hosts(bench_parameters)
        if not selected_hosts:
            Print.warn('There are not enough instances available')
            return

        # Update nodes.
        try:
            self._update(selected_hosts, bench_parameters.collocate)
        except (GroupException, ExecutionError) as e:
            e = FabricError(e) if isinstance(e, GroupException) else e
            raise BenchError('Failed to update nodes', e)

        # Upload all configuration files.
        try:
            committee = self._config(
                selected_hosts, node_parameters, bench_parameters
            )
        except (subprocess.SubprocessError, GroupException) as e:
            e = FabricError(e) if isinstance(e, GroupException) else e
            raise BenchError('Failed to configure nodes', e)

        # Run benchmarks.
        design_tag = getattr(bench_parameters, 'design_tag', None)
        network_tag = getattr(bench_parameters, 'network_tag', None)
        for n in bench_parameters.nodes:
            committee_copy = deepcopy(committee)
            committee_copy.remove_nodes(committee.size() - n)

            for r in bench_parameters.rate:
                Print.heading(f'\nRunning {n} nodes (input rate: {r:,} tx/s)')

                # Run the benchmark.
                for i in range(bench_parameters.runs):
                    Print.heading(f'Run {i+1}/{bench_parameters.runs}')
                    if hasattr(PathMaker, 'summary_file'):
                        summary_arguments = {
                            'design_tag': design_tag,
                            'network_tag': network_tag,
                        }
                        summary_supported = signature(
                            PathMaker.summary_file
                        ).parameters
                        summary_file = PathMaker.summary_file(
                            bench_parameters.faults,
                            n,
                            bench_parameters.workers,
                            bench_parameters.collocate,
                            r,
                            bench_parameters.tx_size,
                            i + 1,
                            **{
                                k: v for k, v in summary_arguments.items()
                                if k in summary_supported
                            },
                        )
                    else:
                        summary_file = PathMaker.result_file(
                            bench_parameters.faults,
                            n,
                            bench_parameters.workers,
                            bench_parameters.collocate,
                            r,
                            bench_parameters.tx_size,
                        )
                    try:
                        self._run_single(
                            r,
                            committee_copy,
                            bench_parameters,
                            node_parameters,
                            debug,
                        )

                        faults = bench_parameters.faults
                        logger = self._logs(
                            committee_copy,
                            faults,
                            total_rate=r,
                            tx_size=bench_parameters.tx_size,
                            design_tag=design_tag,
                            network_tag=network_tag,
                        )
                        logger.print(summary_file)
                    except (subprocess.SubprocessError, GroupException, ParseError) as e:
                        self.kill(hosts=selected_hosts)
                        if isinstance(e, GroupException):
                            e = FabricError(e)
                        write_failure_summary(
                            summary_file,
                            design_tag=design_tag,
                            network_tag=network_tag,
                            faults=bench_parameters.faults,
                            nodes=n,
                            workers=bench_parameters.workers,
                            collocate=bench_parameters.collocate,
                            rate=r,
                            tx_size=bench_parameters.tx_size,
                            run=i + 1,
                            error=str(e),
                        )
                        Print.error(BenchError('Benchmark failed', e))
                        continue
