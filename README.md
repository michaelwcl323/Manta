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

This section describes the common setup procedures for the CloudLab/APT and AWS experimental platforms.

The CloudLab/APT workflow is fully automated through the official Portal API. The scripts create a geni-lib portal profile, instantiate an experiment from that profile, download the manifest, and wait for node initialization. No manual portal clicks are required after the Portal API token is downloaded.

The hardware, node count, image, repository, branch, and token paths are configured in `cloudlab_settings.json`.

> **Note:** The 10-node CloudLab/APT configuration is used for reproducing Figures 9, 10, 11(a), 11(c), and 12. The 50-node experiment in Figure 11(b) is reproduced on AWS.

#### 3.1.1 Configure Portal API Access

Log in to the CloudLab/APT portal and download a Portal API token from the account menu. Save it to the path configured by `portal.token` in `cloudlab_settings.json`.

In `cloudlab_settings.json`, update the Portal API fields: `portal.url`, `portal.token`, `portal.project`, `portal.profile_name`, `portal.profile_project`, `portal.profile_script`, and `portal.duration_hours`.

Also update the experiment, SSH key, and repository fields: `experiment.name`, `experiment.nodes`, `experiment.node_type`, `experiment.disk_image`, `key.private`, `key.pubkey`, `repo.url`, and `repo.branch`.

Do not edit `cloudlab/profile.py` by hand. Change parameters in `cloudlab_settings.json` instead.

#### 3.1.2 Instantiate the Experiment

After updating `cloudlab_settings.json`, start the experiment with:

```bash
python cloudlab/portal_experiment.py start
```

`start` already regenerates `cloudlab/profile.py` from `cloudlab_settings.json`, creates or updates the portal profile, and then instantiates the experiment. You do not need to run `create-profile` separately for the normal artifact workflow.

`create-profile` is optional. Use it only if you want to upload or refresh the portal profile without starting an experiment:

```bash
python cloudlab/portal_experiment.py create-profile
```

Check the experiment status until it becomes `ready`:

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
build/head-node
```

#### 3.1.3 Initialize the Nodes

Wait until all allocated nodes accept SSH:

```bash
python cloudlab/wait_experiment.py
```

Then wait until the startup script on every node finishes:

```bash
python cloudlab/wait_bootstrap.py
```

The bootstrap succeeds only if every node creates `/local/bootstrap.done`. If any node creates `/local/bootstrap.failed`, check `/local/bootstrap.log` on that node.

To terminate the experiment:

```bash
python cloudlab/portal_experiment.py terminate
```

#### 3.1.4 Deploy the Artifact


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