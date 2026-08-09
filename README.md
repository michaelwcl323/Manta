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

This section configures and provisions the AWS testbed used by the 50-replica
Figure 11(b) experiment. Complete this section before running the reproduction
commands in Section 5.

### 3.1 Configure AWS API Access

Document how to configure the AWS credentials used by the deployment scripts:

```text
TODO: Add the required IAM permissions and the expected AWS credentials file
or environment-variable configuration.
```

### 3.2 Configure SSH Key Pairs

Document how to create or import the same SSH public key under the same key-pair
name in every selected AWS region.

```bash
# TODO: Add SSH key generation and AWS key-pair import/validation commands.
```

### 3.3 Configure the AWS Testbed

Create the protocol benchmark `settings.json` files from a credential-free
template:

```json
{
  "key": {
    "name": "<AWS_KEY_PAIR_NAME>",
    "path": "<SSH_PRIVATE_KEY_PATH>"
  },
  "port": 5000,
  "repo": {
    "name": "<REPOSITORY_NAME>",
    "url": "<REPOSITORY_URL>",
    "branch": "<PROTOCOL_BRANCH>"
  },
  "instances": {
    "type": "<INSTANCE_TYPE>",
    "regions": ["<REGION_1>", "<REGION_2>"]
  }
}
```

> TODO: Document the final instance type, AWS regions, per-region instance
> count, VPC/public-IP requirements, security-group ports, service quotas, and
> cost warning. Explain which fields differ between protocol branches.

### 3.4 Provision the EC2 Instances

```bash
# TODO: Add the command that creates the complete 50-instance testbed.
```

Document how to check the provisioning status and confirm that exactly 50
replica machines are running.

### 3.5 Initialize the Nodes

```bash
# TODO: Wait for SSH, install remote dependencies, and clone protocol code.
```

Document the remote operating-system image, login user, repository layout, and
the success condition for initialization.

### 3.6 Deploy and Validate the Artifact

```bash
# TODO: Deploy binaries/configuration and run a short remote functional test.
```

```text
TODO: Add the expected deployment and remote functional-test PASS output.
```

After this step succeeds, the AWS testbed is ready for the full experiment.

### 3.7 Stop or Terminate the Testbed

```bash
# TODO: Add the commands for stopping, restarting, and permanently terminating
# the AWS instances.
```

Explain which operation preserves instance storage and which operation is
permanent. Remind evaluators to collect results before termination.

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
