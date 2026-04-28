#!/usr/bin/env python3
"""
Automated Benchmark Script

This script runs benchmarks with different rate values and TRIGGER_NETWORK_INTERRUPT settings.
For each combination, it runs fab local 5 times and collects TPS, latency, and bandwidth data.

Usage:
    python script/automated_benchmark.py [--output results.json]
"""

import re
import json
import subprocess
import sys
import time
import csv
from pathlib import Path
from typing import Dict, List, Any
from datetime import datetime
import shutil


def modify_fabfile_rate(rate: int, fabfile_path: Path) -> None:
    """Modify the rate value in fabfile.py"""
    with open(fabfile_path, 'r') as f:
        content = f.read()
    
    # Replace the rate value in bench_params
    pattern = r"('rate':\s*)\d+(_?\d*)?"
    replacement = f"\\g<1>{rate}"
    content = re.sub(pattern, replacement, content)
    
    with open(fabfile_path, 'w') as f:
        f.write(content)


def modify_trigger_network_interrupt(value: bool, attack_rs_path: Path) -> None:
    """Modify TRIGGER_NETWORK_INTERRUPT in adversary/src/attack.rs"""
    with open(attack_rs_path, 'r') as f:
        content = f.read()
    
    # Replace the TRIGGER_NETWORK_INTERRUPT value
    pattern = r"(pub const TRIGGER_NETWORK_INTERRUPT: bool = )\w+"
    replacement = f"\\g<1>{str(value).lower()}"
    content = re.sub(pattern, replacement, content)
    
    with open(attack_rs_path, 'w') as f:
        f.write(content)


def parse_benchmark_output(output: str) -> Dict[str, Any]:
    """Parse TPS and latency from benchmark output"""
    result = {
        'tps': None,
        'latency': None,
        'consensus_tps': None,
        'consensus_latency': None,
        'end_to_end_tps': None,
        'end_to_end_latency': None
    }
    
    # Parse End-to-end TPS
    tps_match = re.search(r'End-to-end TPS:\s+([\d,]+)', output)
    if tps_match:
        result['end_to_end_tps'] = int(tps_match.group(1).replace(',', ''))
        result['tps'] = result['end_to_end_tps']  # Use end-to-end as primary
    
    # Parse End-to-end latency
    latency_match = re.search(r'End-to-end latency:\s+([\d,]+)', output)
    if latency_match:
        result['end_to_end_latency'] = int(latency_match.group(1).replace(',', ''))
        result['latency'] = result['end_to_end_latency']  # Use end-to-end as primary
    
    # Parse Consensus TPS
    consensus_tps_match = re.search(r'Consensus TPS:\s+([\d,]+)', output)
    if consensus_tps_match:
        result['consensus_tps'] = int(consensus_tps_match.group(1).replace(',', ''))
    
    # Parse Consensus latency
    consensus_latency_match = re.search(r'Consensus latency:\s+([\d,]+)', output)
    if consensus_latency_match:
        result['consensus_latency'] = int(consensus_latency_match.group(1).replace(',', ''))
    
    return result


