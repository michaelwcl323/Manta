#!/usr/bin/env python3
"""
Script to run CloudLab benchmark and process logs
This script runs 'fab cloudlab_remote', downloads logs, and processes them
"""

import sys
import os
import subprocess
import argparse
from pathlib import Path

# Add benchmark directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from benchmark.logs import LogParser, ParseError
from benchmark.utils import PathMaker, Print, BenchError

def run_fab_command(task='cloudlab_remote', debug=False):
    """Run fab command"""
    fab_cmd = ['fab', task]
    if debug:
        fab_cmd.append('debug=True')
    
    Print.info(f'Running: {" ".join(fab_cmd)}')
    Print.info('=' * 60)
    
    try:
        result = subprocess.run(
            fab_cmd,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            check=False  # Don't raise on error, we'll check return code
        )
        return result.returncode == 0
    except FileNotFoundError:
        Print.error('fab command not found. Please install fabric:')
        Print.error('  pip install fabric')
        return False
    except Exception as e:
        Print.error(f'Failed to run fab command: {e}')
        return False

def download_logs_if_needed(settings_file='cloudlab_settings.json', max_workers=1):
    """Download logs if they don't exist locally"""
    logs_dir = Path(PathMaker.logs_path())
    
    # Check if logs already exist
    primary_logs = list(logs_dir.glob('primary-*.log'))
    worker_logs = list(logs_dir.glob('worker-*.log'))
    client_logs = list(logs_dir.glob('client-*.log'))
    
    if primary_logs or worker_logs or client_logs:
        Print.info(f'Found existing logs: {len(primary_logs)} primary, {len(worker_logs)} worker, {len(client_logs)} client')
        return True
    
    # Try to download logs
    Print.info('No local logs found, attempting to download from remote nodes...')
    try:
        from download_logs import download_logs
        return download_logs(settings_file, max_workers)
    except ImportError:
        Print.warn('download_logs.py not found, skipping download')
        return False

def process_logs(faults=0, save_to_file=False):
    """Process and display log results"""
    logs_dir = PathMaker.logs_path()
    
    if not os.path.exists(logs_dir):
        Print.error(f'Logs directory not found: {logs_dir}')
        return False
    
    try:
        parser = LogParser.process(logs_dir, faults=faults)
        result = parser.result()
        
        # Print results
        print(result)

        return True
        
    except ParseError as e:
        Print.warn(f'Failed to parse logs: {e}')
        Print.warn('This may be because some log files are empty or incomplete.')
        return False
    except Exception as e:
        Print.warn(f'Error processing logs: {e}')
        return False

def main():
    parser = argparse.ArgumentParser(
        description='Run CloudLab benchmark and process logs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Run benchmark and process logs
  python3 run_cloudlab_benchmark.py

  # Only process existing logs (skip running benchmark)
  python3 run_cloudlab_benchmark.py --no-run

  # Run benchmark with debug mode
  python3 run_cloudlab_benchmark.py --debug

  # Only download logs without running benchmark
  python3 run_cloudlab_benchmark.py --download-only
        '''
    )
    
    parser.add_argument('--no-run', action='store_true',
                       help='Skip running fab cloudlab_remote, only process existing logs')
    parser.add_argument('--download-only', action='store_true',
                       help='Only download logs from remote nodes, do not run benchmark or process')
    parser.add_argument('--debug', action='store_true',
                       help='Run benchmark in debug mode')
    parser.add_argument('--faults', type=int, default=0,
                       help='Number of faulty nodes (default: 0)')
    parser.add_argument('--no-save', action='store_true',
                       help='Do not save results to file')
    parser.add_argument('--max-workers', type=int, default=1,
                       help='Maximum number of workers per node for log download (default: 1)')
    parser.add_argument('--settings', default='cloudlab_settings.json',
                       help='Path to CloudLab settings file (default: cloudlab_settings.json)')
    
    args = parser.parse_args()
    
    success = True
    
    # Step 1: Run benchmark (unless skipped)
    if not args.no_run and not args.download_only:
        success = run_fab_command('cloudlab_remote', debug=args.debug)
        if not success:
            Print.warn('Benchmark run completed with errors, but continuing to process logs...')
    
    # Step 2: Download logs if needed (unless download-only)
    if not args.download_only:
        download_logs_if_needed(args.settings, args.max_workers)
    else:
        # Download-only mode
        Print.info('Download-only mode: downloading logs from remote nodes...')
        download_logs_if_needed(args.settings, args.max_workers)
        return 0
    
    # Step 3: Process logs
    success = process_logs(faults=args.faults, save_to_file=False) and success
    
    return 0 if success else 1

if __name__ == '__main__':
    sys.exit(main())

