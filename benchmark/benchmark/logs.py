# Copyright(C) Facebook, Inc. and its affiliates.
from datetime import datetime
from glob import glob
from multiprocessing import Pool
from os.path import join
from re import compile, findall, search
from statistics import mean

from benchmark.utils import Print


class ParseError(Exception):
    pass


class LogParser:
    def __init__(self, clients, primaries, workers, faults=0):
        inputs = [clients, primaries, workers]
        assert all(isinstance(x, list) for x in inputs)
        assert all(isinstance(x, str) for y in inputs for x in y)
        assert all(x for x in inputs)

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
            raise ParseError(f'Failed to parse clients\' logs: {e}')
        self.size, self.rate, self.start, misses, self.sent_samples \
            = zip(*results)
        self.misses = sum(misses)

        # Parse the primaries logs.
        try:
            with Pool() as p:
                results = p.map(self._parse_primaries, primaries)
        except (ValueError, IndexError, AttributeError) as e:
            raise ParseError(f'Failed to parse nodes\' logs: {e}')
        proposals, commits, self.configs, primary_ips = zip(*results)
        self.proposals = self._merge_results([x.items() for x in proposals])
        self.commits = self._merge_results([x.items() for x in commits])
        missing_proposals = set(self.commits) - set(self.proposals)
        if missing_proposals:
            Print.warn(
                f'Skipping {len(missing_proposals):,} commit(s) without matching proposal '
                '(likely due to incomplete primary logs)'
            )
            self.commits = {
                digest: ts for digest, ts in self.commits.items()
                if digest in self.proposals
            }

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

        self.vertex_stats = {}
        self.warmup_start = min(self.start) if self.start else None
        self.origin_mapping = []
        self.origin_mapping_note = ''

    def _merge_results(self, input):
        # Keep the earliest timestamp.
        merged = {}
        for x in input:
            for k, v in x:
                if not k in merged or merged[k] > v:
                    merged[k] = v
        return merged

    def _parse_clients(self, log):
        if search(r'Error', log) is not None:
            raise ParseError('Client(s) panicked')

        size = int(search(r'Transactions size: (\d+)', log).group(1))
        rate = int(search(r'Transactions rate: (\d+)', log).group(1))

        tmp = search(r'\[(.*Z) .* Start ', log).group(1)
        start = self._to_posix(tmp)

        misses = len(findall(r'rate too high', log))

        tmp = findall(r'\[(.*Z) .* sample transaction (\d+)', log)
        samples = {int(s): self._to_posix(t) for t, s in tmp}

        return size, rate, start, misses, samples

    def _parse_primaries(self, log):
        if search(r'(?:panicked|Error)', log) is not None:
            raise ParseError('Primary(s) panicked')

        tmp = findall(r'\[(.*Z) .* Created B\d+\([^ ]+\) -> ([^ ]+=)', log)
        tmp = [(d, self._to_posix(t)) for t, d in tmp]
        proposals = self._merge_results([tmp])

        tmp = findall(r'\[(.*Z) .* Committed B\d+\([^ ]+\) -> ([^ ]+=)', log)
        tmp = [(d, self._to_posix(t)) for t, d in tmp]
        commits = self._merge_results([tmp])

        max_header_batches_match = search(r'Max header batches .* (\d+)', log)
        configs = {
            'header_size': int(
                search(r'Header size .* (\d+)', log).group(1)
            ),
            'max_header_batches': (
                int(max_header_batches_match.group(1))
                if max_header_batches_match is not None
                else None
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
        x = datetime.fromisoformat(string.replace('Z', '+00:00'))
        return datetime.timestamp(x)

    @staticmethod
    def _collect_vertex_stats(directory, warmup_start=None):
        pattern = compile(
            r'\[(?P<timestamp>.*?Z)\s+.*?\]\s+.*?VERTEX_STATS round=(?P<round>\d+) payload_entries=(?P<entries>\d+) '
            r'workload_bytes=(?P<workload>\d+) serialized_header_bytes=(?P<size>\d+)'
        )
        rows_by_source = {}
        for filename in sorted(glob(join(directory, 'primary-*.log'))):
            source = filename.split('/')[-1]
            rows = []
            with open(filename, 'r') as handle:
                for line in handle:
                    match = pattern.search(line)
                    if match:
                        timestamp = LogParser._to_posix(
                            LogParser, match.group('timestamp')
                        )
                        if warmup_start is not None and timestamp < warmup_start:
                            continue
                        rows.append({
                            'timestamp': timestamp,
                            'round': int(match.group('round')),
                            'entries': int(match.group('entries')),
                            'workload': int(match.group('workload')),
                            'size': int(match.group('size')),
                        })
            if rows:
                rows_by_source[source] = rows
        return rows_by_source

    @staticmethod
    def _percentile(values, percentile):
        if not values:
            return 0
        ordered = sorted(values)
        index = round((len(ordered) - 1) * percentile)
        return ordered[index]

    @classmethod
    def _format_distribution(cls, values):
        if not values:
            return 'avg=0 B, p50=0 B, p90=0 B, min=0 B, max=0 B'
        return (
            f'avg={round(mean(values)):,} B, '
            f'p50={cls._percentile(values, 0.50):,} B, '
            f'p90={cls._percentile(values, 0.90):,} B, '
            f'min={min(values):,} B, '
            f'max={max(values):,} B'
        )

    def _vertex_stats_summary(self):
        if not self.vertex_stats:
            return ''

        all_rows = [row for rows in self.vertex_stats.values() for row in rows]
        if not all_rows:
            return ''

        total_vertices = len(all_rows)
        non_empty_vertices = [row for row in all_rows if row['workload'] > 0]
        zero_vertices = total_vertices - len(non_empty_vertices)
        serialized_sizes = [row['size'] for row in all_rows]
        workload_sizes = [row['workload'] for row in all_rows]
        non_empty_serialized_sizes = [row['size'] for row in non_empty_vertices]
        non_empty_workload_sizes = [row['workload'] for row in non_empty_vertices]

        lines = [
            '\n',
            ' + VERTEX STATS:\n',
            ' Vertex stats exclude warmup rounds before the first client started sending transactions.\n',
            f' Vertex samples observed: {total_vertices:,}\n',
            f' Non-empty vertices: {len(non_empty_vertices):,} / {total_vertices:,}\n',
            f' Empty vertices: {zero_vertices:,} / {total_vertices:,}\n',
            f' Serialized vertex size (all): {self._format_distribution(serialized_sizes)}\n',
            f' Inlined workload bytes (all): {self._format_distribution(workload_sizes)}\n',
            f' Serialized vertex size (non-empty only): '
            f'{self._format_distribution(non_empty_serialized_sizes)}\n',
            f' Inlined workload bytes (non-empty only): '
            f'{self._format_distribution(non_empty_workload_sizes)}\n',
            ' Per-node comparison (size stats use non-empty vertices only):\n',
        ]

        for source, rows in sorted(self.vertex_stats.items()):
            non_empty_rows = [row for row in rows if row['workload'] > 0]
            non_empty_ratio = (
                (len(non_empty_rows) / len(rows) * 100)
                if rows else 0
            )
            first_observed_round = min(row['round'] for row in rows) if rows else None
            last_observed_round = max(row['round'] for row in rows) if rows else None

            if non_empty_rows:
                node_sizes = [row['size'] for row in non_empty_rows]
                node_workloads = [row['workload'] for row in non_empty_rows]
                node_entries = [row['entries'] for row in non_empty_rows]
                lines.append(
                    f'  {source}: non_empty={len(non_empty_rows):,}/{len(rows):,} '
                    f'({non_empty_ratio:.1f}%), '
                    f'avg_size={round(mean(node_sizes)):,} B, '
                    f'p50_size={self._percentile(node_sizes, 0.50):,} B, '
                    f'p90_size={self._percentile(node_sizes, 0.90):,} B, '
                    f'max_size={max(node_sizes):,} B, '
                    f'avg_workload={round(mean(node_workloads)):,} B, '
                    f'avg_entries={round(mean(node_entries)):,}, '
                    f'active_rounds={first_observed_round}-{last_observed_round}\n'
                )
            else:
                lines.append(
                    f'  {source}: non_empty=0/{len(rows):,} (0.0%), '
                    'avg_size=0 B, p50_size=0 B, p90_size=0 B, '
                    'max_size=0 B, avg_workload=0 B, avg_entries=0, '
                    f'active_rounds={first_observed_round}-{last_observed_round}\n'
                )

        return ''.join(lines)

    def _origin_mapping_summary(self):
        if not self.origin_mapping:
            return ''

        lines = [
            '\n',
            ' + CERTIFICATE ORIGIN MAP:\n',
        ]
        if self.origin_mapping_note:
            lines.append(f' {self.origin_mapping_note}\n')

        for entry in self.origin_mapping:
            lines.append(
                f'  node{entry["node_id"]}: '
                f'ip={entry["ip"]}, '
                f'region={entry["region"] or "unknown"}, '
                f'key_short={entry["short_public_key"]}, '
                f'key_full={entry["full_public_key"]}\n'
            )

        return ''.join(lines)

    def _consensus_throughput(self):
        if not self.commits or not self.proposals:
            return 0, 0, 0
        start, end = min(self.proposals.values()), max(self.commits.values())
        duration = end - start
        bytes = sum(self.sizes.values())
        bps = bytes / duration
        tps = bps / self.size[0]
        return tps, bps, duration

    def _consensus_latency(self):
        latency = [c - self.proposals[d] for d, c in self.commits.items() if d in self.proposals]
        return mean(latency) if latency else 0

    def _end_to_end_throughput(self):
        if not self.commits:
            return 0, 0, 0
        start, end = min(self.start), max(self.commits.values())
        duration = end - start
        bytes = sum(self.sizes.values())
        bps = bytes / duration
        tps = bps / self.size[0]
        return tps, bps, duration

    def _end_to_end_latency(self):
        latency = []
        for sent, received in zip(self.sent_samples, self.received_samples):
            for tx_id, batch_id in received.items():
                if batch_id in self.commits:
                    assert tx_id in sent  # We receive txs that we sent.
                    start = sent[tx_id]
                    end = self.commits[batch_id]
                    latency += [end-start]
        return mean(latency) if latency else 0

    def result(self, include_vertex_stats=True, include_origin_mapping=True):
        header_size = self.configs[0]['header_size']
        max_header_batches = self.configs[0]['max_header_batches']
        max_header_delay = self.configs[0]['max_header_delay']
        gc_depth = self.configs[0]['gc_depth']
        sync_retry_delay = self.configs[0]['sync_retry_delay']
        sync_retry_nodes = self.configs[0]['sync_retry_nodes']
        batch_size = self.configs[0]['batch_size']
        max_batch_delay = self.configs[0]['max_batch_delay']
        header_batches_line = (
            f' Max header batches: {max_header_batches:,} batch(es)\n'
            if max_header_batches is not None
            else ''
        )

        consensus_latency = self._consensus_latency() * 1_000
        consensus_tps, consensus_bps, _ = self._consensus_throughput()
        end_to_end_tps, end_to_end_bps, duration = self._end_to_end_throughput()
        end_to_end_latency = self._end_to_end_latency() * 1_000
        vertex_stats_block = (
            self._vertex_stats_summary() if include_vertex_stats else ''
        )
        origin_mapping_block = (
            self._origin_mapping_summary() if include_origin_mapping else ''
        )

        return (
            '\n'
            + '-----------------------------------------\n'
            + ' SUMMARY:\n'
            + '-----------------------------------------\n'
            + ' + CONFIG:\n'
            + f' Faults: {self.faults} node(s)\n'
            + f' Committee size: {self.committee_size} node(s)\n'
            + f' Worker(s) per node: {self.workers} worker(s)\n'
            + f' Collocate primary and workers: {self.collocate}\n'
            + f' Input rate: {sum(self.rate):,} tx/s\n'
            + f' Transaction size: {self.size[0]:,} B\n'
            + f' Execution time: {round(duration):,} s\n'
            + '\n'
            + f' Header size: {header_size:,} B\n'
            + header_batches_line
            + f' Max header delay: {max_header_delay:,} ms\n'
            + f' GC depth: {gc_depth:,} round(s)\n'
            + f' Sync retry delay: {sync_retry_delay:,} ms\n'
            + f' Sync retry nodes: {sync_retry_nodes:,} node(s)\n'
            + f' batch size: {batch_size:,} B\n'
            + f' Max batch delay: {max_batch_delay:,} ms\n'
            + '\n'
            + ' + RESULTS:\n'
            + f' Consensus TPS: {round(consensus_tps):,} tx/s\n'
            + f' Consensus BPS: {round(consensus_bps):,} B/s\n'
            + f' Consensus latency: {round(consensus_latency):,} ms\n'
            + '\n'
            + f' End-to-end TPS: {round(end_to_end_tps):,} tx/s\n'
            + f' End-to-end BPS: {round(end_to_end_bps):,} B/s\n'
            + f' End-to-end latency: {round(end_to_end_latency):,} ms\n'
            + vertex_stats_block
            + origin_mapping_block
            + '-----------------------------------------\n'
        )

    def print(self, filename):
        assert isinstance(filename, str)
        with open(filename, 'a') as f:
            f.write(self.result())

    @classmethod
    def process(cls, directory, faults=0):
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

        parser = cls(clients, primaries, workers, faults=faults)
        parser.vertex_stats = cls._collect_vertex_stats(
            directory,
            warmup_start=parser.warmup_start,
        )
        return parser
