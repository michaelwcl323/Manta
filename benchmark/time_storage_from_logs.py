#!/usr/bin/env python3
"""
Parse Narwhal primary node logs and extract round progression and certificate information.
Exports data to CSV format for analysis.
"""

import re
import csv
from datetime import datetime
from collections import defaultdict
from pathlib import Path

from benchmark.origin_mapping import build_origin_mapping_from_files, resolve_origin


def parse_timestamp(timestamp_str):
    """Convert ISO format timestamp to POSIX timestamp.
    
    Args:
        timestamp_str: ISO format timestamp string (e.g., "2025-12-19T09:24:08.261Z")
    
    Returns:
        POSIX timestamp (float) or None if parsing fails
    """
    try:
        timestamp_str = timestamp_str.strip('[]')
        dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        return datetime.timestamp(dt)
    except Exception as e:
        print(f"Error parsing timestamp {timestamp_str}: {e}")
        return None


def load_client_start_time(logs_dir='logs'):
    """Return the earliest client "Start sending transactions" timestamp."""
    pattern = re.compile(r'\[(.*?Z)\s+.*?\]\s+.*?Start sending transactions')
    start_times = []

    for log_path in sorted(Path(logs_dir).glob('client-*.log')):
        try:
            with log_path.open('r') as handle:
                for line in handle:
                    match = pattern.search(line)
                    if match:
                        posix = parse_timestamp(match.group(1))
                        if posix is not None:
                            start_times.append(posix)
                        break
        except FileNotFoundError:
            continue

    return min(start_times) if start_times else None


def parse_log_file(log_file_path):
    """Parse log file and extract round advancement and certificate events.
    
    Args:
        log_file_path: Path to the log file
    
    Returns:
        tuple: (round_time_info, certificate_info) - lists of log lines
    """
    round_time_info = []
    certificate_info = []
    
    try:
        with open(log_file_path, 'r') as f:
            lines = f.readlines()
        
        for line in lines:
            if 'Received certificate' in line:
                certificate_info.append(line)
            if 'Dag starting at round' in line or 'Dag moved to round' in line:
                round_time_info.append(line)
        
        return round_time_info, certificate_info
    except FileNotFoundError:
        print(f"Warning: Log file not found: {log_file_path}")
        return [], []


def extract_round_info(round_time_info, node_id):
    """Extract round information from round advancement log lines.
    
    Args:
        round_time_info: List of round advancement log lines
        node_id: Node ID
    
    Returns:
        list: List of round_info dictionaries
    """
    round_info = []
    pattern = r'\[(.*?Z)\s+.*?\]\s+.*?(?:Dag starting at round|Dag moved to round)\s+(\d+)'
    
    for info in round_time_info:
        match = re.search(pattern, info)
        if match:
            timestamp_str = match.group(1)
            round_number = int(match.group(2))
            round_info.append({
                'node_id': node_id,
                'round_number': round_number,
                'time_stamp': timestamp_str,
                'round_end_time': '',  # Will be set later
                'certificates': []
            })
    
    return round_info


def filter_round_info_by_client_start(round_info, client_start_posix):
    """Drop warmup rounds whose start time is before client traffic begins."""
    if client_start_posix is None:
        return round_info

    filtered = []
    for item in round_info:
        round_start_posix = parse_timestamp(item.get('time_stamp', ''))
        if round_start_posix is None or round_start_posix < client_start_posix:
            continue
        filtered.append(item)
    return filtered


def calculate_round_end_times(round_info):
    """Calculate round end time for each round as relative time (ms) from round start.
    
    Args:
        round_info: List of round_info dictionaries (modified in place)
    """
    # Sort by round number
    sorted_rounds = sorted(round_info, key=lambda x: x['round_number'])
    
    # For each round, calculate relative end time (time difference from round start)
    for i in range(len(sorted_rounds) - 1):
        current_round = sorted_rounds[i]
        next_round = sorted_rounds[i + 1]
        
        # Calculate time difference in milliseconds
        round_start_posix = parse_timestamp(current_round['time_stamp'])
        round_end_posix = parse_timestamp(next_round['time_stamp'])
        
        if round_start_posix is not None and round_end_posix is not None:
            time_diff_ms = (round_end_posix - round_start_posix) * 1000
            current_round['round_end_time'] = max(time_diff_ms, 0)
        else:
            current_round['round_end_time'] = ''
    
    # Last round has no end time (or we could leave it empty)
    if sorted_rounds:
        sorted_rounds[-1]['round_end_time'] = ''


def create_round_dict(round_info):
    """Create a dictionary mapping round_number to round_info index.
    
    Args:
        round_info: List of round_info dictionaries
    
    Returns:
        dict: Mapping of round_number -> index in round_info list
    """
    round_info_dict = {}
    for idx, item in enumerate(round_info):
        try:
            round_num = int(item['round_number'])
            round_info_dict[round_num] = idx
        except (ValueError, KeyError):
            continue
    return round_info_dict


