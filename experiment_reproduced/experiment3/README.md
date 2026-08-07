# Experiment 3 / Figure 11(a)+(c) — controller-driven reproduction

Scripts and results for re-running paper Figure 11(a) and 11(c) across four
protocols (`tusk`, `manta`, `chitu`, `mahi-mahi`). **Only the CloudLab controller
is contacted from this laptop**; the controller SSHs to replica hosts.

Figure 11(b) (50 replicas) is AWS-only and is **not** covered here — see
`aws/README.md`. Protocol code lives on branch `experiment3`
under `manta/`.

## Layout

```text
experiment_reproduced/experiment3/
  run_figure11.py                # local entry (SSH -> controller)
  matrix.yaml                    # 11a/11c cells + per-protocol node params
  wan/
    cloudlab_settings_geo.json   # paper N2 geo 6+3+1 (same as Experiment 1)
  scripts/
    controller_orchestrator.py   # runs ON the controller
    prepare_repo.sh              # clone/build ON each replica (+ controller)
  results/
    Figure11a/                     # {Tusk-result,Manta-result,Chitu-results,Mahi-mahi-result}/
    Figure11c/                     # {tusk_faulty,manta_faulty,chitu_faulty,mahi-mahi-faulty}/
```

## Scenario switching

| Switch | How |
|---|---|
| **variant** (`tusk` / `manta` / `chitu` / `mahi-mahi`) | Flat trees `$HOME/manta-exp3-{variant}` from that protocol’s git branch; `repo.name` points at the active tree |
| **suite** (`figure11a` / `figure11c`) | Faults, rates, design tags, and WAN mode from `matrix.yaml` |
| **network** | 11(a) and 11(c): both use geo WAN `clear`+`setup` |

**Silent faults (11c):** `faults=3` means the last 3 committee members / clients are
**not booted** (standard Narwhal silent-fault mode). This is **not** the Experiment 2
certificate-limiting attack.

**Order:** prepare all hosts for variant 1 (parallel) → variant 2 → … → wait until
all prepares finish → then WAN + experiments (never overlap).

## Prerequisites

1. CloudLab experiment is up (`portal_experiment.py` + `wait_*`).
2. Getting Started deployed (`python cloudlab/deploy_environment.py --skip-functional-test` is enough).
3. Remote `origin` has branches `tusk`, `manta`, `chitu`, `mahi-mahi`.
4. `cloudlab_settings.json` has replica `hosts` (`10.10.1.1`–`10.10.1.10`) and SSH key.

## Run Figure 11(a) and 11(c)

```bash
# from manta-nsdi27 repo root (artifact-evaluation checkout)
python experiment_reproduced/experiment3/run_figure11.py
```

Useful filters:

```bash
python experiment_reproduced/experiment3/run_figure11.py --only-suite figure11a
python experiment_reproduced/experiment3/run_figure11.py --only-suite figure11c
python experiment_reproduced/experiment3/run_figure11.py --only-variant manta
python experiment_reproduced/experiment3/run_figure11.py --skip-prepare   # reuse prepared trees
python experiment_reproduced/experiment3/run_figure11.py --skip-plot
python experiment_reproduced/experiment3/run_figure11.py --keep-remote-workdir
```

## Parameter matrix (paper)

| Suite | Figure | Network | Faults | Rates (tx/s) | Runs |
|---|---|---|---|---|---|
| `figure11a` | 11(a) | geo | 0 | 40k–140k step 20k | 2 |
| `figure11c` | 11(c) | geo | 3 silent | 80k–180k step 20k | 2 |

Shared: 10 nodes, duration 120s, `tx_size` 512, balanced load. Per-protocol
`node_params` (delays, manta flexible-coin flags, etc.) live in `matrix.yaml`.

## Plotting

`run_figure11.py` **calls the Figure 11(a)/(c) plot scripts automatically** after
syncing results (writes under `results/regenerate_graphs/`). Pass `--skip-plot`
to disable, or re-plot later:

```bash
python paper_data/graph_generated_code/experiment3/Figure11a/plot_mean_tps_latency.py \
  --data-root experiment_reproduced/experiment3/results/Figure11a \
  --output-dir results/regenerate_graphs

python paper_data/graph_generated_code/experiment3/Figure11c/plot_mean_tps_latency.py \
  --data-root experiment_reproduced/experiment3/results/Figure11c \
  --output-dir results/regenerate_graphs
```

## Notes

- First prepare builds Rust for **four** protocol trees on every replica (and the
  controller) — expect a long first run. Delay/experiments start only after all
  prepares finish.
- WAN profile for 11(a) is paper Sec 6.1 **geo (N2)**: clusters **6+3+1** on
  `10.10.1.1–6` / `.7–9` / `.10`; **intra-cluster RTT = 0**; inter-cluster RTT =
  2× paper OWD (A–B 103, B–C 153, A–C 234).
- WAN is applied only to replica `hosts`. The controller is not a peer, so
  **controller↔replica latency stays 0** (SSH also bypasses netem via TCP/22 exclusion).
- Protocol-native result locations differ (tusk design/network folders, manta
  `manta_result/…`, chitu `results/`, mahi design/network). The orchestrator
  collects matching `summary*.txt` / `bench-*.txt` into the paper-style folders
  under `results/Figure11{a,c}/`. Sync back to the laptop is **those `*.txt` only**
  (no csv/png/pdf dumps).
