# Experiment 3 / Figure 11(b): Large-Scale Deployment

This directory contains the implementations and scripts used to reproduce the
large-scale experiment in Figure 11(b).

## 1. Implementation Overview

This experiment uses scalability-oriented variants of Manta and Chitu. This
section explains how they differ from the corresponding original
implementations.

### 1.1 Manta Scalable Implementation

This repository contains the scalability-oriented implementation of **Manta** used for large-scale experiments.

#### Overview

This implementation is designed to evaluate Manta under large network sizes. It follows the main scalable execution path of the protocol and applies engineering optimizations to reduce runtime overhead.

In this implementation, `σ` is fixed to `1`, and the **flexible-coin commit rule** is enabled.

#### Implementation Details

After the first solid step, the protocol checks whether a commit can be made by examining the corresponding $ref$ vertices.

In large-scale deployments, the fast-coin commit path is triggered much less frequently. Maintaining this path introduces additional commit-checking overhead. Therefore, the configuration that would otherwise correspond to `σ = 2` without the fast-coin path is simplified to `σ = 1` in this implementation. This implementation focuses on the dominant execution path used in our large-scale evaluation.

### 1.2 Chitu ACK Implementation

This repository contains the scalability-oriented implementation of **Chitu** used for large-scale experiments.

#### Overview

This implementation is designed to evaluate Chitu under large network sizes. It preserves the original consensus and commit logic while applying engineering optimizations to reduce blocking communication and certificate-synchronization overhead.

In this implementation, the worker does not wait for an acknowledgement quorum before forwarding a sealed batch to its local primary, and certificate propagation is optimized through HeaderBundle with on-demand weak-certificate synchronization.

#### Implementation Details

After sealing a batch, the originating worker immediately makes the batch available to its local primary, while the reliable worker-to-worker broadcast and acknowledgement collection continue in the background.

When proposing a header, a primary bundles the available certificates of the referenced parents with the header. Receiving primaries process these certificates before processing the header itself, while missing certificates are completed through weak-certificate synchronization during adaptive waiting. This implementation focuses on reducing blocking overhead along the main execution path used in our large-scale evaluation.

## 2. Repository Structure

The implementations used by Figure 11(b) are split across Git branches. The
`experiment3` branch contains the two large-scale variants introduced in
Section 1:

```text
experiment3
├── manta/                      # Manta
├── chitu/                      # Chitu
└── README.md
```

For both implementations, `benchmark/fabfile.py` is the operator-facing entry
point. The modules under `benchmark/benchmark/` provision AWS instances,
install and update code, launch remote processes, download logs, and generate
per-run summaries. Runtime files are written under each implementation's
`benchmark/logs/` and `benchmark/results/` directories.

### 2.1 Protocol branches

The other Figure 11(b) implementations are maintained in separate branches:

| Protocol | Git branch |
|---|---|
| Manta | `experiment3` |
| Chitu | `experiment3` |
| Mahi-Mahi | `mahi-mahi` |
| Tusk | `tusk` |
| DAG-Rider | `tusk` |

DAG-Rider reuses the implementation tree in the `tusk` branch with a different
protocol configuration; it does not have a separate source branch.

## 3. AWS Remote Environment Deployment

This section configures and provisions the AWS testbed used by the 50-replica Figure 11(b) experiment. Complete this section before running the reproduction commands in Section 5.

### 3.1 AWS CLI and Account Configuration

The AWS experiment is managed from a local controller through the AWS Command Line Interface (AWS CLI). The controller is not part of the consensus cluster and is only used to provision EC2 instances, configure the testbed, launch the
experiment, and collect results.

Ensure that AWS CLI is installed on the controller:

```bash
aws --version
```

Configure the AWS credentials used for the artifact:

```bash
aws configure
```

Provide the **AWS Access Key ID** and **Secret Access Key** associated with the account used for the experiment. A default region is not required because all deployment commands explicitly specify the target AWS region.

Verify whether the credentials are valid:
```bash
aws sts get-caller-identity
```

The command should return the AWS account and IAM identity associated with the configured credentials.

The 50-replica experiment uses the following three AWS regions:

| AWS Cluster | Region |
|---|---|
| eu-central-1 | Frankfurt |
| us-east-2   |    Ohio |
| ap-east-1   |    Hong Kong |

Verify that these regions are available to the account:

```bash
aws ec2 describe-regions \
    --all-regions \
    --query "Regions[?RegionName=='eu-central-1' || RegionName=='us-east-2' || RegionName=='ap-east-1'].[RegionName,OptInStatus]" \
    --output table
```

### 3.2 Import the SSH Key

Import the public key into each AWS region used by the experiment:

```bash
PUBLIC_KEY=~/.ssh/cloudlab_michael9.pub
KEY_NAME=cloudlab_michael9

for REGION in eu-central-1 us-east-2 ap-east-1
do
    aws ec2 import-key-pair \
        --region $REGION \
        --key-name $KEY_NAME \
        --public-key-material fileb://$PUBLIC_KEY
done
```

Verify that the key pair has been imported successfully:

```bash
for REGION in eu-central-1 us-east-2 ap-east-1
do
    echo "===== $REGION ====="
    aws ec2 describe-key-pairs \
        --region $REGION \
        --key-names $KEY_NAME \
        --query 'KeyPairs[].[KeyName,KeyPairId]' \
        --output table
done
```

The public and private key will be used later to access the provisioned EC2 instances through SSH.


### 3.3 Configure Security Groups

Create one security group in each AWS region used by the experiment. At this stage, the security groups only allow SSH access from the controller. The inter-replica communication rules are added after the EC2 instances are provisioned and their public IP addresses are known.


## 4. Regenerating Figure 11(b) from the Paper Data

The `artifact-evaluation` branch contains the archived paper summaries and the
Figure 11(b) plotting script.

### 4.1 Paper Data

```text
paper_data/original_data/Figure11b/
├── Chitu-results/
├── DAG-rider-result/
├── Mahi-mahi-result/
├── Manta-result/
└── Tusk-result/
```

### 4.2 Command

```bash
git switch artifact-evaluation
cd paper_data/graph_generated_code/experiment3/Figure11b
python3 plot_mean_tps_latency.py
```

### 4.3 Output

```text
results/regenerate_graphs/50nodes_protocol_comparison.pdf
```

> TODO: Add the expected console output and the success criteria for graph
> regeneration.

## 5. Reproducing Figure 11(b)

### 5.1 Configuration


> TODO: Add a separate table for protocol-specific parameters and identify the
> Git branch used for every protocol.

### 5.2 Command

```bash
# AWS testbed deployment and validation in Section 3 must succeed first.
# TODO: Add the complete Figure 11(b) execution command or per-protocol command
# sequence.
```

Document the protocol execution order, branch switching, monitoring commands,
and restart behavior for interrupted runs.

### 5.3 Output

```text
TODO: Add the reproduced-result directory tree, including one directory per
protocol and the per-run summary naming convention.
```

Document how logs and summaries are copied from the protocol branches into the
Figure 11(b) plotting layout.

### 5.4 Expected Result

The reproduction succeeds when all required runs produce valid consensus
throughput and latency summaries and the plotting script generates:

```text
results/regenerate_graphs/50nodes_protocol_comparison.pdf
```

> TODO: Add the expected qualitative trend and the accepted variance from the
> paper result.
