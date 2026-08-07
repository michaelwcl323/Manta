#!/usr/bin/env python3
"""
Script to download logs from CloudLab remote nodes
Downloads logs through node0
"""

import sys
import os
import time
import json
from pathlib import Path
from fabric import Connection
from paramiko import RSAKey
from paramiko.ssh_exception import PasswordRequiredException, SSHException
import signal
from contextlib import contextmanager

# Add benchmark directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from benchmark.cloudlab_settings import CloudLabSettings
from benchmark.cloudlab_instance import CloudLabInstanceManager
from benchmark.utils import PathMaker, Print

def get_connection_kwargs(key_path):
    """Get SSH connection kwargs"""
    try:
        # Try to load key without password first
        try:
            key = RSAKey.from_private_key_file(key_path)
            return {'pkey': key}
        except PasswordRequiredException:
            # Key is password-protected, try to get password
            password = os.environ.get('SSH_KEY_PASSWORD')
            
            # Try to get password from cloudlab_settings.json if it exists
            if not password:
                try:
                    settings_file = Path(__file__).parent / 'cloudlab_settings.json'
                    if settings_file.exists():
                        with open(settings_file, 'r') as f:
                            settings_data = json.load(f)
                            password = settings_data.get('ssh_key_password')
                except Exception:
                    pass
            
            if password:
                key = RSAKey.from_private_key_file(key_path, password=password)
                return {'pkey': key}
            else:
                Print.error('SSH key is password-protected. Please provide password via SSH_KEY_PASSWORD environment variable or ssh_key_password in cloudlab_settings.json')
                return {}
    except (FileNotFoundError, PasswordRequiredException, SSHException) as e:
        Print.error(f'Failed to load SSH key: {e}')
        return {}


@contextmanager
def timeout_context(seconds):
    """Context manager for timeout"""
    def timeout_handler(signum, frame):
        raise TimeoutError(f'Operation timed out after {seconds} seconds')
    
    # Set the signal handler
    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(seconds)
    
    try:
        yield
    finally:
        # Restore the old handler and cancel the alarm
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

def check_file_exists(conn, remote_path):
    """Check if a file exists on remote host"""
    try:
        result = conn.run(f'test -f {remote_path} && echo "exists" || echo "not_found"', hide=True, warn=True)
        return 'exists' in result.stdout
    except:
        return False

def safe_get_file(conn, remote_path, local_path, timeout=30):
    """Download file with timeout, existence check, and progress percentage"""
    # First check if file exists
    Print.info(f'    Checking if file exists...')
    sys.stdout.flush()
    if not check_file_exists(conn, remote_path):
        raise FileNotFoundError(f'File not found: {remote_path}')

    # Get file size for progress indication
    file_size = 0
    try:
        result = conn.run(f'stat -c%s {remote_path} 2>/dev/null || echo "0"', hide=True, warn=True)
        file_size = int(result.stdout.strip() or '0')
        if file_size > 0:
            size_mb = file_size / (1024 * 1024)
            Print.info(f'    File size: {size_mb:.2f} MB')
        else:
            Print.info(f'    File size: unknown')
        sys.stdout.flush()
    except Exception:
        pass

    # Download with timeout and progress
    Print.info(f'    Downloading (timeout: {timeout}s)...')
    sys.stdout.flush()

    # Track last progress so we can decide what to do on timeout
    progress_info = {"percent": 0, "done": 0, "total": 0}

    def _report_progress(transferred_bytes, total_bytes):
        # Prefer known file_size as ground truth for total size if available
        total = file_size or total_bytes or 1
        done = min(transferred_bytes, total)
        percent = min(100, max(0, int(done * 100 / total)))
        # Save last progress
        progress_info["percent"] = percent
        progress_info["done"] = done
        progress_info["total"] = total
        sys.stdout.write(f'\r    Progress: {percent:3d}% ({done}/{total} bytes)')
        sys.stdout.flush()

    sftp = None
    try:
        # Ensure local directory exists and has correct permissions
        local_path_obj = Path(local_path)
        local_path_obj.parent.mkdir(parents=True, exist_ok=True)
        # Fix permissions if parent directory is owned by root
        try:
            import os
            parent_stat = local_path_obj.parent.stat()
            if parent_stat.st_uid == 0:  # Owned by root
                os.chown(local_path_obj.parent, os.getuid(), os.getgid())
        except Exception:
            pass  # Ignore permission errors
        
        with timeout_context(timeout):
            sftp = conn.sftp()
            sftp.get(remote_path, str(local_path), callback=_report_progress)
        # Download completed successfully
        sys.stdout.write('\n')
        sys.stdout.flush()
        if sftp:
            try:
                sftp.close()
            except Exception:
                pass
        return True
    except (TimeoutError, Exception) as e:
        # Close SFTP connection if it exists
        if sftp:
            try:
                sftp.close()
            except Exception:
                pass
        
        sys.stdout.write('\n')
        sys.stdout.flush()
        
        # Check if file was actually downloaded completely
        file_complete = False
        if Path(local_path).exists():
            local_size = Path(local_path).stat().st_size
            # If local file size matches remote file size, consider it complete
            if file_size > 0 and local_size == file_size:
                file_complete = True
            # Or if progress reached 100% and file exists
            elif progress_info.get("percent", 0) >= 100:
                file_complete = True
        
        if file_complete:
            Print.warn(f'    ⚠ Download timed out but file appears complete ({Path(local_path).stat().st_size} bytes), treating as success')
            sys.stdout.flush()
            return True
        
        # File is incomplete, report error
        if isinstance(e, TimeoutError):
            Print.warn(f'    ⚠ Download timed out after {timeout}s')
        else:
            msg = str(e)[:100]
            Print.warn(f'    ⚠ Download failed: {msg}')
        sys.stdout.flush()
        raise

