# Experiment 4 / Figure 12 + Table 2 — controller-driven reproduction

Scripts and results for re-running paper Figure 12 (complete Manta vs no-flexible-coin)
and Table 2 (CPU / bandwidth) with the `experiment4` branch (flat manta protocol at
repo root, including CloudLab resource monitors). **Only the CloudLab controller is
contacted from this laptop**; the controller SSHs to replica hosts.

## Layout

```text
experiment_reproduced/experiment4/
  run_figure12.py                # local entry (SSH -> controller); also Table 2
  matrix.yaml                    # complete / noflexible cells + rates
  wan/
    cloudlab_settings_geo.json   # paper N2 geo 6+3+1 (same as Experiment 1/2)
  scripts/
    controller_orchestrator.py   # runs ON the controller
    prepare_repo.sh              # clone/build ON each replica
  results/
    Figure12/complete/             # summary *.txt only (plot input)
    Figure12/noflexible/           # summary *.txt only (plot input)
    Table2/                      # resource_summary.csv, table2_mean_over_runs.csv, table2.md
```

## Scenario switching

| Switch | How |
|---|---|
| **suite** (`figure12_complete` / `figure12_noflexible`) | `design_tag` / fast-coin / spill flags from `matrix.yaml` |
| **network** (`geo` only) | WAN `clear` then `setup` once with `wan/cloudlab_settings_geo.json` |
| **cell** (input rate) | `CloudLabBench` at 80k / 100k / 120k (2 runs each) |

**Order:** parallel prepare on all 10 replicas (+ controller monorepo) → wait until
all finish → then geo delay setup + experiments (never overlap).

Table 2 is **not** a separate CloudLab sweep: resource monitors run during the
Figure 12 cells; the orchestrator averages host CPU/RX/TX into `results/Table2/`.

## Prerequisites

1. CloudLab experiment is up (`portal_experiment.py` + `wait_*`).
2. Getting Started deployed (`python cloudlab/deploy_environment.py --skip-functional-test` is enough).
3. Remote `origin` has branch `experiment4` (or controller/replicas can fetch it).
4. `cloudlab_settings.json` has replica `hosts` (`10.10.1.1`–`10.10.1.10`) and SSH key.

## Run Figure 12 + Table 2

```bash
# from manta-nsdi27 repo root (artifact-evaluation checkout)
python experiment_reproduced/experiment4/run_figure12.py
```

Useful filters:

```bash
python experiment_reproduced/experiment4/run_figure12.py --only-suite figure12_complete
python experiment_reproduced/experiment4/run_figure12.py --only-suite figure12_noflexible
python experiment_reproduced/experiment4/run_figure12.py --skip-prepare   # reuse already prepared trees
python experiment_reproduced/experiment4/run_figure12.py --skip-plot
python experiment_reproduced/experiment4/run_figure12.py --keep-remote-workdir
```

## Parameter matrix (paper)

| Suite | Variant | Notes |
|---|---|---|
| `figure12_complete` | complete | fast coin + solid commit + commit recheck on; spill trigger/cap **4** (Fig11a manta) |
| `figure12_noflexible` | noflexible | fast coin / solid commit / commit recheck **off**; spill trigger **2** / cap **1** |

Shared: geo WAN, 10 nodes, duration 120s, rates `{80k,100k,120k}`, 2 runs, `σ=2`, `κ=2`, `ref=4`, `coverage=7`, header/batch delay 100/50 ms, `batch_size` 500000.

## Plotting / Table 2

`run_figure12.py` **calls the Figure 12 plot script automatically** after syncing
results (writes under `results/regenerate_graphs/`) and prints Table 2 means.
Pass `--skip-plot` to disable, or re-plot later:

```bash
python paper_data/graph_generated_code/experiment4/plot_manta_consensus_tps_latency.py \
  --complete-dir experiment_reproduced/experiment4/results/Figure12/complete \
  --noflexible-dir experiment_reproduced/experiment4/results/Figure12/noflexible \
  --output-dir results/regenerate_graphs

python paper_data/graph_generated_code/experiment4/aggregate_table2_resources.py \
  --per-run-csv experiment_reproduced/experiment4/results/Table2/resource_summary.csv \
  --output-dir experiment_reproduced/experiment4/results/Table2
```

Without `--complete-dir`, the plot script keeps the old Figure11a CSV fallback for
complete-Manta points.

## Notes

- First prepare builds Rust on every replica **in parallel**; expect a long first run.
  Delay/experiments start only after all hosts (and the controller monorepo) finish.
- WAN profile is paper Sec 6.1 **geo (N2)**: clusters **6+3+1** on
  `10.10.1.1–6` / `.7–9` / `.10`; **intra-cluster RTT = 0**; inter-cluster RTT =
  2× paper OWD (A–B 103, B–C 153, A–C 234).
- WAN is applied only to replica `hosts`. The controller is not a peer, so
  **controller↔replica latency stays 0** (SSH also bypasses netem via TCP/22 exclusion).
- Collect / sync back to the laptop is **plot/table inputs only**:
  `Figure12/{complete,noflexible}/*.txt`, and `Table2/*.csv` (+ `table2.md`).
  No raw dumps, round_certificate CSV, or bench PNG/PDF intermediates.
- `prepare_repo.sh` skips `cargo` when release binaries exist and only symlinks
  `benchmark_client` (never the `node/` crate directory).
