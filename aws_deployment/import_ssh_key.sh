#!/usr/bin/env bash

set -euo pipefail

PUBLIC_KEY_PATH="${1:-${HOME}/.ssh/cloudlab_michael9.pub}"
KEY_PAIR_NAME="${2:-cloudlab_michael9}"
REGIONS=(eu-central-1 us-east-2 ap-east-1)

if ! command -v aws >/dev/null 2>&1; then
    echo "Error: AWS CLI is not installed or is not available in PATH." >&2
    exit 1
fi

if [[ ! -f "${PUBLIC_KEY_PATH}" ]]; then
    echo "Error: Public key not found: ${PUBLIC_KEY_PATH}" >&2
    exit 1
fi

for region in "${REGIONS[@]}"; do
    echo "Importing ${KEY_PAIR_NAME} into ${region}..."
    aws ec2 import-key-pair \
        --region "${region}" \
        --key-name "${KEY_PAIR_NAME}" \
        --public-key-material "fileb://${PUBLIC_KEY_PATH}"
done

for region in "${REGIONS[@]}"; do
    echo "===== ${region} ====="
    aws ec2 describe-key-pairs \
        --region "${region}" \
        --key-names "${KEY_PAIR_NAME}" \
        --query 'KeyPairs[].[KeyName,KeyPairId]' \
        --output table
done
