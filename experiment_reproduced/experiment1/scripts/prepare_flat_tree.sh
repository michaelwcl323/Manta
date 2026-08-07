#!/usr/bin/env bash
# Materialize experiment1/{coupled|decoupled} as a flat Narwhal git tree on a replica.
# Usage: prepare_flat_tree.sh <monorepo_dir> <branch> <subdir> <flat_repo_dir>
set -euo pipefail

MONOREPO="${1:?monorepo dir}"
BRANCH="${2:?branch}"
SUBDIR="${3:?coupled|decoupled}"
FLAT="${4:?flat repo dir}"
BARE="${FLAT}.git"

export PATH="$HOME/.cargo/bin:$PATH"

if [ ! -d "$MONOREPO/.git" ]; then
  echo "missing monorepo at $MONOREPO; clone experiment1 first" >&2
  exit 1
fi

cd "$MONOREPO"
git fetch --tags origin "$BRANCH" || true
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH" || true

rm -rf "$FLAT" "$BARE"
mkdir -p "$FLAT"
git archive "$BRANCH:$SUBDIR" | tar -x -C "$FLAT"

cd "$FLAT"
git init -q
git checkout -B "$BRANCH"
git -c user.email="artifact@local" -c user.name="artifact" add -A
git -c user.email="artifact@local" -c user.name="artifact" commit -qm "flat ${SUBDIR} from ${BRANCH}" || true
git clone --bare . "$BARE"
git remote remove origin 2>/dev/null || true
git remote add origin "$BARE"
git push -u origin "$BRANCH" >/dev/null

# Python deps for CloudLabBench (used on controller, but keep remotes consistent).
if [ -f benchmark/requirements.txt ]; then
  python3 -m venv benchmark/.venv
  benchmark/.venv/bin/pip install --upgrade pip >/dev/null
  benchmark/.venv/bin/pip install -r benchmark/requirements.txt >/dev/null
fi

# Build node binaries expected by CloudLabBench._update / run.
source "$HOME/.cargo/env" 2>/dev/null || true
if ! command -v cargo >/dev/null 2>&1; then
  curl https://sh.rustup.rs -sSf | sh -s -- -y --default-toolchain stable
  source "$HOME/.cargo/env"
fi
cargo build --release --features benchmark
test -x ./target/release/node
test -x ./target/release/benchmark_client
ln -sfn ./target/release/benchmark_client ./benchmark_client

echo "prepared flat tree: $FLAT"
