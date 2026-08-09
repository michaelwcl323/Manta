# Copyright(C) Facebook, Inc. and its affiliates.
from datetime import datetime
from glob import glob
from multiprocessing import Pool
from os.path import join
from re import findall, search
from statistics import mean

from benchmark.utils import Print


class ParseError(Exception):
    pass


class LogParser:
    def __init__(self, clients, primaries, workers, faults=0,
                 default_client_size=None, default_client_rates=None):
        inputs = [clients, primaries, workers]
        assert all(isinstance(x, list) for x in inputs)
        assert all(isinstance(x, str) for y in inputs for x in y)
        assert all(x for x in inputs)
        self._end_to_end_latency_cache = None

        self.faults = faults
        if isinstance(faults, int):
            self.committee_size = len(primaries) + int(faults)
            self.workers =  len(workers) // len(primaries)
        else:
            self.committee_size = '?'
            self.workers = '?'

        # Parse the clients logs.
        try:
            with Pool() as p:
                results = p.map(self._parse_clients, clients)
        except (ValueError, IndexError, AttributeError) as e:
            if default_client_size is None or default_client_rates is None:
                raise ParseError(f'Failed to parse clients\' logs: {e}')

            rates = default_client_rates
            if not isinstance(rates, list):
                rates = [rates] * len(clients)
            if len(rates) != len(clients):
                raise ParseError('Failed to parse clients\' logs: mismatched fallback rates')

            Print.warn(
                'Client logs are missing the expected metadata; '
                'falling back to configured transaction size/rate values'
            )
            results = [
                (
                    default_client_size,
                    rates[i],
                    None,
                    0,
                    {},
                    {'recovered_truncated': 0, 'interpolated': 0},
                )
                for i in range(len(clients))
            ]
        self.size, self.rate, self.start, misses, self.sent_samples, repairs \
            = zip(*results)
        self.misses = sum(misses)
        recovered_truncated = sum(x['recovered_truncated'] for x in repairs)
        interpolated = sum(x['interpolated'] for x in repairs)
        if recovered_truncated:
            Print.warn(
                f'Recovered {recovered_truncated:,} client sample timestamp(s) '
                'from truncated log lines'
            )
        if interpolated:
            Print.warn(
                f'Interpolated {interpolated:,} missing client sample timestamp(s)'
            )

        # Parse the primaries logs.
        try:
            with Pool() as p:
                results = p.map(self._parse_primaries, primaries)
        except (ValueError, IndexError, AttributeError) as e:
            raise ParseError(f'Failed to parse nodes\' logs: {e}')
        proposals, commits, self.configs, primary_ips = zip(*results)
        self.proposals = self._merge_results([x.items() for x in proposals])
        self.commits = self._merge_results([x.items() for x in commits])
        self.committed_with_proposals = [
            digest for digest in self.commits if digest in self.proposals
        ]
        missing_commit_proposals = len(self.commits) - len(self.committed_with_proposals)
        if missing_commit_proposals:
            Print.warn(
                f'Skipped {missing_commit_proposals:,} committed batch(es) missing '
                'proposal timestamps when computing consensus metrics'
            )

        # Parse the workers logs.
        try:
            with Pool() as p:
                results = p.map(self._parse_workers, workers)
        except (ValueError, IndexError, AttributeError) as e:
            raise ParseError(f'Failed to parse workers\' logs: {e}')
        sizes, self.received_samples, workers_ips = zip(*results)
        self.sizes = {
            k: v for x in sizes for k, v in x.items() if k in self.commits
        }

        # Determine whether the primary and the workers are collocated.
        self.collocate = set(primary_ips) == set(workers_ips)

        # Check whether clients missed their target rate.
        if self.misses != 0:
            Print.warn(
                f'Clients missed their target rate {self.misses:,} time(s)'
            )

    def _merge_results(self, input):
        # Keep the earliest timestamp.
        merged = {}
        for x in input:
            for k, v in x:
                if not k in merged or merged[k] > v:
                    merged[k] = v
        return merged

    def _repair_client_samples(self, log, samples):
        repaired_from_truncated = 0
        interpolated = 0

        # Under heavy logging load, some client lines are occasionally glued
        # together and lose the trailing "transaction <id>" portion while
        # still preserving each timestamp prefix.
        truncated_timestamps = [
            self._to_posix(t)
            for t in findall(
                r'\[+([^\] \[]+) [^\]]*\] Sending sample transac(?=\[)',
                log,
            )
        ]

        if not samples:
            return {
                'recovered_truncated': repaired_from_truncated,
                'interpolated': interpolated,
            }

        ordered_ids = sorted(samples)
        truncated_index = 0
        for prev_id, next_id in zip(ordered_ids, ordered_ids[1:]):
            missing_ids = list(range(prev_id + 1, next_id))
            if not missing_ids:
                continue

            recovered_here = min(
                len(missing_ids),
                len(truncated_timestamps) - truncated_index,
            )
            for offset in range(recovered_here):
                samples[missing_ids[offset]] = truncated_timestamps[
                    truncated_index + offset
                ]
            truncated_index += recovered_here
            repaired_from_truncated += recovered_here

            remaining_ids = missing_ids[recovered_here:]
            if len(remaining_ids) == 1:
                missing_id = remaining_ids[0]
                left_id = missing_id - 1
                right_id = missing_id + 1
                if left_id in samples and right_id in samples:
                    samples[missing_id] = (
                        samples[left_id] + samples[right_id]
                    ) / 2
                    interpolated += 1

        return {
            'recovered_truncated': repaired_from_truncated,
            'interpolated': interpolated,
        }

    def _parse_clients(self, log):
        if search(r'Error', log) is not None:
            raise ParseError('Client(s) panicked')

        size = int(search(r'Transactions size: (\d+)', log).group(1))
        rate = int(search(r'Transactions rate: (\d+)', log).group(1))

        tmp = search(r'\[+([^\] \[]+) [^\]]*\] Start sending transactions', log).group(1)
        start = self._to_posix(tmp)

        misses = len(findall(r'rate too high', log))

        tmp = findall(r'\[+([^\] \[]+) [^\]]*\] Sending sample transaction (\d+)', log)
        samples = {int(s): self._to_posix(t) for t, s in tmp}
        repairs = self._repair_client_samples(log, samples)

        return size, rate, start, misses, samples, repairs

    def _parse_primaries(self, log):
        if search(r'(?:panicked|Error)', log) is not None:
            raise ParseError('Primary(s) panicked')

        tmp = findall(r'\[+([^\] \[]+) [^\]]*\] Created B\d+\([^ ]+\) -> ([^ ]+=)', log)
        tmp = [(d, self._to_posix(t)) for t, d in tmp]
        proposals = self._merge_results([tmp])

        tmp = findall(r'\[+([^\] \[]+) [^\]]*\] Committed B\d+\([^ ]+\) -> ([^ ]+=)', log)
        tmp = [(d, self._to_posix(t)) for t, d in tmp]

        # Newer benchmark builds log commits as DAG_COMMITTED lines instead of
        # the legacy "Committed B..." format.
        if not tmp:
            tmp = findall(
                r'\[+([^\] \[]+) [^\]]*\] DAG_COMMITTED path=\S+ round=\d+ node=\d+ digest=([^ ]+=)',
                log,
            )
            tmp = [(d, self._to_posix(t)) for t, d in tmp]
        commits = self._merge_results([tmp])

        configs = {
            'header_size': int(
                search(r'Header size .* (\d+)', log).group(1)
            ),
            'max_header_delay': int(
                search(r'Max header delay .* (\d+)', log).group(1)
            ),
            'gc_depth': int(
                search(r'Garbage collection depth .* (\d+)', log).group(1)
            ),
            'sync_retry_delay': int(
                search(r'Sync retry delay .* (\d+)', log).group(1)
            ),
            'sync_retry_nodes': int(
                search(r'Sync retry nodes .* (\d+)', log).group(1)
            ),
            'batch_size': int(
                search(r'Batch size .* (\d+)', log).group(1)
            ),
            'max_batch_delay': int(
                search(r'Max batch delay .* (\d+)', log).group(1)
            ),
        }

        ip = search(r'booted on (\d+.\d+.\d+.\d+)', log).group(1)
        
        return proposals, commits, configs, ip

    def _parse_workers(self, log):
        if search(r'(?:panic|Error)', log) is not None:
            raise ParseError('Worker(s) panicked')

        tmp = findall(r'Batch ([^ ]+) contains (\d+) B', log)
        sizes = {d: int(s) for d, s in tmp}

        tmp = findall(r'Batch ([^ ]+) contains sample tx (\d+)', log)
        samples = {int(s): d for d, s in tmp}

        ip = search(r'booted on (\d+.\d+.\d+.\d+)', log).group(1)

        return sizes, samples, ip

    def _to_posix(self, string):
        normalized = string.strip().lstrip('[').rstrip(']')
        x = datetime.fromisoformat(normalized.replace('Z', '+00:00'))
        return datetime.timestamp(x)

    def _consensus_throughput(self):
        if not self.committed_with_proposals:
            return 0, 0, 0
        start = min(self.proposals[d] for d in self.committed_with_proposals)
        end = max(self.commits[d] for d in self.committed_with_proposals)
        duration = end - start
        if duration <= 0:
            return 0, 0, 0
        bytes = sum(self.sizes.get(d, 0) for d in self.committed_with_proposals)
        bps = bytes / duration
        tps = bps / self.size[0]
        return tps, bps, duration

    def _consensus_latency(self):
        latency = [
            self.commits[d] - self.proposals[d]
            for d in self.committed_with_proposals
        ]
        return mean(latency) if latency else 0

    def _end_to_end_throughput(self):
        if not self.commits:
            return 0, 0, 0
        start_candidates = [x for x in self.start if x is not None]
        if start_candidates:
            start = min(start_candidates)
        elif self.committed_with_proposals:
            start = min(self.proposals[d] for d in self.committed_with_proposals)
        else:
            return 0, 0, 0
        end = max(self.commits.values())
        duration = end - start
        if duration <= 0:
            return 0, 0, 0
        bytes = sum(self.sizes.values())
        bps = bytes / duration
        tps = bps / self.size[0]
        return tps, bps, duration

    def _end_to_end_latency(self):
        if self._end_to_end_latency_cache is not None:
            return self._end_to_end_latency_cache

        latency = []
        missing_sent = 0
        for sent, received in zip(self.sent_samples, self.received_samples):
            for tx_id, batch_id in received.items():
                if batch_id in self.commits:
                    if tx_id not in sent:
                        missing_sent += 1
                        continue
                    start = sent[tx_id]
                    end = self.commits[batch_id]
                    latency += [end-start]
        if missing_sent:
            Print.warn(
                f'Skipped {missing_sent:,} committed sample tx(s) missing '
                'client send timestamps'
            )
        self._end_to_end_latency_cache = mean(latency) if latency else 0
        return self._end_to_end_latency_cache

    def result(self):
        header_size = self.configs[0]['header_size']
        max_header_delay = self.configs[0]['max_header_delay']
        gc_depth = self.configs[0]['gc_depth']
        sync_retry_delay = self.configs[0]['sync_retry_delay']
        sync_retry_nodes = self.configs[0]['sync_retry_nodes']
        batch_size = self.configs[0]['batch_size']
        max_batch_delay = self.configs[0]['max_batch_delay']

        consensus_latency = self._consensus_latency() * 1_000
        consensus_tps, consensus_bps, _ = self._consensus_throughput()
        end_to_end_tps, end_to_end_bps, duration = self._end_to_end_throughput()
        end_to_end_latency = self._end_to_end_latency() * 1_000

        return (
            '\n'
            '-----------------------------------------\n'
            ' SUMMARY:\n'
            '-----------------------------------------\n'
            ' + CONFIG:\n'
            f' Faults: {self.faults} node(s)\n'
            f' Committee size: {self.committee_size} node(s)\n'
            f' Worker(s) per node: {self.workers} worker(s)\n'
            f' Collocate primary and workers: {self.collocate}\n'
            f' Input rate: {sum(self.rate):,} tx/s\n'
            f' Transaction size: {self.size[0]:,} B\n'
            f' Execution time: {round(duration):,} s\n'
            '\n'
            f' Header size: {header_size:,} B\n'
            f' Max header delay: {max_header_delay:,} ms\n'
            f' GC depth: {gc_depth:,} round(s)\n'
            f' Sync retry delay: {sync_retry_delay:,} ms\n'
            f' Sync retry nodes: {sync_retry_nodes:,} node(s)\n'
            f' batch size: {batch_size:,} B\n'
            f' Max batch delay: {max_batch_delay:,} ms\n'
            '\n'
            ' + RESULTS:\n'
            f' Consensus TPS: {round(consensus_tps):,} tx/s\n'
            f' Consensus BPS: {round(consensus_bps):,} B/s\n'
            f' Consensus latency: {round(consensus_latency):,} ms\n'
            '\n'
            f' End-to-end TPS: {round(end_to_end_tps):,} tx/s\n'
            f' End-to-end BPS: {round(end_to_end_bps):,} B/s\n'
            f' End-to-end latency: {round(end_to_end_latency):,} ms\n'
            '-----------------------------------------\n'
        )

    def print(self, filename):
        assert isinstance(filename, str)
        with open(filename, 'a') as f:
            f.write(self.result())

    @classmethod
    def process(cls, directory, faults=0, default_client_size=None,
                default_client_rates=None):
        assert isinstance(directory, str)

        clients = []
        for filename in sorted(glob(join(directory, 'client-*.log'))):
            with open(filename, 'r') as f:
                clients += [f.read()]
        primaries = []
        for filename in sorted(glob(join(directory, 'primary-*.log'))):
            with open(filename, 'r') as f:
                primaries += [f.read()]
        workers = []
        for filename in sorted(glob(join(directory, 'worker-*.log'))):
            with open(filename, 'r') as f:
                workers += [f.read()]

        return cls(
            clients,
            primaries,
            workers,
            faults=faults,
            default_client_size=default_client_size,
            default_client_rates=default_client_rates,
        )
