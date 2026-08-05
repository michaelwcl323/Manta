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

This section describes the common setup procedures for the CloudLab and AWS experimental platforms.

We use CloudLab for experiments with 10 replicas. The CloudLab experiments use C6220 machines running Ubuntu 22.04.2 LTS. Because a sufficient number of C6220 nodes may not be simultaneously available, the large-scale
experiment with 50 replicas is conducted on AWS.

The following instructions describe the CloudLab setup. The AWS setup is described separately in Section 3.2.

#### 3.1.1 CloudLab Profile and Hardware

The CloudLab profile provisions 10 C6220 nodes connected through a dedicated experimental LAN. Each physical node runs one replica and one co-located benchmark client.

> **Note:** The 10-node CloudLab configuration is used for reproducing Figures 9, 10, 11(a), 11(c), and 12. The 50-node experiment in Figure 11(b) is reproduced on AWS.

**Step 1: Download the CloudLab credential**

Log in to the CloudLab portal and download your user credential, which is a `.pem` file.

**Step 2: Configure CloudLab settings**

Edit `cloudlab_settings.json` and enter your CloudLab account, project, credential, and experiment information.

**Step 3: Create the CloudLab profile.**

Run:
```bash
python cloudlab/create_profile.py
```

This command creates or updates the CloudLab profile. It does not allocate or start physical nodes.

The profile creation command only needs to be executed once unless profile.py or the hardware configuration is changed.

#### 3.1.2 Instantiate the Experiment

Start a CloudLab experiment from the previously created profile:
```bash
python cloudlab/start_experiment.py
```

If finish the experiment and don't run for a second time, run the command below to terminate the experiment.
```bash
python cloudlab/terminate_experiment.py
```

#### 3.1.3 Initialize the Nodes


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