def collect_logs_via_node0(conn, repo_name, host_info, max_workers=1):
    """
    Collect logs from all nodes via node0
    
    Args:
        conn: Connection to node0
        repo_name: Repository name on remote nodes
        host_info: List of host info dicts
        max_workers: Maximum number of workers per node
    
    Returns:
        tuple: (dict mapping node index to list of collected log paths on node0, temp_dir)
    """
    Print.info('  Collecting logs from all nodes...')
    sys.stdout.flush()
    
    # Create temporary directory on node0 to store all logs
    temp_dir = f'/tmp/narwhal_logs_{int(time.time())}'
    conn.run(f'mkdir -p {temp_dir}', hide=True, warn=True)
    
    collected_logs = {}
    
    for i, host in enumerate(host_info):
        hostname = host['hostname']
        username = host.get('username', 'root')
        
        Print.info(f'    [{i+1}/{len(host_info)}] Collecting logs from node{i} ({hostname})...')
        sys.stdout.flush()
        
        node_logs = []
        
        # Create node directory on node0
        node_dir = f'{temp_dir}/node{i}'
        conn.run(f'mkdir -p {node_dir}', hide=True, warn=True)
        
        # Collect primary log
        remote_log = f'{repo_name}/{PathMaker.primary_log_file(i)}'
        local_log_on_node0 = f'{node_dir}/primary-{i}.log'
        
        Print.info(f'      Collecting primary-{i}.log...')
        sys.stdout.flush()
        
        # Use scp or rsync to copy from other node
        # If it's node0 itself, just copy directly
        if i == 0:
            result = conn.run(f'cp {remote_log} {local_log_on_node0} 2>/dev/null || echo "not_found"', 
                            hide=True, warn=True)
        else:
            # Copy from other node using hostname
            result = conn.run(
                f'scp -o StrictHostKeyChecking=no -o ConnectTimeout=5 '
                f'{username}@{hostname}:{remote_log} {local_log_on_node0} 2>&1 || echo "not_found"',
                hide=True, warn=True
            )
        
        if 'not_found' not in result.stdout and conn.run(f'test -f {local_log_on_node0}', hide=True, warn=True).ok:
            node_logs.append(local_log_on_node0)
            Print.info(f'      ✓ Collected primary-{i}.log')
        else:
            Print.warn(f'      ⚠ primary-{i}.log not found on node{i}')
        sys.stdout.flush()
        
        # Collect worker logs
        for j in range(max_workers):
            remote_log = f'{repo_name}/{PathMaker.worker_log_file(i, j)}'
            local_log_on_node0 = f'{node_dir}/worker-{i}-{j}.log'
            
            if i == 0:
                result = conn.run(f'cp {remote_log} {local_log_on_node0} 2>/dev/null || echo "not_found"', 
                                hide=True, warn=True)
            else:
                result = conn.run(
                    f'scp -o StrictHostKeyChecking=no -o ConnectTimeout=5 '
                    f'{username}@{hostname}:{remote_log} {local_log_on_node0} 2>&1 || echo "not_found"',
                    hide=True, warn=True
                )
            
            if 'not_found' not in result.stdout and conn.run(f'test -f {local_log_on_node0}', hide=True, warn=True).ok:
                node_logs.append(local_log_on_node0)
        
        # Collect client logs
        for j in range(max_workers):
            remote_log = f'{repo_name}/{PathMaker.client_log_file(i, j)}'
            local_log_on_node0 = f'{node_dir}/client-{i}-{j}.log'
            
            if i == 0:
                result = conn.run(f'cp {remote_log} {local_log_on_node0} 2>/dev/null || echo "not_found"', 
                                hide=True, warn=True)
            else:
                result = conn.run(
                    f'scp -o StrictHostKeyChecking=no -o ConnectTimeout=5 '
                    f'{username}@{hostname}:{remote_log} {local_log_on_node0} 2>&1 || echo "not_found"',
                    hide=True, warn=True
                )
            
            if 'not_found' not in result.stdout and conn.run(f'test -f {local_log_on_node0}', hide=True, warn=True).ok:
                node_logs.append(local_log_on_node0)
        
        collected_logs[i] = node_logs
    
    return collected_logs, temp_dir

