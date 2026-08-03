# MANTA: Unlocking DAG Flexibility in Asynchronous BFT -- NSDI'27 Artifact 

This repository contains the artifact for the paper:

> **MANTA: Unlocking DAG Flexibility in Asynchronous BFT**

The artifact includes the Manta implementation, a scalability-oriented
Manta implementation, and the Tusk, DAG-Rider, Mahi-Mahi, and Chitu
baselines used in the paper.

Each implementation is maintained in a separate Git branch. This
`artifact-evaluation` branch contains the evaluation instructions,
experiment configurations, orchestration scripts, and paper results.

## 1. Artifact Overview

| Protocols | Branch | Role |
|---|---|---|
| Manta | `manta` | Main implementation |
| Manta-Scalable | `manta-scalable-version` | Implementation used for large-scale evaluation |
| Tusk/DAG-Rider | `tusk` | Baseline |
| Mahi-Mahi | `mahi-mahi` | Baseline |
| Chitu | `chitu` | Baseline |

The exact commits used by the artifact are recorded in
[`branches.yaml`](branches.yaml).

## 2. Getting Started and Functional Validation


### 2.1 Prerequisites


### 2.2 Clone the Artifact


### 2.3 Run the Functional Test



### 2.4 Expected Output


## 3. Reproducing the Paper Results


### E1: Impact of Heterogeneity — Figure 9

#### E1.1: Goal


#### E1.2: Systems and Environment


#### E1.3: Command


#### E1.4: Runtime

#### E1.5: Output

#### E1.6: Expected Result


### E2: Parameter Trade-offs — Figure 10


### E3: Performance Comparison — Figure 11


### E4: Flexible-Coin Ablation — Figure 12


### E5: Resource Utilization — Table 2


## 4. Regenerating Figures from the Paper Data


## 5. Contact

Please use the repository's GitHub issue tracker for artifact questions and reproducibility problems.

For questions that cannot be discussed publicly, contact [Chenlin WU](mailto:chenlinwu2-c@cityu.edu.hk)