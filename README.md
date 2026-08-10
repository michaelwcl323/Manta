# MANTA: Unlocking DAG Flexibility in Asynchronous BFT -- NSDI'27 Artifact

## 1. Introduction

This repository contains the artifact for the paper:

> **MANTA: Unlocking DAG Flexibility in Asynchronous BFT**

The artifact includes the Manta, Tusk, DAG-Rider, Mahi-Mahi, and Chitu baselines used in the paper.

Each implementation is maintained in a separate Git branch. This `artifact-evaluation` branch contains the evaluation instructions, experiment configurations, orchestration scripts, and paper results.

## 2. Getting Started and Functional Validation

### 2.1 Clone the Artifact

Please follow the commands below and ensure that the current branch is `artifact-evaluation`.

```Bash
git clone https://github.com/michaelwcl323/manta-nsdi27.git
cd manta-nsdi27
git switch artifact-evaluation
```

### 2.2 Prerequisites

Please install the experiment environment using the following command on Linux. 

```Bash
./scripts/environment_setup.sh
```

### 2.3 Run the Functional Test

The functional test is a lightweight local execution that verifies whether the artifact environment is correctly configured and whether the implementation of each branch can run successfully. 

```bash
python test_functional_test.py --all
```

It takes about 10-15 minutes to finish functional testing. 

### 2.4 Expected Output

The test passes if it ends with:

```text
[functional-test] manta: RUNNING
[functional-test] manta: PASS
[functional-test] tusk: RUNNING
[functional-test] tusk: PASS
[functional-test] mahi-mahi: RUNNING
[functional-test] mahi-mahi: PASS
[functional-test] chitu: RUNNING
[functional-test] chitu: PASS

[functional-test] All selected functional tests passed. Logs: functional_test_results/<timestamp>
```

If you want to see the logs of functional tests, please find them in the folder `functional_test_results`.

## 3. Remote Environment Deployment

This section provisions the remote CloudLab APT cluster and deploys the artifact environment on all nodes.

CloudLab is used for the experiments in Figures 9, 10, 11(a), 11(c), and 12. Each experiment uses 10 replica nodes and one additional controller node. The controller coordinates remote deployment and experiment execution but does not participate in the consensus protocol.

The 50-replica experiment in Figure 11(b) is conducted on AWS because sufficient C6220 nodes may not be simultaneously available on CloudLab.

All CloudLab settings are specified in `cloudlab_settings.json`.

### 3.1 Configure Portal API Access

Before provisioning the cluster, follow the separate
[CloudLab configuration guide](cloudlab/README.md) to configure
`cloudlab_settings.json`, the Portal API token, SSH credentials, replica hosts,
machine type, and project/profile settings. Complete the configuration
validation in that guide before running the commands below.

### 3.2 Instantiate the Experiment

`start` regenerates the portal profile from `cloudlab_settings.json` and then instantiates the experiment. The profile provisions a shared experiment LAN so the replica `hosts` in `cloudlab_settings.json` can reach each other. If you change the profile, recreate the experiment with `terminate` then `start`.

```bash
python cloudlab/portal_experiment.py start
```

Check the experiment status:

```bash
python cloudlab/portal_experiment.py status
```

When the experiment is ready, download the manifests and write the allocated login hosts:

```bash
python cloudlab/portal_experiment.py manifests
```

This creates:

```text
build/manifest.xml
build/nodes
build/controller
build/head-node
```

`build/nodes` lists all allocated hosts. The **last** host is the controller (`build/controller`; `build/head-node` is the same value). The other hosts are replica nodes. For the full CloudLab experiment, `build/nodes` should contain 11 hosts.

### 3.3 Initialize the Nodes

Wait until all allocated nodes accept SSH:

```bash
python cloudlab/wait_experiment.py
```

Then wait until the profile startup script on every node finishes:

```bash
python cloudlab/wait_bootstrap.py
```

The bootstrap succeeds only if every node creates `/local/bootstrap.done`. If any node creates `/local/bootstrap.failed`, check `/local/bootstrap.log` on that node.

### 3.4 Deploy the Artifact

Deploy is driven by the controller (the last host in `build/nodes`). One command does both Getting Started and the CloudLab remote functional test:

```bash
python cloudlab/deploy_environment.py
```

The controller:

1. Runs Getting Started on every node (clone, check out `artifact-evaluation`, run `./scripts/environment_setup.sh`).
2. Runs a short `cloudlab_remote` functional test for `tusk` on the replica hosts from `cloudlab_settings.json` (10 nodes, 10 seconds). The controller does not participate as a consensus node.