def collect_primary_logs_via_node0(conn, repo_name, host_info, node_indices=None):
    """
    Collect primary logs from specified nodes via node0
    
    Args:
        conn: Connection to node0
        repo_name: Repository name on remote nodes
        host_info: List of host info dicts
        node_indices: List of node indices to download from (e.g., [0,1,2,3]). If None, downloads from all nodes.
    
    Returns:
        tuple: (dict mapping node index to list of collected log paths on node0, temp_dir)
    """
    Print.info('  Collecting primary logs from specified nodes...')
    sys.stdout.flush()
    
    # Create temporary directory on node0 to store all logs
    temp_dir = f'/tmp/narwhal_primary_logs_{int(time.time())}'
    conn.run(f'mkdir -p {temp_dir}', hide=True, warn=True)
    
    collected_logs = {}
    
    # If node_indices is None, collect from all nodes
    if node_indices is None:
        node_indices = list(range(len(host_info)))
    
    for i in node_indices:
        if i >= len(host_info):
            Print.warn(f'    ⚠ Node{i} index out of range, skipping...')
            sys.stdout.flush()
            continue
            
        host = host_info[i]
        hostname = host['hostname']
        username = host.get('username', 'root')
        
        Print.info(f'    [{node_indices.index(i)+1}/{len(node_indices)}] Collecting primary log from node{i} ({hostname})...')
        sys.stdout.flush()
        
        node_logs = []
        
        # Create node directory on node0
        node_dir = f'{temp_dir}/node{i}'
        conn.run(f'mkdir -p {node_dir}', hide=True, warn=True)
        
        # Collect primary log
        remote_log = f'{repo_name}/{PathMaker.primary_log_file(i)}'
        local_log_on_node0 = f'{node_dir}/primary-{i}.log'
        
        Print.info(f'      Collecting primary-{i}.log...')
        sys.stdout.flush()
        
        # Use scp or rsync to copy from other node
        # If it's node0 itself, just copy directly
        if i == 0:
            result = conn.run(f'cp {remote_log} {local_log_on_node0} 2>/dev/null || echo "not_found"', 
                            hide=True, warn=True)
        else:
            # Copy from other node using hostname
            result = conn.run(
                f'scp -o StrictHostKeyChecking=no -o ConnectTimeout=5 '
                f'{username}@{hostname}:{remote_log} {local_log_on_node0} 2>&1 || echo "not_found"',
                hide=True, warn=True
            )
        
        if 'not_found' not in result.stdout and conn.run(f'test -f {local_log_on_node0}', hide=True, warn=True).ok:
            node_logs.append(local_log_on_node0)
            Print.info(f'      ✓ Collected primary-{i}.log')
        else:
            Print.warn(f'      ⚠ primary-{i}.log not found on node{i}')
        sys.stdout.flush()
        
        collected_logs[i] = node_logs
    
    return collected_logs, temp_dir

