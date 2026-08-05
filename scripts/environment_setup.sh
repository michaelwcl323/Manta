#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${MANTA_VENV_DIR:-$ROOT_DIR/venv}"

APT_PACKAGES=(
  build-essential
  ca-certificates
  clang
  cmake
  curl
  git
  iproute2
  libclang-dev
  python3
  python3-pip
  python3-venv
  tmux
)

FALLBACK_PYTHON_PACKAGES=(
  boto3
  fabric
  matplotlib
)

log() {
  printf '\n[environment_setup] %s\n' "$*"
}

run_sudo() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf '[environment_setup] error: required command not found after setup: %s\n' "$1" >&2
    exit 1
  fi
}

install_system_packages() {
  if ! command -v apt-get >/dev/null 2>&1; then
    printf '[environment_setup] error: this script currently supports apt-based Ubuntu/Debian systems.\n' >&2
    exit 1
  fi

  log "Installing system packages"
  run_sudo env DEBIAN_FRONTEND=noninteractive apt-get update
  run_sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "${APT_PACKAGES[@]}"
}

install_rust() {
  if ! command -v rustup >/dev/null 2>&1; then
    log "Installing rustup"
    curl https://sh.rustup.rs -sSf | sh -s -- -y
  fi

  # shellcheck disable=SC1091
  [[ -f "$HOME/.cargo/env" ]] && source "$HOME/.cargo/env"
  export PATH="$HOME/.cargo/bin:$PATH"

  log "Configuring Rust stable toolchain"
  rustup default stable

  require_command rustc
  require_command cargo
}

install_python_packages() {
  log "Creating Python virtual environment"
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel

  log "Installing benchmark Python packages"
  if [[ -f "$ROOT_DIR/benchmark/requirements.txt" ]]; then
    (cd "$ROOT_DIR/benchmark" && "$VENV_DIR/bin/pip" install -r requirements.txt)
  elif [[ -f "$ROOT_DIR/requirements.txt" ]]; then
    "$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements.txt"
  else
    "$VENV_DIR/bin/pip" install "${FALLBACK_PYTHON_PACKAGES[@]}"
  fi
}

print_summary() {
  log "Environment setup complete"
  printf 'Repository: %s\n' "$ROOT_DIR"
  printf 'Python virtual environment: %s\n' "$VENV_DIR"
  printf 'Rust: %s\n' "$(rustc --version)"
  printf 'Cargo: %s\n' "$(cargo --version)"
  printf 'Python: %s\n' "$("$VENV_DIR/bin/python" --version)"
  printf '\nTo use the benchmark Python tools in an interactive shell, run:\n'
  printf '  source "%s/bin/activate"\n' "$VENV_DIR"
}

main() {
  install_system_packages
  install_rust
  install_python_packages
  print_summary
}

main "$@"
