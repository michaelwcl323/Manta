# Experiment 1 / Figure 9 — controller-driven reproduction

Scripts and results for re-running paper Figure 9 with the `experiment1` branch
(`coupled/` + `decoupled/`). **Only the CloudLab controller is contacted from
this laptop**; the controller SSHs to replica hosts.

## Layout

```text
experiment_reproduced/experiment1/
  run_figure9.py                 # local entry (SSH -> controller)
  matrix.yaml                    # 9a/9b cells + scenario params
  wan/                           # WAN profiles (80ms, geo)
  scripts/
    controller_orchestrator.py   # runs ON the controller
    prepare_flat_tree.sh         # materialize flat trees ON replicas
  results/
    Figure9a/                      # decoupled @ 100k summaries
    Figure9b/                      # coupled @ 60k summaries
```

## Scenario switching (solved here)

| Switch | How |
|---|---|
| **variant** (`decoupled` / `coupled`) | Controller prepares flat trees `$HOME/manta-exp1-{decoupled,coupled}` from `experiment1:{decoupled,coupled}` and points `repo.name` at the active one |
| **network** (`80ms` / `geo`) | Before that network’s workloads: WAN `clear` then `setup` with `wan/*.json` (re-applied whenever the delay profile changes) |
| **workload** (`balanced` / `custom-high-*`) | Only `CloudLabBench` `bench_params` change (rate_type / percentages / tags) |

**Order:** parallel prepare on all 10 replicas → wait until all finish → then delay setup + experiments (never overlap).


## Prerequisites

1. CloudLab experiment is up (`portal_experiment.py` + `wait_*`).
2. Getting Started deployed (`python cloudlab/deploy_environment.py --skip-functional-test` is enough).
3. Remote `origin` has branch `experiment1` (or controller can fetch it).
4. `cloudlab_settings.json` has replica `hosts` (`10.10.1.1`–`10.10.1.10`) and SSH key.

## Run all of Figure 9

```bash
# from manta-nsdi27 repo root (artifact-evaluation checkout)
python experiment_reproduced/experiment1/run_figure9.py
```

Useful filters:

```bash
python experiment_reproduced/experiment1/run_figure9.py --only-variant decoupled
python experiment_reproduced/experiment1/run_figure9.py --only-network geo --only-workload balanced
python experiment_reproduced/experiment1/run_figure9.py --skip-prepare   # reuse already prepared flat trees
```

## Plotting

`run_figure9.py` **calls the Figure 9 plot scripts automatically** after syncing
results (writes under `results/regenerate_graphs/`). Pass `--skip-plot` to
disable, or re-plot later:

```bash
python paper_data/graph_generated_code/experiment1/plot_workload_grouped_network_metrics_decouple_100k_consensus.py \
  --data-root experiment_reproduced/experiment1/results/Figure9a \
  --output-dir results/regenerate_graphs

python paper_data/graph_generated_code/experiment1/plot_workload_grouped_network_metrics_tusk_coupled_60k_consensus.py \
  --data-root experiment_reproduced/experiment1/results/Figure9b \
  --output-dir results/regenerate_graphs
```

## Notes

- First prepare builds Rust on every replica for both variants **in parallel** across hosts; expect a long first run, then delay/experiments start only after all hosts finish.
- WAN profiles match paper Sec 6.1 / Figure 9 (not the five-site ohio–singapore defaults):
  - **`80ms` (N1):** every replica pair RTT = 80 ms (OWD 40 ms each way).
  - **`geo` (N2):** clusters **6+3+1** on `10.10.1.1–6` / `.7–9` / `.10`; **intra-cluster RTT = 0**; inter-cluster RTT = 2× paper OWD (A–B 51.3 → 103, B–C 76.6 → 153, A–C 117.15 → 234).
- Each time the network tag changes (`80ms` ↔ `geo`, or re-entering a profile for a new variant), orchestrator **clear + setup** delay again before running workloads.
- Collect / sync back to the laptop is **summary `*.txt` only** (no csv/png/pdf dumps).
- WAN is applied only to replica `hosts` in `cloudlab_settings.json`. The controller is not a peer, so **controller↔replica latency stays 0** (SSH also bypasses netem via TCP/22 exclusion).
