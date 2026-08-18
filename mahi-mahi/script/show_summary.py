#!/usr/bin/env python3
"""
Summary Output Script

This script reads the benchmark results JSON file and outputs only the summary
with per-node bandwidth information.

Usage:
    python script/show_summary.py [--input benchmark_results.json]
"""

import json
import sys
import argparse
from pathlib import Path
from collections import defaultdict


def print_summary(results_file: Path):
    """Read results file and print summary with per-node bandwidth"""
    if not results_file.exists():
        print(f"Error: Results file not found: {results_file}", file=sys.stderr)
        sys.exit(1)
    
    try:
        with open(results_file, 'r') as f:
            results = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Failed to parse JSON file: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: Failed to read file: {e}", file=sys.stderr)
        sys.exit(1)
    
    if not results:
        print("No results found in file.", file=sys.stderr)
        sys.exit(1)
    
    # Print header
    print("=" * 120)
    print("BENCHMARK SUMMARY")
    print("=" * 120)
    print(f"{'Rate':>8} | {'Trigger':>8} | {'Success':>10} | {'Avg TPS':>12} | {'Avg Latency':>15} | {'Node Bandwidths (Mbps)':>30}")
    print("-" * 120)
    
    # Print summary for each configuration
    for result in results:
        rate = result.get('rate', 0)
        trigger = result.get('trigger_network_interrupt', False)
        summary = result.get('summary', {})
        runs = result.get('runs', [])
        
        successful_runs = summary.get('successful_runs', 0)
        avg_tps = summary.get('avg_tps', 0.0)
        avg_latency = summary.get('avg_latency', 0.0)
        
        # Calculate average bandwidth per node across all successful runs
        node_bandwidths = defaultdict(list)
        for run in runs:
            if run.get('success') and 'bandwidth' in run:
                per_node = run['bandwidth'].get('per_node_stats', {})
                for node_id, node_stats in per_node.items():
                    node_bandwidths[node_id].append(node_stats.get('bandwidth_mbps', 0.0))
        
        # Calculate averages
        avg_node_bandwidths = {}
        for node_id in sorted(node_bandwidths.keys()):
            if node_bandwidths[node_id]:
                avg_node_bandwidths[node_id] = sum(node_bandwidths[node_id]) / len(node_bandwidths[node_id])
        
        # Format node bandwidths as string
        if avg_node_bandwidths:
            node_bw_str = ", ".join([f"Node{i}: {avg_node_bandwidths[i]:.2f}" 
                                     for i in sorted(avg_node_bandwidths.keys())])
        else:
            node_bw_str = "N/A"
        
        trigger_str = "True" if trigger else "False"
        
        print(f"{rate:8d} | {trigger_str:>8} | {successful_runs:>3}/5      | "
              f"{avg_tps:>12.0f} | {avg_latency:>15.0f} ms | {node_bw_str}")
    
    print("=" * 120)
    
    # Print detailed per-node bandwidth statistics grouped by trigger value
    print("\n" + "=" * 120)
    print("DETAILED PER-NODE BANDWIDTH STATISTICS")
    print("=" * 120)
    
    for trigger in [False, True]:
        trigger_str = "True" if trigger else "False"
        filtered_results = [r for r in results if r.get('trigger_network_interrupt') == trigger]
        
        if not filtered_results:
            continue
        
        print(f"\nTRIGGER_NETWORK_INTERRUPT = {trigger_str}:")
        print("-" * 120)
        
        for result in filtered_results:
            rate = result.get('rate', 0)
            runs = result.get('runs', [])
            summary = result.get('summary', {})
            
            successful_runs = summary.get('successful_runs', 0)
            avg_tps = summary.get('avg_tps', 0.0)
            avg_latency = summary.get('avg_latency', 0.0)
            
            print(f"\nRate: {rate}")
            print(f"  Success: {successful_runs}/5, Avg TPS: {avg_tps:.0f}, Avg Latency: {avg_latency:.0f} ms")
            
            # Collect per-node bandwidths from all successful runs
            node_bandwidths = defaultdict(list)
            for run in runs:
                if run.get('success') and 'bandwidth' in run:
                    per_node = run['bandwidth'].get('per_node_stats', {})
                    for node_id, node_stats in per_node.items():
                        node_bandwidths[node_id].append(node_stats.get('bandwidth_mbps', 0.0))
            
            if node_bandwidths:
                print(f"  Per-Node Bandwidth (Mbps):")
                for node_id in sorted(node_bandwidths.keys()):
                    bandwidths = node_bandwidths[node_id]
                    avg_bw = sum(bandwidths) / len(bandwidths)
                    min_bw = min(bandwidths)
                    max_bw = max(bandwidths)
                    print(f"    Node {node_id}: {avg_bw:.2f} (min: {min_bw:.2f}, max: {max_bw:.2f})")
            else:
                print(f"  Per-Node Bandwidth: N/A")
    
    print("=" * 120)


def main():
    parser = argparse.ArgumentParser(
        description='Display summary of benchmark results with per-node bandwidth'
    )
    parser.add_argument(
        '--input',
        type=Path,
        default=Path(__file__).parent / 'benchmark_results.json',
        help='Path to benchmark results JSON file (default: script/benchmark_results.json)'
    )
    
    args = parser.parse_args()
    print_summary(args.input)


if __name__ == '__main__':
    main()