def calculate_time_delta(cert_timestamp_str, round_start_timestamp_str):
    """Calculate time difference between certificate and round start.
    
    Args:
        cert_timestamp_str: Certificate timestamp string
        round_start_timestamp_str: Round start timestamp string
    
    Returns:
        float: Time difference in milliseconds, or 0 if calculation fails
    """
    cert_posix = parse_timestamp(cert_timestamp_str)
    round_start_posix = parse_timestamp(round_start_timestamp_str)
    
    if cert_posix is not None and round_start_posix is not None:
        time_diff_seconds = cert_posix - round_start_posix
        return max(time_diff_seconds, 0) * 1000
    return 0


def process_certificates(certificate_info, round_info, round_info_dict):
    """Process certificate log lines and add them to round_info.
    
    Args:
        certificate_info: List of certificate log lines
        round_info: List of round_info dictionaries (modified in place)
        round_info_dict: Dictionary mapping round_number to round_info index
    
    Returns:
        int: Number of successfully matched certificates
    """
    pattern = r'\[(.*?Z)\s+.*?\]\s+.*?Received certificate from network: round (\d+), origin: ([^\s,]+), digest:'
    matched_count = 0
    
    for certificate_line in certificate_info:
        match = re.search(pattern, certificate_line)
        if match:
            matched_count += 1
            ts_str = match.group(1)
            round_str = match.group(2)
            origin = match.group(3)
            round_num = int(round_str)
            
            # Find the corresponding round_info entry
            if round_num in round_info_dict:
                idx = round_info_dict[round_num]
                cert_timestamp = calculate_time_delta(ts_str, round_info[idx]['time_stamp'])
                
                new_cert = {
                    'timestamp': cert_timestamp,
                    'origin': origin
                }
                
                # Keep the earliest observation for each (round, origin).
                certs = round_info[idx]['certificates']
                found_duplicate = False
                for cert_idx, existing_cert in enumerate(certs):
                    if existing_cert.get('origin') == origin:
                        existing_timestamp = existing_cert.get('timestamp')
                        if (
                            existing_timestamp in ('', None)
                            or cert_timestamp < existing_timestamp
                        ):
                            certs[cert_idx] = new_cert
                        found_duplicate = True
                        break
                
                # If no duplicate found, append the new certificate
                if not found_duplicate:
                    certs.append(new_cert)
        else:
            # Debug: print first few non-matching lines
            if matched_count < 3:
                print(f"Warning: Could not parse certificate line: {certificate_line[:100]}...")
    
    return matched_count


def find_max_certificates(round_info):
    """Find the maximum number of certificates in any round.
    
    Args:
        round_info: List of round_info dictionaries
    
    Returns:
        int: Maximum number of certificates
    """
    max_certs = 0
    for round_item in round_info:
        max_certs = max(max_certs, len(round_item.get('certificates', [])))
    return max_certs


def format_timestamp(timestamp):
    """Format timestamp to 3 decimal places.
    
    Args:
        timestamp: Timestamp value (int, float, or string)
    
    Returns:
        str: Formatted timestamp string
    """
    if timestamp != '' and isinstance(timestamp, (int, float)):
        return f"{timestamp:.3f}"
    return str(timestamp) if timestamp else ''


def load_origin_mapping_if_available(
    committee_path='.committee.json',
    settings_path='cloudlab_settings.json',
):
    committee_file = Path(committee_path)
    settings_file = Path(settings_path)

    if not committee_file.exists() or not settings_file.exists():
        return None

    try:
        return build_origin_mapping_from_files(committee_file, settings_file)
    except Exception as e:
        print(f'Warning: Could not load origin mapping: {e}')
        return None


def export_to_csv(round_info, csv_filename, write_header=False, origin_mapping=None):
    """Export round_info to CSV file.
    
    Args:
        round_info: List of round_info dictionaries
        csv_filename: Output CSV file path
        write_header: Whether to write CSV header
    """
    max_certs = find_max_certificates(round_info)
    
    with open(csv_filename, 'a' if not write_header else 'w', newline='') as f:
        writer = csv.writer(f)
        
        # Write header if this is the first node
        if write_header:
            header = ['Node_ID', 'Round', 'Round_Start_Time', 'Round_End_Time_ms', 'Certificate_Count']
            for j in range(1, max_certs + 1):
                header.append(f'Certificate_{j}_Time_Delta_ms')
                header.append(f'Certificate_{j}_Origin')
                header.append(f'Certificate_{j}_Origin_Node_ID')
                header.append(f'Certificate_{j}_Origin_IP')
                header.append(f'Certificate_{j}_Origin_Region')
                header.append(f'Certificate_{j}_Origin_Full_Public_Key')
            writer.writerow(header)
        
        # Write data rows
        for round_item in sorted(round_info, key=lambda x: x['round_number']):
            node_id = round_item.get('node_id', '')
            round_num = round_item.get('round_number', '')
            round_start = round_item.get('time_stamp', '')
            round_end = round_item.get('round_end_time', '')
            cert_count = len(round_item.get('certificates', []))
            certs = round_item.get('certificates', [])
            
            # Build row
            row = [
                node_id,
                round_num,
                round_start if round_start else '',
                format_timestamp(round_end) if round_end != '' else '',
                cert_count
            ]
            
            # Add certificate data
            for j in range(max_certs):
                if j < len(certs):
                    cert = certs[j]
                    resolved = resolve_origin(cert.get('origin', ''), origin_mapping or [])
                    row.append(format_timestamp(cert.get('timestamp', '')))
                    row.append(cert.get('origin', ''))
                    row.append(resolved.get('node_id', ''))
                    row.append(resolved.get('ip', ''))
                    row.append(resolved.get('region', ''))
                    row.append(resolved.get('full_public_key', ''))
                else:
                    row.append('')
                    row.append('')
                    row.append('')
                    row.append('')
                    row.append('')
                    row.append('')
            
            writer.writerow(row)