Deploy succeeds if it ends with:

```text
[deploy] functional tusk: PASS
[deploy] CloudLab remote functional tests passed on all branches.
[deploy] done
```

After this command succeeds, the cluster is ready for the paper experiments below.

### 3.5 Terminate the Experiment

When all experiments are finished, terminate the CloudLab experiment to stop using the machines:

```bash
python cloudlab/portal_experiment.py terminate
```

## 4. Regenerating Figures from the Paper Data

### 4.1 Experiment 1

Plotting code for Experiment 1 lives in `paper_data/graph_generated_code/experiment1/`:

- `plot_workload_grouped_network_metrics_decouple_100k_consensus.py`
- `plot_workload_grouped_network_metrics_tusk_coupled_60k_consensus.py`

Paper data is under `paper_data/original_data/Figure9a/` and `paper_data/original_data/Figure9b/`
(flat run summary `*.txt` files named `{network}_{workload}_{timestamp}_n{nodes}_r{rate}_run{run}.txt`).
Regenerated figures go to `results/regenerate_graphs/`.

#### Figure 9(a)

Workload-grouped consensus latency and throughput for the **decoupled** architecture at $100000$ tx/s offered load, comparing networks (80ms, geo) within each workload (balanced, custom-high-3, custom-high-5). Reads summary `*.txt` files from `paper_data/original_data/Figure9a/`.

```bash
cd paper_data/graph_generated_code/experiment1
python3 plot_workload_grouped_network_metrics_decouple_100k_consensus.py
```

Output:

```text
results/regenerate_graphs/workload_grouped_network_metrics_decouple_100k_consensus.pdf
```

#### Figure 9(b)

Workload-grouped consensus latency and throughput for the **Tusk coupled** architecture at $60000$ tx/s offered load, using the same network/workload grouping as Figure 9(a). Reads summary `*.txt` files from `paper_data/original_data/Figure9b/`.

```bash
cd paper_data/graph_generated_code/experiment1
python3 plot_workload_grouped_network_metrics_tusk_coupled_60k_consensus.py
```

Output:

```text
results/regenerate_graphs/workload_grouped_network_metrics_tusk_coupled_60k_consensus.pdf
```

Each command succeeds if the corresponding PDF is created and the script prints the output path.

### 4.2 Experiment 2

Plotting code for Experiment 2 lives in `paper_data/graph_generated_code/experiment2/`. Shared helpers (do not run directly):

- `paper_figure_save.py` — figure-saving helper (tight layout / aspect ratio)
- `plot_latency_y_axis.py` — y-axis helper (linear / log latency scales)

Paper data is under `paper_data/original_data/`. Regenerated figures go to `results/regenerate_graphs/`.

#### Figure 10(a)

Mean consensus latency vs $\kappa$ for each $(\sigma, \mathrm{ref})$ series. Latencies are read from `paper_data/original_data/Figure10a_10b/consensus_summary.csv` and averaged over runs with the same $(\sigma, \kappa, \mathrm{ref})$.

```bash
cd paper_data/graph_generated_code/experiment2
python3 plot_latency_by_kappa_sigma_reference.py
```

Output:

```text
results/regenerate_graphs/latency_by_kappa_sigma_reference.pdf
```

#### Figure 10(b)

Mean consensus latency vs reference for $\kappa=2$, comparing $\sigma=1$ and $\sigma=2$. Same summary CSV and multi-run averaging as Figure 10(a).

```bash
cd paper_data/graph_generated_code/experiment2
python3 plot_reference_impact_kappa2_by_sigma.py
```

Output:

```text
results/regenerate_graphs/reference_impact_kappa2_by_sigma.pdf
```

#### Figure 10(c)

Attack-window latency timeseries overlay from `paper_data/original_data/Figure10c/`.

Main script: `plot_attack_latency_timeseries.py` (imports the shared helpers above).