def get_bandwidth_stats(logs_dir: Path) -> Dict[str, Any]:
    """Get bandwidth statistics from logs using wave_bandwidth_analyzer.py"""
    try:
        # Import the wave bandwidth analyzer functions
        sys.path.insert(0, str(Path(__file__).parent))
        from wave_bandwidth_analyzer import aggregate_wave_bandwidth
        
        wave_stats = aggregate_wave_bandwidth(logs_dir)
        
        # Calculate global statistics from all waves
        total_bytes = 0
        total_messages = 0
        total_mbps = 0.0
        per_node_stats = {}
        
        for wave_num, stats in wave_stats.items():
            total_bytes += stats.get('total_bytes', 0)
            total_messages += stats.get('total_messages', 0)
            total_mbps += stats.get('total_mbps', 0.0)
            
            # Aggregate per-node statistics
            if 'nodes' in stats:
                for node_id, node_stat in stats['nodes'].items():
                    if node_id not in per_node_stats:
                        per_node_stats[node_id] = {
                            'bandwidth_mbps': 0.0,
                            'bytes': 0,
                            'messages': 0
                        }
                    per_node_stats[node_id]['bandwidth_mbps'] += node_stat.get('total_mbps', 0.0)
                    per_node_stats[node_id]['bytes'] += node_stat.get('total_bytes', 0)
                    per_node_stats[node_id]['messages'] += node_stat.get('total_messages', 0)
        
        return {
            'global_bandwidth_mbps': total_mbps,
            'global_bandwidth_gbps': total_mbps / 1000.0,
            'global_bytes': total_bytes,
            'global_messages': total_messages,
            'node_count': len(per_node_stats),
            'per_node_stats': per_node_stats,
            'wave_stats': wave_stats  # Store detailed wave stats
        }
    except Exception as e:
        print(f"Warning: Failed to get bandwidth stats: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return {}


def run_fab_local(workspace_path: Path) -> Dict[str, Any]:
    """Run fab local and return parsed results"""
    benchmark_dir = workspace_path / 'benchmark'
    logs_dir = workspace_path / 'benchmark' / 'logs'
    
    # Run fab local
    try:
        result = subprocess.run(
            ['fab', 'local'],
            cwd=benchmark_dir,
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout
        )
        
        output = result.stdout + result.stderr
        
        # Parse benchmark output
        benchmark_result = parse_benchmark_output(output)
        
        # Get bandwidth statistics
        bandwidth_stats = get_bandwidth_stats(logs_dir)
        
        return {
            'success': result.returncode == 0,
            'returncode': result.returncode,
            'benchmark': benchmark_result,
            'bandwidth': bandwidth_stats,
            'raw_output': output[:1000]  # Store first 1000 chars of output
        }
    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'error': 'Timeout',
            'benchmark': {},
            'bandwidth': {}
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'benchmark': {},
            'bandwidth': {}
        }


def backup_file(file_path: Path) -> Path:
    """Create a backup of a file"""
    backup_path = file_path.with_suffix(file_path.suffix + '.backup')
    shutil.copy2(file_path, backup_path)
    return backup_path


def restore_file(file_path: Path, backup_path: Path) -> None:
    """Restore a file from backup"""
    if backup_path.exists():
        shutil.copy2(backup_path, file_path)
        backup_path.unlink()


