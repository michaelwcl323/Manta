#!/usr/bin/env bash

set -euo pipefail

SECURITY_GROUP_NAME="${1:-manta-experiment}"
OUTPUT_FILE="${2:-security_groups.txt}"
REGIONS=(eu-central-1 us-east-2 ap-east-1)

for command_name in aws curl; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        echo "Error: ${command_name} is not installed or is not available in PATH." >&2
        exit 1
    fi
done

CONTROLLER_IP="$(curl --fail --silent --show-error https://checkip.amazonaws.com)"
if [[ ! "${CONTROLLER_IP}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    echo "Error: Unable to obtain a valid public IPv4 address: ${CONTROLLER_IP}" >&2
    exit 1
fi

echo "Controller IP: ${CONTROLLER_IP}"
: > "${OUTPUT_FILE}"

for region in "${REGIONS[@]}"; do
    vpc_id="$(aws ec2 describe-vpcs \
        --region "${region}" \
        --filters Name=is-default,Values=true \
        --query 'Vpcs[0].VpcId' \
        --output text)"

    if [[ -z "${vpc_id}" || "${vpc_id}" == "None" ]]; then
        echo "Error: No default VPC found in ${region}." >&2
        exit 1
    fi

    security_group_id="$(aws ec2 create-security-group \
        --region "${region}" \
        --group-name "${SECURITY_GROUP_NAME}" \
        --description "Security group for the MANTA AWS experiment" \
        --vpc-id "${vpc_id}" \
        --query 'GroupId' \
        --output text)"

    printf '%s %s\n' "${region}" "${security_group_id}" | tee -a "${OUTPUT_FILE}"

    aws ec2 authorize-security-group-ingress \
        --region "${region}" \
        --group-id "${security_group_id}" \
        --protocol tcp \
        --port 22 \
        --cidr "${CONTROLLER_IP}/32"
done

while read -r region security_group_id; do
    echo "===== ${region} ====="
    aws ec2 describe-security-groups \
        --region "${region}" \
        --group-ids "${security_group_id}" \
        --query 'SecurityGroups[].[GroupId,GroupName,VpcId,IpPermissions]' \
        --output table
done < "${OUTPUT_FILE}"
