# Manta Scalable Implementation

This repository contains the scalability-oriented implementation of **Manta** used for large-scale experiments.

## Overview

This implementation is designed to evaluate Manta under large network sizes. It follows the main scalable execution path of the protocol and applies engineering optimizations to reduce runtime overhead.

In this implementation, `σ` is fixed to `1`, and the **flexible-coin commit rule** is enabled.

## Implementation Details

When the node parameter `support_broadcast` is enabled, a node that observes `f+1` certificates in the support round broadcasts one signed `CoinVote` to all other primaries. The commit check starts only after signed votes from distinct authorities reach `f+1` stake, modeling the network latency of making the flexible coin available. When disabled, the support gate invokes the commit check directly. These votes model coin readiness only; leader selection remains deterministic round-robin and the implementation does not derive randomness from the votes.