def save_bandwidth_to_file(bandwidth_stats: Dict[str, Any], output_file: Path, rate: int, trigger: bool, run_num: int) -> None:
    """Save bandwidth statistics to CSV file"""
    try:
        # Check if file exists to determine if we need to write header
        file_exists = output_file.exists()
        
        with open(output_file, 'a', newline='') as f:
            writer = csv.writer(f)
            
            # Write header if file is new
            if not file_exists:
                header = ['run_number', 'rate', 'attack', 'timestamp',
                         'global_bandwidth_mbps', 'global_bandwidth_gbps',
                         'global_bytes', 'global_messages', 'node_count']
                # Add per-node columns
                if 'per_node_stats' in bandwidth_stats:
                    for node_id in sorted(bandwidth_stats['per_node_stats'].keys()):
                        header.extend([
                            f'node_{node_id}_bandwidth_mbps',
                            f'node_{node_id}_bytes',
                            f'node_{node_id}_messages'
                        ])
                writer.writerow(header)
            
            # Write data row
            row = [
                run_num,
                rate,
                trigger,
                datetime.now().isoformat(),
                bandwidth_stats.get('global_bandwidth_mbps', 0),
                bandwidth_stats.get('global_bandwidth_gbps', 0),
                bandwidth_stats.get('global_bytes', 0),
                bandwidth_stats.get('global_messages', 0),
                bandwidth_stats.get('node_count', 0)
            ]
            
            # Add per-node data
            if 'per_node_stats' in bandwidth_stats:
                for node_id in sorted(bandwidth_stats['per_node_stats'].keys()):
                    node_stat = bandwidth_stats['per_node_stats'][node_id]
                    row.extend([
                        node_stat.get('bandwidth_mbps', 0),
                        node_stat.get('bytes', 0),
                        node_stat.get('messages', 0)
                    ])
            
            writer.writerow(row)
    except Exception as e:
        print(f"Warning: Failed to save bandwidth to file: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()


def main():
    workspace_path = Path(__file__).parent.parent
    fabfile_path = workspace_path / 'benchmark' / 'fabfile.py'
    attack_rs_path = workspace_path / 'adversary' / 'src' / 'attack.rs'
    
    # Rate values: 25000, 50000, 75000, ..., 200000
    rates = list(range(220000, 240001, 20000))
    trigger_values = [False]
    
    # Backup original files
    print("Backing up original files...")
    fabfile_backup = backup_file(fabfile_path)
    attack_rs_backup = backup_file(attack_rs_path)
    
    results = []
    
    try:
        total_combinations = len(rates) * len(trigger_values) * 5
        current = 0
        
        for rate in rates:
            for trigger in trigger_values:
                print(f"\n{'='*80}")
                print(f"Testing: rate={rate}, TRIGGER_NETWORK_INTERRUPT={trigger}")
                print(f"{'='*80}")
                
                # Modify configuration files
                modify_fabfile_rate(rate, fabfile_path)
                modify_trigger_network_interrupt(trigger, attack_rs_path)
                
                # Recompile if TRIGGER_NETWORK_INTERRUPT changed
                if trigger:
                    print("Recompiling with TRIGGER_NETWORK_INTERRUPT=true...")
                    subprocess.run(
                        ['cargo', 'build', '--release'],
                        cwd=workspace_path,
                        check=False
                    )
                
                # Run 5 times
                run_results = []
                for run_num in range(1, 6):
                    current += 1
                    print(f"\nRun {run_num}/5 ({current}/{total_combinations})")
                    print(f"Rate: {rate}, TRIGGER_NETWORK_INTERRUPT: {trigger}")
                    
                    result = run_fab_local(workspace_path)
                    result['run_number'] = run_num
                    result['rate'] = rate
                    result['trigger_network_interrupt'] = trigger
                    result['timestamp'] = datetime.now().isoformat()
                    
                    run_results.append(result)
                    
                    if result['success']:
                        print(f"✓ Success - TPS: {result['benchmark'].get('tps', 'N/A')}, "
                              f"Latency: {result['benchmark'].get('latency', 'N/A')} ms")
                        
                        # Save bandwidth information to file
                        output_dir = workspace_path / 'script' / 'bandwidth_results'
                        output_dir.mkdir(parents=True, exist_ok=True)
                        filename = f"input={rate}_attack={trigger}.csv"
                        output_file = output_dir / filename
                        
                        save_bandwidth_to_file(result['bandwidth'], output_file, rate, trigger, run_num)
                        print(f"  Bandwidth data saved to {output_file}")
                    else:
                        print(f"✗ Failed - {result.get('error', 'Unknown error')}")
                    
                    # Rest for 20 seconds before next run
                    if run_num < 5:  # Don't wait after the last run
                        print(f"  Resting for 20 seconds before next run...")
                        time.sleep(20)
                
                # Store results for this configuration
                results.append({
                    'rate': rate,
                    'trigger_network_interrupt': trigger,
                    'runs': run_results,
                    'summary': {
                        'successful_runs': sum(1 for r in run_results if r['success']),
                        'avg_tps': sum(r['benchmark'].get('tps', 0) or 0 for r in run_results if r['success']) / max(1, sum(1 for r in run_results if r['success'])),
                        'avg_latency': sum(r['benchmark'].get('latency', 0) or 0 for r in run_results if r['success']) / max(1, sum(1 for r in run_results if r['success'])),
                        'avg_bandwidth_mbps': sum(r['bandwidth'].get('global_bandwidth_mbps', 0) or 0 for r in run_results if r['success']) / max(1, sum(1 for r in run_results if r['success']))
                    }
                })
                
                # Save intermediate results
                output_file = workspace_path / 'script' / 'benchmark_results.json'
                output_file.parent.mkdir(parents=True, exist_ok=True)
                with open(output_file, 'w') as f:
                    json.dump(results, f, indent=2)
                print(f"\nIntermediate results saved to {output_file}")
    
    finally:
        # Restore original files
        print("\nRestoring original files...")
        restore_file(fabfile_path, fabfile_backup)
        restore_file(attack_rs_path, attack_rs_backup)
    
    # Save final results
    output_file = workspace_path / 'script' / 'benchmark_results.json'
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\n{'='*80}")
    print(f"All tests completed! Results saved to {output_file}")
    print(f"{'='*80}")
    
    # Print summary
    print("\nSummary:")
    for result in results:
        rate = result['rate']
        trigger = result['trigger_network_interrupt']
        summary = result['summary']
        print(f"Rate: {rate:6d}, Trigger: {trigger:5s} - "
              f"Success: {summary['successful_runs']}/5, "
              f"Avg TPS: {summary['avg_tps']:.0f}, "
              f"Avg Latency: {summary['avg_latency']:.0f} ms, "
              f"Avg Bandwidth: {summary['avg_bandwidth_mbps']:.2f} Mbps")


if __name__ == '__main__':
    main()