# Manta Scalable Implementation

This repository contains the scalability-oriented implementation of **Manta** used for large-scale experiments.

## Overview

This implementation is designed to evaluate Manta under large network sizes. It follows the main scalable execution path of the protocol and applies engineering optimizations to reduce runtime overhead.

In this implementation, `σ` is fixed to `1`, and the **flexible-coin commit rule** is enabled.

## Implementation Details

After the first solid step, the protocol checks whether a commit can be made by examining the corresponding $ref$ vertices.

In large-scale deployments, the fast-coin commit path is triggered much less frequently. Maintaining this path introduces additional commit-checking overhead. Therefore, the configuration that would otherwise correspond to `σ = 2` without the fast-coin path is simplified to `σ = 1` in this implementation. This implementation focuses on the dominant execution path used in our large-scale evaluation.
