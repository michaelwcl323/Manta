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

## 3. Reproducing the Paper Results

### 3.1 Common Experimental Setup

This section describes the common setup for the CloudLab APT cluster and AWS.

CloudLab is used for the experiments in Figures 9, 10, 11(a), 11(c), and 12. Each experiment uses 10 replica nodes and one additional controller node. The controller coordinates deployment and experiment execution but does not participate in the consensus protocol.

The 50-replica experiment in Figure 11(b) is conducted on AWS because sufficient C6220 nodes may not be simultaneously available on CloudLab.

All CloudLab settings are specified in `cloudlab_settings.json`.

#### 3.1.1 Configure Portal API Access

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
#### 3.1.2 Instantiate the Experiment

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

#### 3.1.3 Initialize the Nodes

Wait until all allocated nodes accept SSH:

```bash
python cloudlab/wait_experiment.py
```

Then wait until the profile startup script on every node finishes:

```bash
python cloudlab/wait_bootstrap.py
```

The bootstrap succeeds only if every node creates `/local/bootstrap.done`. If any node creates `/local/bootstrap.failed`, check `/local/bootstrap.log` on that node.

#### 3.1.4 Deploy the Artifact

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

#### 3.1.5 Terminate the Experiment

When all experiments are finished, terminate the CloudLab experiment to stop using the machines:

```bash
python cloudlab/portal_experiment.py terminate
```


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

## 4. Regenerating Figures from the Paper Data

## 5. Troubleshooting

### 5.1 **No available physical nodes of type <c6220> found.**

Due to limited resources of C6220 machines, sometimes we cannot generate experiment with enough nodes. 

Please use other nodes for experiments, and modify `node_type` and `aggregate` in `cloudlab_settings.json`

## 6. Contact

Please use the repository's GitHub issue tracker for artifact questions and reproducibility problems.

For questions that cannot be discussed publicly, contact [Chenlin WU](mailto:chenlinwu2-c@cityu.edu.hk)