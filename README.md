# Experiment 3 / Figure 11(b): Large-Scale Deployment

This directory contains the implementations and scripts used to reproduce the large-scale experiment in Figure 11(b).

## 1. Implementation Overview

This experiment uses scalability-oriented variants of Manta and Chitu. This section explains how they differ from the corresponding original implementations.

### 1.1 Manta Implementation

The large-scale evaluation uses a simplified implementation of **Manta** that enables only the flexible-coin commit path; accordingly, `σ` can be simplified to `1`.

### 1.2 Chitu Implementation

The large-scale evaluation uses an optimized implementation of **Chitu** that preserves its original consensus and commit logic. Workers forward sealed batches without waiting for an acknowledgement quorum, while certificates are propagated through `HeaderBundle` with on-demand weak-certificate synchronization.

## 2. Repository Structure

The implementations used by Figure 11(b) are split across Git branches. The `experiment3` branch contains the two large-scale variants introduced in Section 1:

```text
experiment3
├── manta/                      # Manta
├── chitu/                      # Chitu
└── README.md
```

For both implementations, `benchmark/fabfile.py` is the operator-facing entry point. The modules under `benchmark/benchmark/` provision AWS instances, install and update code, launch remote processes, download logs, and generate per-run summaries. Runtime files are written under each implementation's `benchmark/logs/` and `benchmark/results/` directories.

### 2.1 Protocol branches

The other Figure 11(b) implementations are maintained in separate branches:

| Protocol | Git branch |
|---|---|
| Manta | `experiment3` |
| Chitu | `experiment3` |
| Mahi-Mahi | `mahi-mahi` |
| Tusk | `tusk` |
| DAG-Rider | `tusk` |

DAG-Rider reuses the implementation tree in the `tusk` branch with a different protocol configuration; it does not have a separate source branch.

## 3. AWS Remote Environment Deployment

This section configures and provisions the AWS testbed used by the 50-replica Figure 11(b) experiment. Complete this section before running the reproduction commands in Section 5.

### 3.1 AWS CLI and Account Configuration

The AWS experiment is managed from a local controller through the AWS Command Line Interface (AWS CLI). The controller is not part of the consensus cluster and is only used to provision EC2 instances, configure the testbed, launch the experiment, and collect results.

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

Import the public key into each AWS region used by the experiment and verify the imported key pairs:

```bash
./aws_deployment/import_ssh_key.sh
```

The public and private key will be used later to access the provisioned EC2 instances through SSH.


### 3.3 Configure Security Groups

Create one security group in each AWS region used by the experiment. At this stage, the security groups only allow SSH access from the controller. The inter-replica communication rules are added after the EC2 instances are provisioned and their public IP addresses are known.

Run the following command to obtain the controller's public IPv4 address, create the security groups in the default VPCs, save their IDs to `security_groups.txt`, and verify the resulting ingress rules:

```bash
./aws_deployment/configure_security_groups.sh
```

The resulting `security_groups.txt` should contain one security group ID for each region, for example:

```text
eu-central-1 sg-xxxxxxxxxxxxxxxxx
us-east-2    sg-yyyyyyyyyyyyyyyyy
ap-east-1    sg-zzzzzzzzzzzzzzzzz
```

At this point, inbound TCP port `22` should be accessible only from the controller's public IP address. Additional rules required for communication between the experiment replicas will be configured after instance provisioning.

### 3.4 Provision EC2 Instances

Provision the 50 t3.2xlarge EC2 instances used by the Figure 11(b) experiment:

```bash
./aws_deployment/provision_instances.sh
```

The script launches 30 replicas in Frankfurt (eu-central-1), 15 replicas in Ohio (us-east-2), and 5 replicas in Hong Kong (ap-east-1). The provisioned instance IDs are stored in `aws_deployment/instances.txt` for subsequent configuration and deployment.

The output will be 

```
aws_deployment/
├── import_ssh_key.sh
├── configure_security_groups.sh
├── security_groups.txt
├── provision_instances.sh
└── instances.txt
```

and `instances.txt` includes

```
eu-central-1 i-xxxxxxxx
eu-central-1 i-xxxxxxxx
...
us-east-2 i-yyyyyyyy
...
ap-east-1 i-zzzzzzzz
...
```

### 3.5 Collect Instance Addresses

Wait for all EC2 instances to become ready and collect their public and private IP addresses:

```bash
./aws_deployment/collect_instance_ips.sh
```

The collected node information is stored in `aws_deployment/nodes.txt`. The public IP addresses used for cross-region communication are additionally stored in `aws_deployment/public_ips.txt`.

### 3.6 Configure Inter-Replica Communication

Configure the security groups to allow communication among all provisioned replicas:

```bash
./aws_deployment/configure_replica_network.sh
```

The script restricts inter-replica inbound traffic to the public IP addresses of the 50 provisioned EC2 instances.

### 3.7 Validate the AWS Testbed

Validate the instance distribution, EC2 configuration, SSH connectivity, and cross-region communication:

```bash
./aws_deployment/validate_testbed.sh
```

A successful validation confirms that the 50-replica AWS testbed is ready for the Figure 11(b) experiment.


## 4. Regenerating Figure 11(b) from the Paper Data

The `artifact-evaluation` branch contains the archived paper summaries and the Figure 11(b) plotting script.

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

> TODO: Add the expected console output and the success criteria for graph regeneration.

## 5. Reproducing Figure 11(b)

### 5.1 Configuration


> TODO: Add a separate table for protocol-specific parameters and identify the Git branch used for every protocol.

### 5.2 Command

```bash
# AWS testbed deployment and validation in Section 3 must succeed first.
# TODO: Add the complete Figure 11(b) execution command or per-protocol command sequence.
```

Document the protocol execution order, branch switching, monitoring commands, and restart behavior for interrupted runs.

### 5.3 Output

```text
TODO: Add the reproduced-result directory tree, including one directory per protocol and the per-run summary naming convention.
```

Document how logs and summaries are copied from the protocol branches into the Figure 11(b) plotting layout.

### 5.4 Expected Result

The reproduction succeeds when all required runs produce valid consensus throughput and latency summaries and the plotting script generates:

```text
results/regenerate_graphs/50nodes_protocol_comparison.pdf
```

> TODO: Add the expected qualitative trend and the accepted variance from the paper result.
