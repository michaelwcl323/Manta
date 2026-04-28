import csv
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

def extract_e2e_tps_latency(summary_csv='script/tps_latency_summary.csv'):
    e2e_tps = []
    latency = []
    attack = []
    rate = []
    with open(summary_csv, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            # Each run's TPS
            tps_keys = [k for k in row if k.startswith('E2E_TPS_Run')]
            lat_keys = [k for k in row if k.startswith('E2E_Lat_Run')]
            # Extract avg columns (could also use min/max/median columns)
            if 'E2E_TPS_Avg' in row and 'E2E_Lat_Avg' in row:
                e2e_tps.append(float(row['E2E_TPS_Avg']))
                latency.append(float(row['E2E_Lat_Avg']))
            else:
                # Fallback: average the runs
                tps_vals = [float(row[k]) for k in tps_keys]
                lat_vals = [float(row[k]) for k in lat_keys]
                e2e_tps.append(np.mean(tps_vals))
                latency.append(np.mean(lat_vals))
            attack.append(row['Attack'])
            rate.append(int(row['Rate']))
    return {
        'rate': rate,
        'attack': attack,
        'e2e_tps': e2e_tps,
        'latency': latency,
    }

if __name__ == '__main__':
    data = extract_e2e_tps_latency()
    print("Rates:", data['rate'])
    print("Attack (True/False):", data['attack'])
    print("E2E TPS averaged:", data['e2e_tps'])
    print("Latency averaged:", data['latency'])

    attack_data = {
        'rate': [],
        'e2e_tps': [],
        'latency': [],
    }
    non_attack_data = {
        'rate': [],
        'e2e_tps': [],
        'latency': [],
    }
    for r, a, tps, lat in zip(data['rate'], data['attack'], data['e2e_tps'], data['latency']):
        if str(a).lower() == 'true':
            attack_data['rate'].append(r)
            attack_data['e2e_tps'].append(tps)
            attack_data['latency'].append(lat)
        else:
            non_attack_data['rate'].append(r)
            non_attack_data['e2e_tps'].append(tps)
            non_attack_data['latency'].append(lat)
    print("Attack data:", attack_data)
    print("Non-attack data:", non_attack_data)

    plt.figure(figsize=(8,6))
    plt.scatter(attack_data['e2e_tps'], attack_data['latency'], color='red', label='Attack', marker='x')
    plt.scatter(non_attack_data['e2e_tps'], non_attack_data['latency'], color='blue', label='No Attack', marker='o')

    plt.xlabel('E2E TPS (throughput)')
    plt.ylabel('E2E Latency (ms)')
    plt.title('E2E Latency vs TPS (Attack vs Non-Attack)')
    plt.legend()
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()

    workspace_path = Path(__file__).parent.parent
    output_dir = workspace_path / 'script' / 'plots'
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / 'tps_latency_simple.png'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f'Figure saved to: {output_file}')