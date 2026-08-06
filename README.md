# MANTA: Unlocking DAG Flexibility in Asynchronous BFT -- NSDI'27 Artifact

This repository contains the artifact for the paper:

> **MANTA: Unlocking DAG Flexibility in Asynchronous BFT**

The artifact includes the Manta implementation, a scalability-oriented Manta implementation, and the Tusk, DAG-Rider, Mahi-Mahi, and Chitu baselines used in the paper.

Each implementation is maintained in a separate Git branch. This `artifact-evaluation` branch contains the evaluation instructions, experiment configurations, orchestration scripts, and paper results.

## 1. Artifact Overview


| Protocols      | Branch                   | Role                                           |
| -------------- | ------------------------ | ---------------------------------------------- |
| Manta          | `manta`                  | Main implementation                            |
| Manta-Scalable | `manta-scalable-version` | Implementation used for large-scale evaluation |
| Tusk/DAG-Rider | `tusk`                   | Baseline                                       |
| Mahi-Mahi      | `mahi-mahi`              | Baseline                                       |
| Chitu          | `chitu`                  | Baseline                                       |


The exact commits used by the artifact are recorded in
`[branches.yaml](branches.yaml)`.

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
[functional-test] manta-scalable: RUNNING
[functional-test] manta-scalable: PASS
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

Download a CloudLab Portal API token and update the following fields in
`cloudlab_settings.json`:

```text
portal.url
portal.token
portal.project
portal.profile_name
portal.profile_project
portal.duration_hours

experiment.name
experiment.nodes
experiment.node_type
experiment.disk_image

key.private
key.pubkey
ssh_key_password

repo.url
repo.branch
```

If the SSH private key is passphrase-protected, set `ssh_key_password` in `cloudlab_settings.json`. This value is required for the CloudLab remote functional test during deploy.
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

Plotting code for Experiment 4 lives in `paper_data/graph_generated_code/experiment4/`.

#### Figure 12

Compare complete Manta against the no-flexible-coin ablation at input rates $80{,}000$, $100{,}000$, and $120{,}000$ tx/s.

- **complete**: Manta rows from `paper_data/original_data/Figure11a/geo_consensus_tps_latency.csv`
- **no flexible coin**: averages of summary `*.txt` files in `paper_data/original_data/Figure12/`

```bash
cd paper_data/graph_generated_code/experiment4
python3 plot_manta_consensus_tps_latency.py
```

Output:

```text
results/regenerate_graphs/manta_consensus_tps_latency_bar_csv_vs_without_80k_100k_120k.pdf
results/regenerate_graphs/manta_consensus_latency_only_no_legend.pdf
results/regenerate_graphs/manta_consensus_throughput_only_no_legend.pdf
results/regenerate_graphs/manta_consensus_legend_only.pdf
```

The command succeeds if the corresponding PDFs are created and the script prints the output paths.

## 5. 

### E1: Impact of Heterogeneity — Figure 9

#### E1.1: Goal

#### E1.2: Systems and Environment

#### E1.3: Command

#### E1.4: Runtime

#### E1.5: Output

#### E1.6: Expected Result

### E2: Parameter Trade-offs — Figure 10

#### E2.1: Goal

#### E2.2: Systems and Environment

#### E2.3: Command

#### E2.4: Runtime

#### E2.5: Output

#### E2.6: Expected Result

### E3: Performance Comparison — Figure 11

### E4: Flexible-Coin Ablation — Figure 12

### E5: Resource Utilization — Table 2


## 6. Troubleshooting

### 6.1 **No available physical nodes of type <c6220> found.**

Due to limited resources of C6220 machines, sometimes we cannot generate experiment with enough nodes. 

Please use other nodes for experiments, and modify `node_type` and `aggregate` in `cloudlab_settings.json`

## 6. Contact

Please use the repository's GitHub issue tracker for artifact questions and reproducibility problems.

For questions that cannot be discussed publicly, contact [Chenlin WU](mailto:chenlinwu2-c@cityu.edu.hk)