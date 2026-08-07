# Experiment 2 / Figure 10 — controller-driven reproduction

Scripts and results for re-running paper Figure 10 with the `experiment2` branch
(flat manta protocol at repo root). **Only the CloudLab controller is contacted
from this laptop**; the controller SSHs to replica hosts.

## Layout

```text
experiment_reproduced/experiment2/
  run_figure10.py                # local entry (SSH -> controller)
  matrix.yaml                    # 10a/10b/10c cells + scenario params
  wan/
    cloudlab_settings_geo.json   # paper N2 geo 6+3+1 (same as Experiment 1)
  scripts/
    controller_orchestrator.py   # runs ON the controller
    prepare_repo.sh              # clone/build ON each replica
  results/
    Figure10a_10b/                 # consensus_summary.csv only (plot input)
    Figure10c/                     # {k2-ref4,...}/latency.csv only (plot input)
```

## Scenario switching

| Switch | How |
|---|---|
| **suite** (`figure10a_10b` / `figure10c`) | `design_tag` / `load_tag` / attack flags from `matrix.yaml` |
| **network** (`geo` only) | WAN `clear` then `setup` once with `wan/cloudlab_settings_geo.json` |
| **cell** (`σ` / `κ` / `ref` [/ `coverage`]) | `CloudLabBench` `node_params` (and cartesian or explicit configs) |

**Order:** parallel prepare on all 10 replicas (+ controller monorepo) → wait until
all finish → then geo delay setup + experiments (never overlap).

## Prerequisites

1. CloudLab experiment is up (`portal_experiment.py` + `wait_*`).
2. Getting Started deployed (`python cloudlab/deploy_environment.py --skip-functional-test` is enough).
3. Remote `origin` has branch `experiment2` (or controller/replicas can fetch it).
4. `cloudlab_settings.json` has replica `hosts` (`10.10.1.1`–`10.10.1.10`) and SSH key.

## Run all of Figure 10

```bash
# from manta-nsdi27 repo root (artifact-evaluation checkout)
python experiment_reproduced/experiment2/run_figure10.py
```

Useful filters:

```bash
python experiment_reproduced/experiment2/run_figure10.py --only-suite figure10a_10b
python experiment_reproduced/experiment2/run_figure10.py --only-suite figure10c
python experiment_reproduced/experiment2/run_figure10.py --skip-prepare   # reuse already prepared trees
python experiment_reproduced/experiment2/run_figure10.py --skip-plot
python experiment_reproduced/experiment2/run_figure10.py --keep-remote-workdir
```

## Parameter matrix (paper)

| Suite | Figure | Notes |
|---|---|---|
| `figure10a_10b` | 10(a)+10(b) | `σ∈{1,2}`, `κ∈{2,3,4}`, `ref∈{4,7,10}`, **coverage fixed at 7**, 3 runs, balanced 100k tx/s, geo |
| `figure10c` | 10(c) | Attack on (start 60s); four `(κ, ref, coverage)` configs; 1 run each |

Shared defaults: 10 nodes, duration 120s, header/batch delay 50 ms, `batch_size` 500000.

## Plotting

`run_figure10.py` **calls the Figure 10 plot scripts automatically** after syncing
results (writes under `results/regenerate_graphs/`). Pass `--skip-plot` to
disable, or re-plot later:

```bash
python paper_data/graph_generated_code/experiment2/plot_latency_by_kappa_sigma_reference.py \
  --summary-csv experiment_reproduced/experiment2/results/Figure10a_10b/consensus_summary.csv \
  --output results/regenerate_graphs/latency_by_kappa_sigma_reference.pdf

python paper_data/graph_generated_code/experiment2/plot_reference_impact_kappa2_by_sigma.py \
  --summary-csv experiment_reproduced/experiment2/results/Figure10a_10b/consensus_summary.csv \
  --output results/regenerate_graphs/reference_impact_kappa2_by_sigma.pdf

python paper_data/graph_generated_code/experiment2/plot_attack_latency_timeseries.py \
  --merge-four-runs \
    experiment_reproduced/experiment2/results/Figure10c/k2-ref4 \
    experiment_reproduced/experiment2/results/Figure10c/k2-ref7 \
    experiment_reproduced/experiment2/results/Figure10c/k3-ref7 \
    experiment_reproduced/experiment2/results/Figure10c/k3-ref10 \
  --time-axis commit --rolling-stat mean \
  --attack-start-secs 60 --attack-end-secs 120 \
  --order k2-c4,k2-c7,k3-c7,k3-c10 \
  --output results/regenerate_graphs/attack_latency_timeseries_overlay_mean.pdf
```

## Notes

- First prepare builds Rust on every replica **in parallel**; expect a long first run.
  Delay/experiments start only after all hosts (and the controller monorepo) finish.
- WAN profile is paper Sec 6.1 / Figure 10 **geo (N2)**: clusters **6+3+1** on
  `10.10.1.1–6` / `.7–9` / `.10`; **intra-cluster RTT = 0**; inter-cluster RTT =
  2× paper OWD (A–B 103, B–C 153, A–C 234).
- WAN is applied only to replica `hosts`. The controller is not a peer, so
  **controller↔replica latency stays 0** (SSH also bypasses netem via TCP/22 exclusion).
- Collect / sync back to the laptop is **plot inputs only**:
  `consensus_summary.csv` for 10(a)/(b), and `Figure10c/{label}/latency.csv` for 10(c).
  No raw dumps, logs, png/pdf intermediates.