def process_node_log(node_id, csv_filename, num_nodes, logs_dir='logs', origin_mapping=None):
    """Process a single node's log file.
    
    Args:
        node_id: Node ID
        csv_filename: Output CSV file path
        num_nodes: Total number of nodes (for header writing)
        logs_dir: Directory containing log files (default: 'logs')
    """
    log_file_path = f'{logs_dir}/primary-{node_id}.log'
    
    client_start_posix = load_client_start_time(logs_dir)

    # Parse log file
    round_time_info, certificate_info = parse_log_file(log_file_path)
    if not round_time_info and not certificate_info:
        print(f"Node {node_id}: No data found, skipping...")
        return
    
    # Extract round information
    round_info = extract_round_info(round_time_info, node_id)
    round_info = filter_round_info_by_client_start(round_info, client_start_posix)
    if not round_info:
        print(f"Node {node_id}: No rounds after client traffic start, skipping...")
        return
    
    # Calculate round end times (next round start time)
    calculate_round_end_times(round_info)
    
    # Create round dictionary for quick lookup
    round_info_dict = create_round_dict(round_info)
    
    # Process certificates
    print(f"Node {node_id}: Found {len(certificate_info)} certificate log lines")
    matched_count = process_certificates(certificate_info, round_info, round_info_dict)
    print(f"Node {node_id}: Successfully matched {matched_count} certificates")
    
    # Export to CSV
    write_header = (node_id == 0)
    export_to_csv(
        round_info,
        csv_filename,
        write_header,
        origin_mapping=origin_mapping,
    )
    print(f"Node {node_id}: CSV exported to {csv_filename}\n")


def export_round_end_pivot_table(csv_filename, output_filename):
    """Export a pivot table with rounds as rows, nodes as columns, and round end time (relative, ms) as values.
    
    Args:
        csv_filename: Input CSV file path (round_certificate_analysis.csv)
        output_filename: Output CSV file path for the pivot table
    """
    # Read the CSV file and extract round end time data
    round_node_end_time = defaultdict(dict)  # {round: {node_id: round_end_time_ms}}
    all_rounds = set()
    all_nodes = set()
    
    try:
        with open(csv_filename, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                node_id = row.get('Node_ID', '')
                round_num = row.get('Round', '')
                round_end_time = row.get('Round_End_Time_ms', '')
                
                if node_id != '' and round_num != '':
                    all_rounds.add(int(round_num))
                    all_nodes.add(int(node_id))
                    # Store the round end time (relative time in ms), or empty string if not available
                    round_node_end_time[int(round_num)][int(node_id)] = round_end_time if round_end_time else ''
        
        # Sort rounds and nodes
        sorted_rounds = sorted(all_rounds)
        sorted_nodes = sorted(all_nodes)
        
        # Write the pivot table
        with open(output_filename, 'w', newline='') as f:
            writer = csv.writer(f)
            
            # Write header: Round, Node_0, Node_1, ..., Node_N
            header = ['Round'] + [f'Node_{node_id}' for node_id in sorted_nodes]
            writer.writerow(header)
            
            # Write data rows
            for round_num in sorted_rounds:
                row = [round_num]
                for node_id in sorted_nodes:
                    round_end = round_node_end_time[round_num].get(node_id, '')
                    row.append(round_end)
                writer.writerow(row)
        
        print(f"Round end time pivot table exported to: {output_filename}")
        
    except FileNotFoundError:
        print(f"Warning: Could not find input file {csv_filename}")
    except Exception as e:
        print(f"Error generating pivot table: {e}")


def main():
    """Main function to process all node logs."""
    num_nodes = 10
    csv_filename = 'round_certificate_analysis.csv'
    pivot_filename = 'round_end_time_pivot.csv'
    logs_dir = 'logs'
    origin_mapping = load_origin_mapping_if_available()
    
    print("=" * 80)
    print("Narwhal Log Analysis - Round and Certificate Extraction")
    print("=" * 80)
    print(f"Processing {num_nodes} nodes...\n")
    
    for node_id in range(num_nodes):
        process_node_log(
            node_id,
            csv_filename,
            num_nodes,
            logs_dir=logs_dir,
            origin_mapping=origin_mapping,
        )
    
    print("=" * 80)
    print(f"Analysis complete! Results saved to: {csv_filename}")
    
    # Generate round end time pivot table
    print("\nGenerating round end time pivot table...")
    export_round_end_pivot_table(csv_filename, pivot_filename)
    
    print("=" * 80)


if __name__ == '__main__':
    main()
