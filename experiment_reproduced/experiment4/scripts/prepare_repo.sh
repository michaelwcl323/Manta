#!/usr/bin/env bash
# Prepare flat experiment4 protocol tree on a replica (or controller).
# Usage: prepare_repo.sh <repo_dir> <branch> <repo_url>
set -euo pipefail

REPO_DIR="${1:?repo dir}"
BRANCH="${2:?branch}"
REPO_URL="${3:?repo url}"

export PATH="$HOME/.cargo/bin:$PATH"
source "$HOME/.cargo/env" 2>/dev/null || true

resolved_repo="$(readlink -f "$REPO_DIR")"
if [ "$resolved_repo" = "/" ] || [ "$resolved_repo" = "$HOME" ]; then
  echo "refusing unsafe repo path: $REPO_DIR" >&2
  exit 1
fi
rm -rf "$REPO_DIR"
mkdir -p "$(dirname "$REPO_DIR")"
git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$REPO_DIR"

cd "$REPO_DIR"
test "$(git rev-parse HEAD)" = "$(git rev-parse "origin/$BRANCH")"

# Drop leftover / previously checked-in bench outputs so AE collect never mixes runs.
rm -rf benchmark/logs benchmark/results benchmark/manta_result \
  benchmark/data/latest benchmark/data/paper-data
rm -f benchmark/bench-*.txt benchmark/summary*.txt 2>/dev/null || true

if ! command -v cargo >/dev/null 2>&1; then
  curl https://sh.rustup.rs -sSf | sh -s -- -y --default-toolchain stable
  source "$HOME/.cargo/env"
fi

# Build every binary from the verified fresh checkout.
cargo build --release --features benchmark
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
