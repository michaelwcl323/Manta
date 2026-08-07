#!/usr/bin/env python3
"""
Remote Server Setup Script

This script connects to multiple remote servers via SSH and executes
environmental setup commands on them.

Usage:
    python script/remote_setup.py [--config config.json] [--dry-run]
    
Configuration file format (JSON):
{
    "ssh_key_path": "/path/to/private/key",
    "servers": [
        {
            "hostname": "server1.example.com",
            "username": "ubuntu",
            "port": 22,
            "description": "Primary server"
        },
        {
            "hostname": "server2.example.com",
            "username": "root",
            "port": 2222,
            "description": "Secondary server"
        }
    ],
    "setup_commands": [
        "sudo apt-get update",
        "sudo apt-get -y upgrade",
        "sudo apt-get -y install build-essential",
        "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y",
        "source $HOME/.cargo/env",
        "rustup default stable"
    ]
}
"""

import json
import sys
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional
from fabric import Connection
from paramiko import RSAKey
from paramiko.ssh_exception import PasswordRequiredException, SSHException
from concurrent.futures import ThreadPoolExecutor, as_completed
import time


class RemoteSetupError(Exception):
    """Custom exception for remote setup errors"""
    pass


class RemoteServerManager:
    """Manages connections and commands for multiple remote servers"""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the remote server manager
        
        Args:
            config: Configuration dictionary with ssh_key_path, servers, and setup_commands
        """
        self.config = config
        self.ssh_key_path = Path(config['ssh_key_path'])
        self.servers = config['servers']
        self.setup_commands = config.get('setup_commands', [])
        
        # Load SSH key
        try:
            if not self.ssh_key_path.exists():
                raise RemoteSetupError(f"SSH key not found: {self.ssh_key_path}")
            
            # Try to load key without password first
            try:
                pkey = RSAKey.from_private_key_file(str(self.ssh_key_path))
            except PasswordRequiredException:
                # Key is password-protected, try to get password
                import os
                password = os.environ.get('SSH_KEY_PASSWORD')
                
                # Try to get password from config if not in environment
                if not password:
                    password = config.get('ssh_key_password')
                
                if password:
                    pkey = RSAKey.from_private_key_file(str(self.ssh_key_path), password=password)
                else:
                    raise RemoteSetupError(
                        'SSH key is password-protected. Please provide password via SSH_KEY_PASSWORD environment variable or ssh_key_password in config file'
                    )
            
            self.connect_kwargs = {
                'pkey': pkey
            }
        except (IOError, PasswordRequiredException, SSHException) as e:
            raise RemoteSetupError(f'Failed to load SSH key: {e}')
    
    def test_connection(self, server: Dict[str, Any]) -> bool:
        """
        Test connection to a single server
        
        Args:
            server: Server configuration dictionary
            
        Returns:
            True if connection successful, False otherwise
        """
        hostname = server['hostname']
        username = server.get('username', 'ubuntu')
        port = server.get('port', 22)
        connect_timeout = server.get('connect_timeout', 30)
        
        try:
            conn = Connection(
                hostname,
                user=username,
                port=port,
                connect_kwargs=self.connect_kwargs,
                connect_timeout=connect_timeout
            )
            conn.open()
            conn.close()
            return True
        except Exception as e:
            print(f"  ✗ Connection failed to {username}@{hostname}:{port} - {e}")
            return False
    
    def test_all_connections(self) -> Dict[str, bool]:
        """
        Test connections to all servers
        
        Returns:
            Dictionary mapping hostname to connection status
        """
        print("Testing connections to all servers...")
        results = {}
        
        for server in self.servers:
            hostname = server['hostname']
            description = server.get('description', '')
            print(f"  Testing {hostname} ({description})...", end=' ')
            
            if self.test_connection(server):
                print("✓ Connected")
                results[hostname] = True
            else:
                print("✗ Failed")
                results[hostname] = False
        
        return results
    
    def execute_command(self, server: Dict[str, Any], command: str, 
                       hide_output: bool = False) -> Dict[str, Any]:
        """
        Execute a single command on a server
        Creates a separate socket connection for each command
        
        Args:
            server: Server configuration dictionary
            command: Command to execute
            hide_output: Whether to hide command output
            
        Returns:
            Dictionary with execution results
        """
        hostname = server['hostname']
        username = server.get('username', 'ubuntu')
        port = server.get('port', 22)
        connect_timeout = server.get('connect_timeout', 30)
        
        conn = None
        try:
            # Create a new connection (separate socket) for each command
            conn = Connection(
                hostname,
                user=username,
                port=port,
                connect_kwargs=self.connect_kwargs,
                connect_timeout=connect_timeout
            )
            
            result = conn.run(command, hide=hide_output, warn=True)
            
            return {
                'success': result.ok,
                'hostname': hostname,
                'command': command,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'return_code': result.return_code
            }
        except Exception as e:
            return {
                'success': False,
                'hostname': hostname,
                'command': command,
                'error': str(e)
            }
        finally:
            # Explicitly close the connection to free the socket
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
    
    def execute_commands_parallel(self, commands: List[str], 
                                  hide_output: bool = False) -> List[Dict[str, Any]]:
        """
        Execute commands on all servers in parallel
        Each server and command uses a separate socket connection
        
        Args:
            commands: List of commands to execute
            hide_output: Whether to hide command output
            
        Returns:
            List of execution results for each server and command
        """
        results = []
        
        # Create tasks: (server, command) pairs
        tasks = []
        for server in self.servers:
            for cmd in commands:
                tasks.append((server, cmd))
        
        print(f"\n  Executing {len(tasks)} tasks in parallel (each with separate connection)...")
        
        # Execute all tasks in parallel using ThreadPoolExecutor
        # Each task creates its own Connection (socket)
        def execute_single_task(task):
            server, cmd = task
            hostname = server['hostname']
            username = server.get('username', 'ubuntu')
            port = server.get('port', 22)
            connect_timeout = server.get('connect_timeout', 30)
            
            try:
                # Create a new connection for each task (separate socket)
                conn = Connection(
                    hostname,
                    user=username,
                    port=port,
                    connect_kwargs=self.connect_kwargs,
                    connect_timeout=connect_timeout
                )
                
                result = conn.run(cmd, hide=hide_output, warn=True)
                conn.close()  # Close the connection after use
                
                return {
                    'success': result.ok,
                    'hostname': hostname,
                    'command': cmd,
                    'stdout': result.stdout,
                    'stderr': result.stderr,
                    'return_code': result.return_code
                }
            except Exception as e:
                return {
                    'success': False,
                    'hostname': hostname,
                    'command': cmd,
                    'error': str(e)
                }
        
        # Execute all tasks in parallel
        with ThreadPoolExecutor(max_workers=min(len(tasks), 20)) as executor:
            future_to_task = {executor.submit(execute_single_task, task): task for task in tasks}
            
            completed = 0
            for future in as_completed(future_to_task):
                completed += 1
                result = future.result()
                results.append(result)
                
                if not hide_output:
                    status = "✓" if result['success'] else "✗"
                    print(f"  [{completed}/{len(tasks)}] {status} {result['hostname']}: {result['command'][:50]}...")
        
        return results
    
    def execute_commands_sequential(self, commands: List[str],
                                   hide_output: bool = False) -> List[Dict[str, Any]]:
        """
        Execute commands on all servers sequentially (one server at a time)
        
        Args:
            commands: List of commands to execute
            hide_output: Whether to hide command output
            
        Returns:
            List of execution results for each server and command
        """
        results = []
        
        for server in self.servers:
            hostname = server['hostname']
            description = server.get('description', '')
            print(f"\n{'='*80}")
            print(f"Server: {hostname} ({description})")
            print(f"{'='*80}")
            
            # Test connection first
            if not self.test_connection(server):
                print(f"  ⚠ Skipping {hostname} due to connection failure")
                continue
            
            # Execute each command
            for cmd in commands:
                print(f"\n  Executing: {cmd}")
                result = self.execute_command(server, cmd, hide_output=hide_output)
                results.append(result)
                
                if result['success']:
                    print(f"  ✓ Success")
                    if not hide_output and result.get('stdout'):
                        print(f"  Output: {result['stdout'][:200]}...")
                else:
                    print(f"  ✗ Failed")
                    if result.get('stderr'):
                        print(f"  Error: {result['stderr']}")
                    elif result.get('error'):
                        print(f"  Error: {result['error']}")
        
        return results
    
    def run_setup(self, parallel: bool = True, hide_output: bool = False,
                  dry_run: bool = False) -> List[Dict[str, Any]]:
        """
        Run setup commands on all servers
        
        Args:
            parallel: Whether to execute commands in parallel across servers
            hide_output: Whether to hide command output
            dry_run: If True, only print commands without executing
            
        Returns:
            List of execution results
        """
        if not self.setup_commands:
            print("⚠ No setup commands specified in configuration")
            return []
        
        print(f"\n{'='*80}")
        print(f"Running Setup Commands ({'DRY RUN' if dry_run else 'EXECUTING'})")
        print(f"{'='*80}")
        print(f"Servers: {len(self.servers)}")
        print(f"Commands: {len(self.setup_commands)}")
        print(f"Mode: {'Parallel' if parallel else 'Sequential'}")
        
        if dry_run:
            print("\nCommands that would be executed:")
            for i, cmd in enumerate(self.setup_commands, 1):
                print(f"  {i}. {cmd}")
            print("\nServers:")
            for server in self.servers:
                print(f"  - {server['hostname']} ({server.get('description', 'N/A')})")
            return []
        
        # Test all connections first
        connection_results = self.test_all_connections()
        failed_connections = [h for h, status in connection_results.items() if not status]
        
        if failed_connections:
            print(f"\n⚠ Warning: {len(failed_connections)} server(s) failed connection test:")
            for hostname in failed_connections:
                print(f"  - {hostname}")
            response = input("\nContinue anyway? (y/N): ")
            if response.lower() != 'y':
                print("Aborted.")
                return []
        
        # Execute commands
        if parallel:
            return self.execute_commands_parallel(self.setup_commands, hide_output)
        else:
            return self.execute_commands_sequential(self.setup_commands, hide_output)


def load_config(config_path: Path) -> Dict[str, Any]:
    """
    Load configuration from JSON file
    
    Args:
        config_path: Path to configuration file
        
    Returns:
        Configuration dictionary
    """
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        # Validate required fields
        required_fields = ['ssh_key_path', 'servers']
        for field in required_fields:
            if field not in config:
                raise RemoteSetupError(f"Missing required field in config: {field}")
        
        if not isinstance(config['servers'], list) or len(config['servers']) == 0:
            raise RemoteSetupError("'servers' must be a non-empty list")
        
        # Validate each server has required fields
        for i, server in enumerate(config['servers']):
            if 'hostname' not in server:
                raise RemoteSetupError(f"Server {i} missing 'hostname' field")
        
        return config
    except json.JSONDecodeError as e:
        raise RemoteSetupError(f"Invalid JSON in config file: {e}")
    except IOError as e:
        raise RemoteSetupError(f"Failed to read config file: {e}")


def print_summary(results: List[Dict[str, Any]]):
    """Print summary of execution results"""
    if not results:
        return
    
    print(f"\n{'='*80}")
    print("Execution Summary")
    print(f"{'='*80}")
    
    successful = sum(1 for r in results if r.get('success', False))
    failed = len(results) - successful
    
    print(f"Total commands executed: {len(results)}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    
    if failed > 0:
        print("\nFailed executions:")
        for result in results:
            if not result.get('success', False):
                print(f"  ✗ {result['hostname']}: {result.get('command', 'N/A')}")
                if result.get('error'):
                    print(f"    Error: {result['error']}")
                elif result.get('stderr'):
                    print(f"    Stderr: {result['stderr'][:200]}")


def main():
    parser = argparse.ArgumentParser(
        description='Remote server setup and environmental configuration tool'
    )
    parser.add_argument(
        '--config',
        type=str,
        default='script/remote_control/remote_setup_config.json',
        help='Path to configuration JSON file (default: script/remote_setup_config.json)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print commands without executing them'
    )
    parser.add_argument(
        '--sequential',
        action='store_true',
        help='Execute commands sequentially (one server at a time) instead of in parallel'
    )
    parser.add_argument(
        '--hide-output',
        action='store_true',
        help='Hide command output (only show success/failure)'
    )
    parser.add_argument(
        '--test-only',
        action='store_true',
        help='Only test connections, do not execute setup commands'
    )
    
    args = parser.parse_args()
    
    try:
        # Load configuration
        config_path = Path(args.config)
        if not config_path.exists():
            print(f"Error: Configuration file not found: {config_path}")
            print("\nExample configuration file:")
            print("""
{
    "ssh_key_path": "/path/to/private/key",
    "servers": [
        {
            "hostname": "server1.example.com",
            "username": "ubuntu",
            "port": 22,
            "description": "Primary server"
        }
    ],
    "setup_commands": [
        "sudo apt-get update",
        "sudo apt-get -y upgrade"
    ]
}
""")
            sys.exit(1)
        
        config = load_config(config_path)
        
        # Create manager
        manager = RemoteServerManager(config)
        
        # Test connections
        if args.test_only:
            manager.test_all_connections()
        else:
            # Run setup
            results = manager.run_setup(
                parallel=not args.sequential,
                hide_output=args.hide_output,
                dry_run=args.dry_run
            )
            
            # Print summary
            if not args.dry_run:
                print_summary(results)
        
        print("\n✓ Done!")
        
    except RemoteSetupError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