def download_logs(settings_file='cloudlab_settings.json', max_workers=1):
    """Download logs from all CloudLab hosts directly from local machine"""
    
    # Load settings
    try:
        settings = CloudLabSettings.load(settings_file)
    except Exception as e:
        Print.error(f'Failed to load settings: {e}')
        return False
    
    # Create instance manager
    manager = CloudLabInstanceManager(settings)
    host_info = manager.get_host_info()
    repo_name = settings.repo_name
    
    if not host_info:
        Print.error('No hosts configured')
        return False
    
    Print.info(f'Downloading logs directly from {len(host_info)} nodes')
    Print.info(f'Repository name: {repo_name}')
    Print.info('=' * 60)
    sys.stdout.flush()
    
    conn_kwargs = get_connection_kwargs(settings.key_path)
    if not conn_kwargs:
        Print.error('Failed to load SSH key. Cannot proceed.')
        return False
    
    # Create local logs directory with proper permissions
    logs_dir = Path(PathMaker.logs_path())
    logs_dir.mkdir(parents=True, exist_ok=True)
    # Fix permissions if directory is owned by root
    try:
        import stat
        current_stat = logs_dir.stat()
        if current_stat.st_uid == 0:  # Owned by root
            import os
            os.chown(logs_dir, os.getuid(), os.getgid())
            Print.warn(f'  ⚠ Fixed logs directory ownership (was owned by root)')
    except Exception:
        pass  # Ignore permission errors
    
    # Download logs directly from each node
    try:
        Print.info('Downloading logs directly from all nodes...')
        sys.stdout.flush()
        
        for i, host in enumerate(host_info):
            hostname = host['hostname']
            username = host.get('username', 'root')
            port = host.get('port', 22)
            
            Print.info(f'  [{i+1}/{len(host_info)}] Downloading from node{i} ({hostname})...')
            sys.stdout.flush()
            
            # Helper function to download a single file with fresh connection
            def download_file_with_fresh_conn(remote_path, local_path, file_desc):
                """Download a file using a fresh connection"""
                conn = None
                try:
                    conn = Connection(hostname, user=username, port=port, 
                                     connect_kwargs=conn_kwargs, connect_timeout=30)
                    conn.open()
                    safe_get_file(conn, remote_path, str(local_path))
                    return True
                except FileNotFoundError:
                    return False  # File not found is OK
                except Exception as e:
                    Print.warn(f'    ⚠ Failed to download {file_desc}: {e}')
                    return False
                finally:
                    if conn:
                        try:
                            conn.close()
                        except Exception:
                            pass
            
            try:
                # Download primary log
                # PathMaker.primary_log_file() returns 'logs/primary-{i}.log'
                # logs_dir is already 'logs/', so we need to use just the filename
                remote_log = f'{repo_name}/{PathMaker.primary_log_file(i)}'
                log_filename = f'primary-{i}.log'
                local_log_path = logs_dir / log_filename
                
                Print.info(f'    Downloading primary-{i}.log...')
                sys.stdout.flush()
                
                if download_file_with_fresh_conn(remote_log, local_log_path, f'primary-{i}.log'):
                    Print.info(f'    ✓ Downloaded primary-{i}.log')
                else:
                    Print.warn(f'    ⚠ primary-{i}.log not found or failed to download')
                
                # Download worker logs
                for j in range(max_workers):
                    remote_log = f'{repo_name}/{PathMaker.worker_log_file(i, j)}'
                    log_filename = f'worker-{i}-{j}.log'
                    local_log_path = logs_dir / log_filename
                    
                    download_file_with_fresh_conn(remote_log, local_log_path, f'worker-{i}-{j}.log')
                
                # Download client logs
                for j in range(max_workers):
                    remote_log = f'{repo_name}/{PathMaker.client_log_file(i, j)}'
                    log_filename = f'client-{i}-{j}.log'
                    local_log_path = logs_dir / log_filename
                    
                    download_file_with_fresh_conn(remote_log, local_log_path, f'client-{i}-{j}.log')
                
            except Exception as e:
                Print.warn(f'  ⚠ Failed to connect to node{i} ({hostname}): {e}')
                continue
        
        Print.info('✓ Log download completed')
        return True
        
    except KeyboardInterrupt:
        Print.warn('\n✗ Interrupted by user')
        return False
    except Exception as e:
        error_msg = str(e)
        if len(error_msg) > 200:
            error_msg = error_msg[:200] + '...'
        Print.error(f'✗ Failed: {error_msg}')
        return False

