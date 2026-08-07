#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BENCHMARK_DIR="$REPO_ROOT/benchmark"
TARGET_BRANCH="exp2_coupled"

NETWORK_ORDER=(
  "geo"
  "80ms"
)

WORKLOAD_ORDER=(
  "balanced"
  "custom-high-3"
  "custom-high-5"
)

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

run_cmd() {
  log "+ $*"
  "$@"
}

network_settings_file() {
  case "$1" in
    geo) printf '%s\n' 'cloudlab_settings_6_3_1.json' ;;
    80ms) printf '%s\n' 'cloudlab_settings_80ms.json' ;;
    *)
      printf 'Unsupported network tag: %s\n' "$1" >&2
      return 1
      ;;
  esac
}

workload_rate_type() {
  case "$1" in
    balanced) printf '%s\n' 'balanced' ;;
    custom-high-3|custom-high-5) printf '%s\n' 'custom' ;;
    *)
      printf 'Unsupported workload tag: %s\n' "$1" >&2
      return 1
      ;;
  esac
}

workload_percentages() {
  case "$1" in
    balanced) printf '%s\n' '' ;;
    custom-high-3) printf '%s\n' '[1,1,1,1,6,6,6,6,6,6]' ;;
    custom-high-5) printf '%s\n' '[1,1,1,1,30,30,30,30,30,30]' ;;
    *)
      printf 'Unsupported workload tag: %s\n' "$1" >&2
      return 1
      ;;
  esac
}

ensure_expected_branch() {
  local current_branch
  current_branch="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)"
  if [[ "$current_branch" != "$TARGET_BRANCH" ]]; then
    printf 'Expected branch %s, but current branch is %s\n' "$TARGET_BRANCH" "$current_branch" >&2
    exit 1
  fi
}

commit_and_push_results() {
  local commit_message="$1"

  run_cmd git -C "$REPO_ROOT" add :/

  if git -C "$REPO_ROOT" diff --cached --quiet; then
    printf 'No staged changes found for commit: %s\n' "$commit_message" >&2
    exit 1
  fi

  run_cmd git -C "$REPO_ROOT" commit -m "$commit_message"
  run_cmd git -C "$REPO_ROOT" push -u origin "$TARGET_BRANCH"
}

run_workload() {
  local network_tag="$1"
  local workload_tag="$2"
  local rate_type
  local percentages
  local commit_message
  local -a fab_args

  rate_type="$(workload_rate_type "$workload_tag")"
  percentages="$(workload_percentages "$workload_tag")"
  commit_message="coupled ${network_tag} ${workload_tag}"

  fab_args=(
    cloudlab-remote
    "--network-tag=${network_tag}"
    "--rate-type=${rate_type}"
    "--workload-tag=${workload_tag}"
  )

  if [[ "$rate_type" == "custom" ]]; then
    fab_args+=("--percentages=${percentages}")
  fi

  log "Starting benchmark for network=${network_tag}, workload=${workload_tag}"
  run_cmd fab "${fab_args[@]}"
  commit_and_push_results "$commit_message"
  log "Finished benchmark for network=${network_tag}, workload=${workload_tag}"
}

switch_network() {
  local network_tag="$1"
  local settings_file

  settings_file="$(network_settings_file "$network_tag")"
  log "Switching WAN profile to ${network_tag} (${settings_file})"
  run_cmd fab cloudlab-wan --action=clear
  run_cmd fab "cloudlab-wan" "--settings-file=${settings_file}"
}

main() {
  ensure_expected_branch
  cd "$BENCHMARK_DIR"

  for network_tag in "${NETWORK_ORDER[@]}"; do
    switch_network "$network_tag"
    for workload_tag in "${WORKLOAD_ORDER[@]}"; do
      run_workload "$network_tag" "$workload_tag"
    done
  done

  log 'All coupled benchmarks completed successfully.'
}

main "$@"