```bash
cd paper_data/graph_generated_code/experiment2

python3 plot_attack_latency_timeseries.py \
  --merge-four-runs \
    ../../original_data/Figure10c/20260419_092353_560935_cloudlab-n10-r100000-run1-s1-k2-ref4-tag-experiment2_attack_final_latency.csv \
    ../../original_data/Figure10c/20260419_123518_715243_cloudlab-n10-r100000-run1-s1-k2-ref7-tag-experiment2_attack_final_latency.csv \
    ../../original_data/Figure10c/20260419_092900_079578_cloudlab-n10-r100000-run1-s1-k3-ref7-tag-experiment2_attack_final_latency.csv \
    ../../original_data/Figure10c/20260419_093137_848886_cloudlab-n10-r100000-run1-s1-k3-ref10-tag-experiment2_attack_final_latency.csv \
  --time-axis commit \
  --rolling-stat mean \
  --attack-start-secs 60 \
  --attack-end-secs 120 \
  --output ../../../results/regenerate_graphs/attack_latency_timeseries_overlay_mean.pdf
```

Output:

```text
results/regenerate_graphs/attack_latency_timeseries_overlay_mean.pdf
```

Each command succeeds if the corresponding PDF is created and the script prints the output path.

### 4.3 Experiment 3

Plotting code for Experiment 3 lives in `paper_data/graph_generated_code/experiment3/`:

- `Figure11a/plot_mean_tps_latency.py`
- `Figure11b/plot_mean_tps_latency.py`
- `Figure11c/plot_mean_tps_latency.py`

Paper data is under `paper_data/original_data/Figure11a/`, `paper_data/original_data/Figure11b/`, and `paper_data/original_data/Figure11c/`.
Regenerated figures go to `results/regenerate_graphs/`.

#### Figure 11(a)

Mean consensus throughput-latency curve for five protocols (Chitu, DAG-Rider, Mahi-mahi, Manta, Tusk), using aggregated CSV data from `paper_data/original_data/Figure11a/geo_consensus_tps_latency.csv`.

```bash
cd paper_data/graph_generated_code/experiment3/Figure11a
python3 plot_mean_tps_latency.py
```

Output:

```text
results/regenerate_graphs/631_consensus_tps_vs_consensus_latency.pdf
results/regenerate_graphs/legend.pdf
```

#### Figure 11(b)

50-node protocol comparison curve generated from summary `*.txt` files in `paper_data/original_data/Figure11b/` (the protocol folders directly contain the summaries).

```bash
cd paper_data/graph_generated_code/experiment3/Figure11b
python3 plot_mean_tps_latency.py
```

Output:

```text
results/regenerate_graphs/50nodes_protocol_comparison.pdf
```

#### Figure 11(c)

Faulty-setting protocol comparison curve generated from summary `*.txt` files in `paper_data/original_data/Figure11c/` (the protocol folders directly contain the summaries).

```bash
cd paper_data/graph_generated_code/experiment3/Figure11c
python3 plot_mean_tps_latency.py
```

Output:

```text
results/regenerate_graphs/faulty_graph.pdf
```

Each command succeeds if the corresponding PDF is created and the script prints the output path.

### 4.4 Experiment 4

Plotting / aggregation code for Experiment 4 lives in
`paper_data/graph_generated_code/experiment4/`.

#### Figure 12

Compare complete Manta against the no-flexible-coin ablation at input rates
$80000$, $100000$, and $120000$ tx/s.

- **complete** (default): Manta rows from
  `paper_data/original_data/Figure11a/geo_consensus_tps_latency.csv`
- **complete** (reproduction): pass `--complete-dir` with summary `*.txt` averages
- **no flexible coin**: averages of summary `*.txt` under `--noflexible-dir`
  (default `paper_data/original_data/Figure12/`)

```bash
# paper original data (Figure11a CSV + Figure12 txts)
python3 paper_data/graph_generated_code/experiment4/plot_manta_consensus_tps_latency.py

# after CloudLab reproduction
python3 paper_data/graph_generated_code/experiment4/plot_manta_consensus_tps_latency.py \
  --complete-dir experiment_reproduced/experiment4/results/Figure12/complete \
  --noflexible-dir experiment_reproduced/experiment4/results/Figure12/noflexible \
  --output-dir results/regenerate_graphs
```

Output:

```text
results/regenerate_graphs/manta_consensus_tps_latency_bar_csv_vs_without_80k_100k_120k.pdf
results/regenerate_graphs/manta_consensus_latency_only_no_legend.pdf
results/regenerate_graphs/manta_consensus_throughput_only_no_legend.pdf
results/regenerate_graphs/manta_consensus_legend_only.pdf
```

#### Table 2

```bash
python3 paper_data/graph_generated_code/experiment4/aggregate_table2_resources.py \
  --per-run-csv experiment_reproduced/experiment4/results/Table2/resource_summary.csv \
  --output-dir experiment_reproduced/experiment4/results/Table2
```

