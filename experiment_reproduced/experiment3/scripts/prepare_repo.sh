#!/usr/bin/env bash
# Prepare flat experiment3 protocol tree on a replica (or controller).
# Usage: prepare_repo.sh <repo_dir> <branch> <repo_url>
set -euo pipefail

REPO_DIR="${1:?repo dir}"
BRANCH="${2:?branch}"
REPO_URL="${3:?repo url}"

export PATH="$HOME/.cargo/bin:$PATH"
source "$HOME/.cargo/env" 2>/dev/null || true

if [ ! -d "$REPO_DIR/.git" ]; then
  git clone "$REPO_URL" "$REPO_DIR"
fi

cd "$REPO_DIR"
git fetch --tags origin "$BRANCH" || true
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH" || true

# Drop leftover / previously checked-in bench outputs so AE collect never mixes runs.
rm -rf benchmark/logs benchmark/results benchmark/manta_result \
  benchmark/data/latest benchmark/data/paper-data \
  benchmark/tusk_coupled benchmark/decouple \
  benchmark/exp1results benchmark/result_decouple \
  benchmark/manta_compare benchmark/manta_final_geo
rm -f benchmark/bench-*.txt benchmark/summary*.txt benchmark/round_*.csv 2>/dev/null || true

if ! command -v cargo >/dev/null 2>&1; then
  curl https://sh.rustup.rs -sSf | sh -s -- -y --default-toolchain stable
  source "$HOME/.cargo/env"
fi

# Build once; reuse existing release binaries on later prepares.
if [ -x ./target/release/node ] && [ -x ./target/release/benchmark_client ]; then
  echo "[prepare] release binaries already present; skip cargo build"
else
  cargo build --release --features benchmark
fi
test -x ./target/release/node
test -x ./target/release/benchmark_client
# `node/` is the crate source dir — never replace it with a binary symlink.
# CloudLabBench resolves the binary via ./target/release/node.
ln -sfn ./target/release/benchmark_client ./benchmark_client

if [ -f benchmark/requirements.txt ]; then
  python3 -m venv benchmark/.venv
  benchmark/.venv/bin/pip install --upgrade pip >/dev/null
  benchmark/.venv/bin/pip install -r benchmark/requirements.txt >/dev/null
fi

echo "prepared $REPO_DIR@$BRANCH"
