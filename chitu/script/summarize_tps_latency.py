#!/usr/bin/env python3
"""
Summarize TPS and Latency Results

This script reads benchmark_results.json and summarizes all TPS and latency metrics,
including consensus and end-to-end metrics.
"""

import json
import sys
import csv
from pathlib import Path
from typing import Dict, List, Any
from collections import defaultdict


def load_results(results_file: Path) -> List[Dict[str, Any]]:
    """Load benchmark results from JSON file"""
    try:
        with open(results_file, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading results file: {e}", file=sys.stderr)
        sys.exit(1)


def calculate_statistics(values: List[float]) -> Dict[str, float]:
    """Calculate statistics for a list of values"""
    if not values:
        return {'min': 0, 'max': 0, 'avg': 0, 'median': 0}
    
    sorted_values = sorted(values)
    n = len(sorted_values)
    
    return {
        'min': min(values),
        'max': max(values),
        'avg': sum(values) / n,
        'median': sorted_values[n // 2] if n > 0 else 0
    }


def summarize_tps_latency(results: List[Dict[str, Any]], workspace_path: Path) -> None:
    """Summarize TPS and latency metrics in table format"""
    
    # Group by rate and attack
    grouped = defaultdict(lambda: defaultdict(list))
    
    for config in results:
        rate = config['rate']
        attack = config['trigger_network_interrupt']
        
        for run in config.get('runs', []):
            if run.get('success'):
                benchmark = run.get('benchmark', {})
                
                # Collect all metrics (only E2E metrics are needed)
                metrics = {
                    'end_to_end_tps': benchmark.get('end_to_end_tps'),
                    'end_to_end_latency': benchmark.get('end_to_end_latency'),
                }
                
                # Store metrics for this configuration
                for metric_name, metric_value in metrics.items():
                    if metric_value is not None:
                        grouped[(rate, attack)][metric_name].append(metric_value)
    
    # Sort by rate and attack
    sorted_configs = sorted(grouped.keys(), key=lambda x: (x[0], x[1]))
    
    # Print combined table with all metrics and 5 runs (only E2E)
    print("=" * 300)
    print("COMPREHENSIVE TPS AND LATENCY SUMMARY (All 5 Runs)")
    print("=" * 300)
    print()
    
    # Header for the main table - only E2E metrics
    header = (f"{'Rate':>8} | {'Attack':>6} | "
              f"{'E2E TPS (R1-R5)':>50} | "
              f"{'E2E Lat (R1-R5)':>50}")
    print(header)
    print("-" * 300)
    
    for rate, attack in sorted_configs:
        config_metrics = grouped[(rate, attack)]
        
        # Get values for each metric, pad to 5 runs
        def get_values(metric_name):
            if metric_name in config_metrics:
                values = config_metrics[metric_name][:5]
                while len(values) < 5:
                    values.append(0)
                return values
            return [0, 0, 0, 0, 0]
        
        e2e_tps = get_values('end_to_end_tps')
        e2e_lat = get_values('end_to_end_latency')
        
        # Format runs as comma-separated values
        e2e_tps_str = f"{e2e_tps[0]:.0f},{e2e_tps[1]:.0f},{e2e_tps[2]:.0f},{e2e_tps[3]:.0f},{e2e_tps[4]:.0f}"
        e2e_lat_str = f"{e2e_lat[0]:.0f},{e2e_lat[1]:.0f},{e2e_lat[2]:.0f},{e2e_lat[3]:.0f},{e2e_lat[4]:.0f}"
        
        row = (f"{rate:8d} | {str(attack):>6} | "
               f"{e2e_tps_str:>50} | "
               f"{e2e_lat_str:>50}")
        print(row)
    
    print("-" * 300)
    print()
    
    # Print statistics summary table
    print("=" * 200)
    print("STATISTICS SUMMARY (Min, Max, Avg, Median)")
    print("=" * 200)
    print(f"{'Rate':>10} | {'Attack':>8} | {'Metric':>20} | {'Min':>12} | {'Max':>12} | {'Avg':>12} | {'Median':>12}")
    print("-" * 200)
    
    for rate, attack in sorted_configs:
        config_metrics = grouped[(rate, attack)]
        
        # End-to-End TPS
        if 'end_to_end_tps' in config_metrics:
            stats = calculate_statistics(config_metrics['end_to_end_tps'])
            print(f"{rate:10d} | {str(attack):>8} | {'E2E TPS':>20} | "
                  f"{stats['min']:12.0f} | {stats['max']:12.0f} | "
                  f"{stats['avg']:12.0f} | {stats['median']:12.0f}")
        
        # End-to-End Latency
        if 'end_to_end_latency' in config_metrics:
            stats = calculate_statistics(config_metrics['end_to_end_latency'])
            print(f"{rate:10d} | {str(attack):>8} | {'E2E Latency (ms)':>20} | "
                  f"{stats['min']:12.0f} | {stats['max']:12.0f} | "
                  f"{stats['avg']:12.0f} | {stats['median']:12.0f}")
        
        print("-" * 200)
    
    print("=" * 200)
    
    # Save to CSV file
    csv_file = workspace_path / 'script' / 'tps_latency_summary.csv'
    save_to_csv(sorted_configs, grouped, csv_file, calculate_statistics)
    print(f"\nResults saved to CSV: {csv_file}")


def save_to_csv(sorted_configs, grouped, csv_file: Path, calculate_statistics_func) -> None:
    """Save TPS and latency summary to CSV file"""
    try:
        with open(csv_file, 'w', newline='') as f:
            writer = csv.writer(f)
            
            # Write header
            header = [
                'Rate', 'Attack',
                'E2E_TPS_Run1', 'E2E_TPS_Run2', 'E2E_TPS_Run3', 'E2E_TPS_Run4', 'E2E_TPS_Run5',
                'E2E_TPS_Min', 'E2E_TPS_Max', 'E2E_TPS_Avg', 'E2E_TPS_Median',
                'E2E_Lat_Run1', 'E2E_Lat_Run2', 'E2E_Lat_Run3', 'E2E_Lat_Run4', 'E2E_Lat_Run5',
                'E2E_Lat_Min', 'E2E_Lat_Max', 'E2E_Lat_Avg', 'E2E_Lat_Median',
            ]
            writer.writerow(header)
            
            # Write data rows
            for rate, attack in sorted_configs:
                config_metrics = grouped[(rate, attack)]
                
                # Helper function to get values and stats
                def get_values_and_stats(metric_name):
                    if metric_name in config_metrics:
                        values = config_metrics[metric_name][:5]
                        while len(values) < 5:
                            values.append(0)
                        stats = calculate_statistics_func([v for v in values if v > 0])
                        return values, stats
                    return [0, 0, 0, 0, 0], {'min': 0, 'max': 0, 'avg': 0, 'median': 0}
                
                e2e_tps_vals, e2e_tps_stats = get_values_and_stats('end_to_end_tps')
                e2e_lat_vals, e2e_lat_stats = get_values_and_stats('end_to_end_latency')
                
                row = [
                    rate, attack,
                    e2e_tps_vals[0], e2e_tps_vals[1], e2e_tps_vals[2], e2e_tps_vals[3], e2e_tps_vals[4],
                    e2e_tps_stats['min'], e2e_tps_stats['max'], e2e_tps_stats['avg'], e2e_tps_stats['median'],
                    e2e_lat_vals[0], e2e_lat_vals[1], e2e_lat_vals[2], e2e_lat_vals[3], e2e_lat_vals[4],
                    e2e_lat_stats['min'], e2e_lat_stats['max'], e2e_lat_stats['avg'], e2e_lat_stats['median'],
                ]
                writer.writerow(row)
    except Exception as e:
        print(f"Warning: Failed to save CSV file: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()


def main():
    workspace_path = Path(__file__).parent.parent
    results_file = workspace_path / 'script' / 'benchmark_results.json'
    
    if not results_file.exists():
        print(f"Error: Results file not found: {results_file}", file=sys.stderr)
        sys.exit(1)
    
    results = load_results(results_file)
    
    if not results:
        print("No results found in file.", file=sys.stderr)
        sys.exit(1)
    
    summarize_tps_latency(results, workspace_path)


if __name__ == '__main__':
    main()

