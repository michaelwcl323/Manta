# Experiment 3 / Figure 11(b): Large-Scale Deployment

This directory contains the implementations and scripts used to reproduce the large-scale experiment in Figure 11(b).

## 1. Implementation Overview

This experiment uses scalability-oriented variants of Manta and Chitu. This section explains how they differ from the corresponding original implementations.

### 1.1 Manta Implementation

The large-scale evaluation uses a simplified implementation of **Manta** that enables only the flexible-coin commit path; accordingly, `σ` can be simplified to `1`.
For more details, please refer to [Manta Scalable Implementation](https://github.com/michaelwcl323/manta-nsdi27/blob/experiment3/manta/README.md).

### 1.2 Chitu Implementation

The large-scale evaluation uses an optimized implementation of **Chitu** that preserves its original consensus and commit logic. Workers forward sealed batches without waiting for an acknowledgement quorum, while certificates are propagated through `HeaderBundle` with on-demand weak-certificate synchronization.

## 2. Repository Structure

The experiment3 branch contains the implementations used for Figure 11(b), with each protocol organized in a separate directory. Mahi-Mahi and Tusk are imported from their corresponding protocol branches. For Mahi-Mahi and Tusk, we add process-level parallelism across client, worker, and primary processes to support large-scale experiments. This organization allows all Figure 11(b) experiments to be run from a single working tree.:

```text
experiment3
├── manta/                      # Manta
├── chitu/                      # Chitu
├── mahi-mahi/                  # Mahi-Mahi
├── tusk/                       # Tusk and DAG-Rider
└── README.md
```

For each implementation, `benchmark/fabfile.py` is the operator-facing entry point. The modules under `benchmark/benchmark/` provision AWS instances, install and update code, launch remote processes, download logs, and generate per-run summaries. Runtime files are written under each implementation's `benchmark/logs/` and `benchmark/results/` directories.

All protocol AWS benchmark entry points read the shared `aws_deployment/config.json` and `aws_deployment/nodes.txt`. They use the same bounded-parallel SSH runner and automatically select their own source directory after cloning the repository:

| Working directory | Remote source directory |
|---|---|
| `manta/benchmark` | `manta-nsdi27/manta` |
| `chitu/benchmark` | `manta-nsdi27/chitu` |
| `mahi-mahi/benchmark` | `manta-nsdi27/mahi-mahi` |
| `tusk/benchmark` | `manta-nsdi27/tusk` |

Run `fab install` and `fab remote` from the selected protocol's benchmark directory. Run protocols sequentially on a shared testbed because concurrent runs use the same TCP ports, tmux session names, and remote log paths.

### 2.1 Protocol sources

DAG-Rider reuses the implementation in `tusk/` with a different protocol configuration; it does not have a separate source directory.

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

The AWS deployment workflow is implemented by `aws_deployment/deploy.py`. It reads regions, SSH key settings, security-group settings, EC2 provisioning parameters, and output paths from `aws_deployment/config.json`. AWS credentials are not stored in this file and continue to be supplied by the AWS CLI configuration or an AWS profile.

The workflow provides five separately executable steps:

| Step | Command argument | Purpose |
|---|---|---|
| 1 | `import-key` | Import and verify the SSH public key in every region. |
| 2 | `security-groups` | Create or reuse regional security groups and configure controller SSH access. |
| 3 | `provision` | Provision the configured number of EC2 instances in every region. |
| 4 | `collect-addresses` | Wait for instance health checks and collect public and private addresses. |
| 5 | `network` | Allow inter-node protocol traffic between the collected public addresses. |

After reviewing `aws_deployment/config.json`, run all five steps sequentially with:

```bash
python3 aws_deployment/deploy.py all --config aws_deployment/config.json
```

Within each step, a failure in one region is reported without preventing the other regions from being attempted. The step returns a failure status after printing its regional summary, and `all` does not start the next dependent step when the current step is incomplete.

### 3.2 Import the SSH Key

Import the public key into each AWS region used by the experiment and verify the imported key pairs:

```bash
python3 aws_deployment/deploy.py import-key --config aws_deployment/config.json
```

The public and private key will be used later to access the provisioned EC2 instances through SSH.

### 3.3 Configure Security Groups

Create one security group in each AWS region used by the experiment. The security groups created by this workflow allow SSH access from the controller's public IP address.

Run the following command to obtain the controller's public IPv4 address, create the security groups in the default VPCs, save their IDs to `security_groups.txt`, and verify the resulting ingress rules:

```bash
python3 aws_deployment/deploy.py security-groups --config aws_deployment/config.json
```

The resulting `security_groups.txt` should contain one security group ID for each region, for example:

```text
eu-central-1 sg-xxxxxxxxxxxxxxxxx
us-east-2    sg-yyyyyyyyyyyyyyyyy
ap-east-1    sg-zzzzzzzzzzzzzzzzz
```

At this point, inbound TCP port `22` should be accessible only from the controller's public IP address.

### 3.4 Provision EC2 Instances

Provision the 50 t3.2xlarge EC2 instances used by the Figure 11(b) experiment:

```bash
python3 aws_deployment/deploy.py provision --config aws_deployment/config.json
```

The script launches 30 replicas in Frankfurt (eu-central-1), 15 replicas in Ohio (us-east-2), and 5 replicas in Hong Kong (ap-east-1). The provisioned instance IDs are stored in `aws_deployment/instances.txt` for subsequent configuration and deployment.

The resulting `instances.txt` includes:

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
python3 aws_deployment/deploy.py collect-addresses --config aws_deployment/config.json
```

The collected node information is stored in `aws_deployment/nodes.txt`. The public IP addresses are additionally stored in `aws_deployment/public_ips.txt`.

The Manta Fabric tasks read `aws_deployment/config.json` and `aws_deployment/nodes.txt` directly. A separate `manta/benchmark/settings.json` is not required. The SSH private-key path is derived from `ssh.public_key`, its optional password is read from `ssh.private_key_passphrase`, and the benchmark port and repository settings are read from the `benchmark` object in `config.json`. Do not commit a real private-key password to version control.

Configure inter-node communication after collecting the addresses:

```bash
python3 aws_deployment/deploy.py network --config aws_deployment/config.json
```

This authorizes TCP ports `5000-7000` in every regional security group for the `/32` public address of every experiment node. Do not run the remote benchmark before this step succeeds.

After completing all five steps, the deployment directory contains:

```text
aws_deployment/
├── config.json
├── deploy.py
├── security_groups.txt
├── instances.txt
├── nodes.txt
└── public_ips.txt
```

### 3.6 Stop and Restart Provisioned Instances

Stop the experiment instances when they are temporarily not needed:

```bash
python3 aws_deployment/deploy.py stop --config aws_deployment/config.json
```

This stops EC2 compute billing and removes the local address files because the automatically assigned public IP addresses may change. Attached EBS volumes and some other AWS resources can continue to incur charges while an instance is stopped.

Restart the same instances and automatically wait for health checks and regenerate `nodes.txt` and `public_ips.txt`:

```bash
python3 aws_deployment/deploy.py start --config aws_deployment/config.json
```

If the controller's network or public IP has changed, rerun the `security-groups` step before connecting with Fabric.

### 3.7 Destroy Provisioned Instances

Terminate all active EC2 instances tagged with `Project=MANTA` and `Experiment=Figure11b` in the configured regions:

```bash
python3 aws_deployment/deploy.py destroy --config aws_deployment/config.json
```

The destroy step waits for instance termination and then removes the local instance and address records. It does not delete the imported SSH key pairs or regional security groups.


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

## 5. Reproducing Figure 11(b)

### 5.1 Configuration

The five-protocol execution is configured by `experiment.yml`. The first field, `protocols_enabled`, selects protocols and their execution order. The top-level `benchmark` object defines shared parameters such as committee size, rates, duration, and run count. Each entry under `protocols` provides protocol-specific benchmark or node parameter overrides.

Validate the configuration and show the selected executions without contacting the instances:

```bash
python3 run_experiments.py --config experiment.yml --dry-run
```

### 5.2 Command

```bash
python3 run_experiments.py --config experiment.yml --plot
```

The runner creates the result directories and follows `protocols_enabled` from left to right. For every selected protocol it runs `fab install`, then `fab remote`, then validates and archives the generated summaries. If any step fails, the failure is recorded and the runner continues with the next selected protocol. After all protocols have been attempted, it prints a combined success/failure summary and returns a nonzero exit status when any protocol failed. Remove a protocol from `protocols_enabled` to exclude it.

### 5.3 Output

```text
results/
├── Manta-result/
├── Chitu-results/
├── Tusk-result/
├── DAG-rider-result/
├── Mahi-mahi-result/
├── plot_mean_tps_latency.py
└── 50nodes_protocol_comparison.pdf
```

Only summary files created or updated by the current invocation are copied from each protocol's benchmark directory. Raw logs remain under the corresponding protocol directory. The migrated plotting script reads all summary blocks in the five result directories and averages points having the same protocol and input rate.

### 5.4 Expected Result

The reproduction succeeds when all required runs produce valid consensus throughput and latency summaries and the plotting script generates:

```text
results/50nodes_protocol_comparison.pdf
```
