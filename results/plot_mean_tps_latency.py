#!/usr/bin/env python3
"""Plot mean consensus throughput and latency from unified experiment results."""

import re
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt


RESULTS_ROOT = Path(__file__).resolve().parent
OUTPUT_FILE = RESULTS_ROOT / '50nodes_protocol_comparison.pdf'
PROTOCOL_NAMES = {
    'Manta-result': 'Manta',
    'Chitu-results': 'Chitu',
    'Tusk-result': 'Tusk',
    'DAG-rider-result': 'DAG-Rider',
    'Mahi-mahi-result': 'Mahi-Mahi',
}
COLORS = {
    'Manta': '#4C956C',
    'Chitu': '#1399B2',
    'Tusk': '#7B5EA7',
    'DAG-Rider': '#F39C12',
    'Mahi-Mahi': '#B22222',
}
BENCH_BLOCK = re.compile(
    r'Input rate:\s*([\d,]+)\s*tx/s[\s\S]*?'
    r'Consensus TPS:\s*([\d,]+)\s*tx/s[\s\S]*?'
    r'Consensus latency:\s*([\d,]+)\s*ms'
)


def collect():
    values = defaultdict(lambda: defaultdict(list))
    for directory, protocol in PROTOCOL_NAMES.items():
        for path in (RESULTS_ROOT / directory).glob('*.txt'):
            text = path.read_text(encoding='utf-8', errors='replace')
            for match in BENCH_BLOCK.finditer(text):
                rate, tps, latency = (
                    int(value.replace(',', '')) for value in match.groups()
                )
                values[protocol][rate].append((tps, latency))
    return values


def main():
    values = collect()
    if not values:
        raise SystemExit('No benchmark summaries found under results/')

    plt.rcParams['font.family'] = 'DejaVu Sans'
    fig, axis = plt.subplots(figsize=(14, 8), layout='constrained')
    for protocol in PROTOCOL_NAMES.values():
        points = []
        for rate, samples in sorted(values.get(protocol, {}).items()):
            mean_tps = sum(sample[0] for sample in samples) / len(samples)
            mean_latency = sum(sample[1] for sample in samples) / len(samples)
            points.append((rate, mean_tps, mean_latency))
        if points:
            axis.plot(
                [point[1] / 1000 for point in points],
                [point[2] / 1000 for point in points],
                'o-',
                label=protocol,
                color=COLORS[protocol],
                linewidth=5,
                markersize=12,
                markeredgewidth=0,
                alpha=0.95,
            )

    axis.set_xlabel('Throughput (KTps)', fontsize=36)
    axis.set_ylabel('Latency (s)', fontsize=36)
    axis.tick_params(axis='both', labelsize=30)
    axis.grid(True, linestyle=':', alpha=0.6)
    axis.legend(fontsize=24)
    fig.savefig(OUTPUT_FILE)
    print(f'Wrote {OUTPUT_FILE}')


if __name__ == '__main__':
    main()
