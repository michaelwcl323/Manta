#!/usr/bin/env bash
set -euo pipefail

# ====== 基本配置 ======
REPO_NAME="narwhal"
REPO_URL="https://github.com/Michael112233/narwhal.git"
BRANCH="experiment1"

# CloudLab 所有节点（和 cloudlab_settings.json / remote_setup_config.json 保持一致）
NODES=(
  "10.10.1.1"
  "10.10.1.2"
  "10.10.1.3"
  "10.10.1.4"
  "10.10.1.5"
  "10.10.1.6"
  "10.10.1.7"
  "10.10.1.8"
  "10.10.1.9"
  "10.10.1.10"
)

USER="wucy"
SSH_KEY="$HOME/.ssh/authorized_keys"

# ====== 1. 清理本地配置/数据库 ======
echo "[INFO] Cleaning local db and config files ..."
rm -rf .db-* .*.json
mkdir -p narwhal/benchmark/results

# ====== 2. 本地编译（带 benchmark 特性） ======
echo "[INFO] Building node with benchmark feature ..."
source "$HOME/.cargo/env" 2>/dev/null || export PATH="$HOME/.cargo/bin:$PATH"

cd narwhal/benchmark && cargo build --release --features benchmark

# 创建方便调用的软链接 ./node 和 ./benchmark_client
cd narwhal/benchmark && rm -rf node benchmark_client 2>/dev/null || true
cd narwhal/benchmark && ln -s ./target/release/node node
cd narwhal/benchmark && ln -s ./target/release/benchmark_client benchmark_client

# ====== 3. 生成 keys 和 committee / parameters ======
echo "[INFO] Generating keys on controller node ..."
KEY_FILES=()
NODE_COUNT=${#NODES[@]}

for ((i=0; i<NODE_COUNT; i++)); do
  KEY_FILE="narwhal/benchmark/.node-${i}.json"
  cd narwhal/benchmark && ./node generate_keys --filename "$KEY_FILE"
  KEY_FILES+=("$KEY_FILE")
done

echo "[INFO] Building committee.json and parameters.json via Rust node ..."
./target/release/node \
  --keys "${KEY_FILES[0]}" \
  --committee .committee.json \
  --store .db-0 \
  --parameters .parameters.json \
  --help >/dev/null 2>&1 || true

# ====== 4. 上传配置到所有节点 ======
echo "[INFO] Uploading keys & configs to all nodes ..."

for i in "${!NODES[@]}"; do
  HOST="${NODES[$i]}"
  echo "[INFO] Uploading to $HOST ..."

  scp -i "$SSH_KEY" narwhal/benchmark/.committee.json   "${USER}@${HOST}:${REPO_NAME}/.committee.json"
  scp -i "$SSH_KEY" narwhal/benchmark/.parameters.json  "${USER}@${HOST}:${REPO_NAME}/.parameters.json"
  scp -i "$SSH_KEY" narwhal/benchmark/.node-${i}.json "${USER}@${HOST}:${REPO_NAME}/.node-${i}.json"
done

echo "[INFO] Full benchmark setup (clean → pull → build → keys → upload) finished."