The command succeeds if the corresponding PDFs/CSVs are created and the script
prints the output paths.

## 5. Reproducing the Paper Results

### 5.1 Experiment Overview

| Experiment ID | Paper Result | Platform | Est. runtime |
|---|---|---|---|
| `figure9` | Figure 9 | CloudLab | ~2–3 hours |
| `figure10a` / `figure10b` / `figure10c` | Figure 10 | CloudLab | ~2.5–4 hours (one `run_figure10.py`) |
| `figure11a` | Figure 11(a) | CloudLab | ~6–8 hours |
| `figure11b` | Figure 11(b) | AWS | — |
| `figure11c` | Figure 11(c) | CloudLab | ~6–8 hours |
| `figure12` / `table2` | Figure 12 + Table 2 | CloudLab | ~1.5–2.5 hours (one `run_figure12.py`) |

### 5.2 E1: Impact of Heterogeneity — Figure 9

Controller-driven reproduction lives under `experiment_reproduced/experiment1/`.
It uses the `experiment1` branch (`coupled/` + `decoupled/`). This laptop only
SSHs to the **controller**; the controller drives all replica nodes and switches
scenarios (variant / network / workload).

#### Configuration

- Branch: `experiment1`
- Figure 9(a): `decoupled`, $100000$ tx/s, header/batch delay $200$ ms
- Figure 9(b): `coupled`, $60000$ tx/s, `max_header_batches=1`, delays $50$ ms
- Networks: `80ms`, `geo`
- Workloads: `balanced`, `custom-high-3`, `custom-high-5`

See `experiment_reproduced/experiment1/matrix.yaml` and `README.md`.

#### Command

```bash
# CloudLab up + Getting Started first (see Section 3)
python experiment_reproduced/experiment1/run_figure9.py
```

#### Output

Summaries are synced to (**`*.txt` plot inputs only**):

```text
experiment_reproduced/experiment1/results/Figure9a/
experiment_reproduced/experiment1/results/Figure9b/
```

`run_figure9.py` then **directly calls** the Figure 9 plot scripts (same as §4.1) and
writes PDFs/PNGs under `results/regenerate_graphs/`. Use `--skip-plot` to skip.

#### Expected Result

PDFs under `results/regenerate_graphs/` matching the paper Figure 9 workload/network grouping.

### 5.3 E2: Parameter Trade-offs — Figure 10

Controller-driven reproduction lives under `experiment_reproduced/experiment2/`.
It uses the `experiment2` branch (flat manta protocol at repo root). This laptop
only SSHs to the **controller**; the controller drives all replica nodes, applies
the geo WAN profile once, and sweeps parameter cells from `matrix.yaml`.

#### Configuration

- Branch: `experiment2`
- Network: `geo` (paper 6+3+1; see `experiment_reproduced/experiment2/wan/`)
- Figure 10(a)/(b): `σ∈{1,2}`, `κ∈{2,3,4}`, `ref∈{4,7,10}`, coverage fixed at 7, 3 runs, $100000$ tx/s (18 cells)
- Figure 10(c): certificate-limiting attack from $t=60$s; four `(κ, ref, coverage)` configs (4 cells)

See `experiment_reproduced/experiment2/matrix.yaml` and `README.md`.

#### Command

```bash
# CloudLab up + Getting Started first (see Section 3)
python experiment_reproduced/experiment2/run_figure10.py
# optional: --only-suite figure10a_10b | figure10c
```

#### Output

Plot inputs only are synced to:

```text
experiment_reproduced/experiment2/results/Figure10a_10b/consensus_summary.csv
experiment_reproduced/experiment2/results/Figure10c/{k2-ref4,k2-ref7,k3-ref7,k3-ref10}/latency.csv
```

`run_figure10.py` then **directly calls** the Figure 10 plot scripts (same as §4.2) and
writes PDFs/PNGs under `results/regenerate_graphs/`. Use `--skip-plot` to skip.

#### Expected Result

PDFs under `results/regenerate_graphs/` matching paper Figure 10(a)–(c)
(`latency_by_kappa_sigma_reference`, `reference_impact_kappa2_by_sigma`,
`attack_latency_timeseries_overlay_mean`).

### 5.4 E3: Performance Comparison — Figure 11

#### Figure 11(a): 10-Replica Fault-Free Performance

