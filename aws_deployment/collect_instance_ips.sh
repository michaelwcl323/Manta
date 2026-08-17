#!/usr/bin/env bash

set -euo pipefail

INSTANCE_FILE="./aws_deployment/instances.txt"
NODE_FILE="./aws_deployment/nodes.txt"
PUBLIC_IP_FILE="./aws_deployment/public_ips.txt"

REGIONS=(
    "eu-central-1"
    "us-east-2"
    "ap-east-1"
)

if [[ ! -f "${INSTANCE_FILE}" ]]; then
    echo "Error: ${INSTANCE_FILE} does not exist."
    echo "Run provision_instances.sh first."
    exit 1
fi

> "${NODE_FILE}"
> "${PUBLIC_IP_FILE}"

for REGION in "${REGIONS[@]}"
do
    echo "========================================"
    echo "Collecting instances in ${REGION}"

    mapfile -t INSTANCE_IDS < <(
        awk -v region="${REGION}" \
            '$1 == region {print $2}' "${INSTANCE_FILE}"
    )

    if [[ ${#INSTANCE_IDS[@]} -eq 0 ]]; then
        echo "Error: No instances found for ${REGION}"
        exit 1
    fi

    echo "Waiting for instance status checks..."

    aws ec2 wait instance-status-ok \
        --region "${REGION}" \
        --instance-ids "${INSTANCE_IDS[@]}"

    aws ec2 describe-instances \
        --region "${REGION}" \
        --instance-ids "${INSTANCE_IDS[@]}" \
        --query \
        'Reservations[].Instances[].[InstanceId,PublicIpAddress,PrivateIpAddress]' \
        --output text |
    while read -r INSTANCE_ID PUBLIC_IP PRIVATE_IP
    do
        if [[ "${PUBLIC_IP}" == "None" || -z "${PUBLIC_IP}" ]]; then
            echo "Error: ${INSTANCE_ID} has no public IP."
            exit 1
        fi

        echo \
            "${REGION} ${INSTANCE_ID} ${PUBLIC_IP} ${PRIVATE_IP}" \
            >> "${NODE_FILE}"

        echo "${PUBLIC_IP}" >> "${PUBLIC_IP_FILE}"
    done
done

NODE_COUNT=$(wc -l < "${NODE_FILE}")

if [[ "${NODE_COUNT}" -ne 50 ]]; then
    echo "Error: Expected 50 instances, found ${NODE_COUNT}."
    exit 1
fi

echo "========================================"
echo "Collected ${NODE_COUNT} instances."
echo
echo "Node information:"
cat "${NODE_FILE}"