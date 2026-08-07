#!/usr/bin/env python3
"""
Parameter sweep script for cloudlab benchmarks.
Generates all combinations of parameters and runs fab cloudlab-remote for each.
"""

import os
import sys
import subprocess
from itertools import product
from pathlib import Path

# Parameter values
SIGMA_VALUES = [5]
KAPPA_VALUES = [4]
REFERENCE_VALUES = [1, 4, 7, 10]
COVERAGE_VALUE = 7
INPUT_RATE_VALUES = [100000]  # Can add more rates like [20000, 40000, 80000]

# Change to benchmark directory
BENCHMARK_DIR = Path(__file__).parent.parent / 'benchmark'
os.chdir(BENCHMARK_DIR)

def run_fab_command(sigma, kappa, reference, coverage, run_idx, total_runs, input_rate=20000):
    """Run a single fab cloudlab-remote command with the given parameters."""
    cmd = [
        'fab',
        'cloudlab-remote',
        f'--sigma={sigma}',
        f'--kappa={kappa}',
        f'--reference={reference}',
        f'--coverage={coverage}',
    ]
    
    # Create custom folder prefix with parameter values
    folder_prefix = f'sigma{sigma}_kappa{kappa}_reference{reference}_input_rate{input_rate}'
    
    print(f"\n{'='*80}")
    print(f"Run {run_idx}/{total_runs}")
    print(f"Parameters: sigma={sigma}, kappa={kappa}, reference={reference}, coverage={coverage}, input_rate={input_rate}")
    print(f"Folder prefix: {folder_prefix}")
    print(f"Command: {' '.join(cmd)}")
    print(f"{'='*80}\n")
    
    try:
        # Pass the folder prefix as environment variable
        env = os.environ.copy()
        env['MANTA_FOLDER_PREFIX'] = folder_prefix
        result = subprocess.run(cmd, check=True, env=env)
        print(f"✓ Run {run_idx}/{total_runs} completed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ Run {run_idx}/{total_runs} failed with exit code {e.returncode}")
        return False

def main():
    # Generate all parameter combinations
    param_combinations = list(product(SIGMA_VALUES, KAPPA_VALUES, REFERENCE_VALUES, [COVERAGE_VALUE], INPUT_RATE_VALUES))
    total_runs = len(param_combinations)
    
    print(f"\n{'='*80}")
    print(f"Parameter Sweep Configuration")
    print(f"{'='*80}")
    print(f"sigma: {SIGMA_VALUES}")
    print(f"kappa: {KAPPA_VALUES}")
    print(f"reference: {REFERENCE_VALUES}")
    print(f"coverage: {COVERAGE_VALUE}")
    print(f"input_rate: {INPUT_RATE_VALUES}")
    print(f"\nTotal combinations: {total_runs}")
    print(f"{'='*80}\n")
    
    # Ask for confirmation
    response = input("Do you want to proceed? (yes/no): ").strip().lower()
    if response not in ['yes', 'y']:
        print("Aborted.")
        return
    
    successful_runs = 0
    failed_runs = 0
    failed_params = []
    
    # Run each combination
    for run_idx, (sigma, kappa, reference, coverage, input_rate) in enumerate(param_combinations, 1):
        success = run_fab_command(sigma, kappa, reference, coverage, run_idx, total_runs, input_rate)
        if success:
            successful_runs += 1
        else:
            failed_runs += 1
            failed_params.append((sigma, kappa, reference, coverage, input_rate))
    
    # Print summary
    print(f"\n{'='*80}")
    print(f"SUMMARY")
    print(f"{'='*80}")
    print(f"Total runs: {total_runs}")
    print(f"Successful: {successful_runs}")
    print(f"Failed: {failed_runs}")
    
    if failed_params:
        print(f"\nFailed parameter combinations:")
        for sigma, kappa, reference, coverage, input_rate in failed_params:
            print(f"  sigma={sigma}, kappa={kappa}, reference={reference}, coverage={coverage}, input_rate={input_rate}")
    
    print(f"{'='*80}\n")

if __name__ == '__main__':
    main()