Controller-driven reproduction lives under `experiment_reproduced/experiment3/`.
It checks out each protocol branch (`tusk`/`dag-rider`, `manta`, `chitu`, `mahi-mahi`) into a
flat tree `$HOME/manta-exp3-{protocol}` on the controller and replicas
(`dag-rider` reuses the `tusk` tree with different `sigma`/`kappa`). This
laptop only SSHs to the **controller**.

##### Configuration

- Protocols: `tusk`, `dag-rider`, `manta`, `chitu`, `mahi-mahi`
- Network: `geo` (paper 6+3+1; see `experiment_reproduced/experiment3/wan/`)
- Faults: 0
- Rates: $40000$–$140000$ tx/s (step $20000$), 2 runs, duration 120s
- Solid-wave params from paper Sec.5.4/6.1 with $n{=}10$, $f{=}3$ (see `matrix.yaml`):
  Tusk $(\sigma,\kappa,\mathrm{ref},\mathrm{cov})=(1,2,7,7)$;
  DAG-Rider $(1,3,7,7)$;
  Chitu / Mahi-Mahi $(1,3,7,7)$;
  Manta $(2,2,4,7)$

See `experiment_reproduced/experiment3/matrix.yaml` and `README.md`.

##### Command

```bash
# CloudLab up + Getting Started first (see Section 3)
python experiment_reproduced/experiment3/run_figure11.py --only-experiment 11a
# equivalent: --only-suite figure11a
# or both 11(a) and 11(c):
python experiment_reproduced/experiment3/run_figure11.py
```

##### Output

Summaries are synced to (**`*.txt` plot inputs only**):

```text
experiment_reproduced/experiment3/results/Figure11a/
  Tusk-result/  DAG-rider-result/  Manta-result/  Chitu-results/  Mahi-mahi-result/
```

`run_figure11.py` then **directly calls** the Figure 11(a) plot script (same as §4.3)
and writes PDFs under `results/regenerate_graphs/`. Use `--skip-plot` to skip.

##### Expected Result

PDF under `results/regenerate_graphs/` matching paper Figure 11(a)
(`631_consensus_tps_vs_consensus_latency` / companion legend).

#### Figure 11(b): 50-Replica Large-Scale Performance

Figure 11(b) is reproduced using the 50-replica AWS deployment on branch
`experiment3`.