def download_primary_logs(settings_file='cloudlab_settings.json', node_indices=None):
    """Download primary logs from specified CloudLab nodes directly from local machine"""
    
    # Load settings
    try:
        settings = CloudLabSettings.load(settings_file)
    except Exception as e:
        Print.error(f'Failed to load settings: {e}')
        return False
    
    # Create instance manager
    manager = CloudLabInstanceManager(settings)
    host_info = manager.get_host_info()
    repo_name = settings.repo_name
    
    if not host_info:
        Print.error('No hosts configured')
        return False
    
    # If node_indices is None, download from all nodes
    if node_indices is None:
        node_indices = list(range(len(host_info)))
    
    Print.info(f'Downloading primary logs directly from {len(node_indices)} nodes')
    Print.info(f'Repository name: {repo_name}')
    Print.info(f'Node indices: {node_indices}')
    Print.info('=' * 60)
    sys.stdout.flush()
    
    conn_kwargs = get_connection_kwargs(settings.key_path)
    if not conn_kwargs:
        Print.error('Failed to load SSH key. Cannot proceed.')
        return False
    
    # Create local logs directory with proper permissions
    logs_dir = Path(PathMaker.logs_path())
    logs_dir.mkdir(parents=True, exist_ok=True)
    # Fix permissions if directory is owned by root
    try:
        import stat
        current_stat = logs_dir.stat()
        if current_stat.st_uid == 0:  # Owned by root
            import os
            os.chown(logs_dir, os.getuid(), os.getgid())
            Print.warn(f'  ⚠ Fixed logs directory ownership (was owned by root)')
    except Exception:
        pass  # Ignore permission errors
    
    # Download logs directly from each specified node
    try:
        Print.info('Downloading primary logs directly from all nodes...')
        sys.stdout.flush()
        
        success_count = 0
        fail_count = 0
        
        for idx, i in enumerate(node_indices):
            if i >= len(host_info):
                Print.warn(f'  ⚠ Node{i} index out of range, skipping...')
                continue
            
            host = host_info[i]
            hostname = host['hostname']
            username = host.get('username', 'root')
            port = host.get('port', 22)
            
            Print.info(f'  [{idx+1}/{len(node_indices)}] Downloading from node{i} ({hostname})...')
            sys.stdout.flush()
            
            # Helper function to download a single file with fresh connection
            def download_file_with_fresh_conn(remote_path, local_path, file_desc):
                """Download a file using a fresh connection"""
                conn = None
                try:
                    conn = Connection(hostname, user=username, port=port, 
                                     connect_kwargs=conn_kwargs, connect_timeout=30)
                    conn.open()
                    safe_get_file(conn, remote_path, str(local_path))
                    return True
                except FileNotFoundError:
                    return False  # File not found
                except Exception as e:
                    Print.warn(f'    ⚠ Failed to download {file_desc}: {e}')
                    return False
                finally:
                    if conn:
                        try:
                            conn.close()
                        except Exception:
                            pass
            
            try:
                # Download primary log
                # PathMaker.primary_log_file() returns 'logs/primary-{i}.log'
                # logs_dir is already 'logs/', so we need to use just the filename
                remote_log = f'{repo_name}/{PathMaker.primary_log_file(i)}'
                log_filename = f'primary-{i}.log'
                local_log_path = logs_dir / log_filename
                
                Print.info(f'    Downloading primary-{i}.log...')
                sys.stdout.flush()
                
                if download_file_with_fresh_conn(remote_log, local_log_path, f'primary-{i}.log'):
                    Print.info(f'    ✓ Downloaded primary-{i}.log')
                    success_count += 1
                else:
                    Print.warn(f'    ⚠ primary-{i}.log not found or failed to download')
                    fail_count += 1
                
            except Exception as e:
                Print.warn(f'  ⚠ Failed to connect to node{i} ({hostname}): {e}')
                fail_count += 1
                continue
        
        Print.info('=' * 60)
        Print.info(f'Download complete: {success_count} succeeded, {fail_count} failed')
        Print.info(f'Logs saved to: {logs_dir.absolute()}')
        sys.stdout.flush()
        
        return fail_count == 0
        
    except KeyboardInterrupt:
        Print.warn('\n✗ Interrupted by user')
        return False
    except Exception as e:
        error_msg = str(e)
        if len(error_msg) > 200:
            error_msg = error_msg[:200] + '...'
        Print.error(f'✗ Failed: {error_msg}')
        return False

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Download logs from CloudLab remote nodes via node0')
    parser.add_argument('--settings', default='cloudlab_settings.json', 
                       help='Path to CloudLab settings file')
    parser.add_argument('--max-workers', type=int, default=1,
                       help='Maximum number of workers per node (default: 1)')
    parser.add_argument('--primary-only', action='store_true',
                       help='Download only primary logs')
    parser.add_argument('--nodes', type=str, default=None,
                       help='Comma-separated list of node indices to download from (e.g., "0,1,2,3")')
    
    args = parser.parse_args()
    
    if args.primary_only:
        node_indices = None
        if args.nodes:
            try:
                node_indices = [int(x.strip()) for x in args.nodes.split(',')]
            except ValueError:
                Print.error(f'Invalid node indices: {args.nodes}')
                sys.exit(1)
        success = download_primary_logs(args.settings, node_indices)
    else:
        success = download_logs(args.settings, args.max_workers)
    sys.exit(0 if success else 1)