See the separate [AWS deployment instructions](https://github.com/michaelwcl323/manta-nsdi27/blob/experiment3/README.md) for the
environment setup, execution commands, runtime, output, and expected result.

#### Figure 11(c): Performance under Silent Faults

Same package as 11(a) (`experiment_reproduced/experiment3/`). Silent faults mean
the last $f=3$ nodes/clients are **not booted** (not the Experiment 2 attack).

##### Configuration

- Protocols: `tusk`, `dag-rider`, `manta`, `chitu`, `mahi-mahi`
- Network: `geo` (same 6+3+1 WAN as 11(a))
- Faults: 3 (silent)
- Rates: $80000$–$180000$ tx/s (step $20000$), 2 runs, duration 120s
- Same solid-wave params as 11(a) (see `matrix.yaml`)

The `rates` in `matrix.yaml` are the nominal aggregate input rates before silent
faults. With `nodes=10` and `faults=3`, the final three nodes and clients are not
booted, so seven clients submit transactions and the TXT summaries report 70%
of the nominal rate:

| Matrix rate (tx/s) | TXT `Input rate` (tx/s) |
|---:|---:|
| 80,000 | 56,000 |
| 100,000 | 70,000 |
| 120,000 | 84,000 |
| 140,000 | 98,000 |
| 160,000 | 112,000 |
| 180,000 | 126,000 |

Keep the nominal 80k–180k values in the matrix; do not replace them with the
effective values printed in the TXT files. Each rate has two runs. Tusk,
DAG-Rider, and Manta store them in separate files, while Chitu and Mahi-Mahi may
store two `SUMMARY` blocks in one TXT file.

See `experiment_reproduced/experiment3/matrix.yaml` and `README.md`.

##### Command

```bash
python experiment_reproduced/experiment3/run_figure11.py --only-experiment 11c
# equivalent: --only-suite figure11c
```

##### Output

Summaries are synced to (**`*.txt` plot inputs only**):

```text
experiment_reproduced/experiment3/results/Figure11c/
  tusk_faulty/  rider_faulty/  manta_faulty/  chitu_faulty/  mahi-mahi-faulty/
```

`run_figure11.py` then **directly calls** the Figure 11(c) plot script (same as §4.3)
and writes PDFs under `results/regenerate_graphs/`. Use `--skip-plot` to skip.

##### Expected Result

PDF under `results/regenerate_graphs/` matching paper Figure 11(c) (`faulty_graph`).

### 5.5 E4: Flexible-Coin Ablation — Figure 12

Controller-driven reproduction lives under `experiment_reproduced/experiment4/`.
It uses the `experiment4` branch (flat manta protocol at repo root, with resource
monitors). This laptop only SSHs to the **controller**; the controller drives all
replica nodes, applies the geo WAN once, and runs complete vs no-flexible-coin
cells from `matrix.yaml`. Table 2 resources are collected during the same runs.

#### Configuration

- Branch: `experiment4`
- Network: `geo` (paper 6+3+1; see `experiment_reproduced/experiment4/wan/`)
- Rates: $80000$ / $100000$ / $120000$ tx/s, 2 runs, duration 120s
- Complete: fast coin + solid commit + commit recheck on; spill trigger/cap 4
- No-flexible: fast coin / solid commit / commit recheck off; spill trigger 2 / cap 1

See `experiment_reproduced/experiment4/matrix.yaml` and `README.md`.

#### Command

```bash
# CloudLab up + Getting Started first (see Section 3)
python experiment_reproduced/experiment4/run_figure12.py
# optional: --only-suite figure12_complete | figure12_noflexible
```

#### Output

Plot/table inputs only are synced to:

```text
experiment_reproduced/experiment4/results/Figure12/complete/*.txt
experiment_reproduced/experiment4/results/Figure12/noflexible/*.txt
experiment_reproduced/experiment4/results/Table2/{resource_summary,table2_mean_over_runs}.csv
```

`run_figure12.py` then **directly calls** the Figure 12 plot script (same as §4.4)
and prints Table 2 means. Use `--skip-plot` to skip.

#### Expected Result

PDFs under `results/regenerate_graphs/` matching paper Figure 12
(`manta_consensus_tps_latency_bar_csv_vs_without_80k_100k_120k` and companions).

### 5.6 E5: Resource Utilization — Table 2

Table 2 is produced by the same Experiment 4 package (`run_figure12.py`). During
each Figure 12 CloudLab cell, `CloudLabBench` records per-host CPU and bandwidth
into `resource_usage_summary.txt`; the orchestrator averages across hosts and
runs into `results/Table2/`.

#### Configuration

Same as §5.5 (complete + noflexible at 80k/100k/120k on geo). No separate bench
sweep is required.

#### Command

```bash
python experiment_reproduced/experiment4/run_figure12.py
# or re-aggregate only:
python paper_data/graph_generated_code/experiment4/aggregate_table2_resources.py \
  --per-run-csv experiment_reproduced/experiment4/results/Table2/resource_summary.csv \
  --output-dir experiment_reproduced/experiment4/results/Table2
```

#### Output

```text
experiment_reproduced/experiment4/results/Table2/resource_summary.csv
experiment_reproduced/experiment4/results/Table2/table2_mean_over_runs.csv
experiment_reproduced/experiment4/results/Table2/table2.md
```

#### Expected Result

Mean CPU (%) and RX/TX (Mbps) per variant×rate matching paper Table 2 trends
(complete vs no-flexible-coin under the same geo load).


## 6. Troubleshooting

### 6.1 **No available physical nodes of type <c6220> found.**

Due to limited resources of C6220 machines, sometimes we cannot generate experiment with enough nodes. 

Choose another available machine type by changing `experiment.node_type` in
`cloudlab_settings.json`, or leave it empty to let CloudLab choose. To use a
different aggregate, configure the corresponding portal/project/profile;
changing `experiment.aggregate` alone does not move the allocation. See the
[CloudLab configuration guide](cloudlab/README.md) for details.

### 6.2 **`python: command not found` or missing Python modules**

`./scripts/environment_setup.sh` installs the required Python packages in the
repository-local virtual environment `venv`. It does not activate that virtual
environment in the current shell. From the repository root, activate it before
running the Python commands in this README:

```bash
source venv/bin/activate
```

Run this command again after opening a new shell. A successful activation makes
`python` and `python3` use the virtual environment; `which python` should point
to `<repository-root>/venv/bin/python`.

## 7. Contact

Please use the repository's GitHub issue tracker for artifact questions and reproducibility problems.

For questions that cannot be discussed publicly, contact [Chenlin WU](mailto:chenlinwu2-c@cityu.edu.hk)
