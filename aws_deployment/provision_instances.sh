#!/usr/bin/env bash

set -euo pipefail

KEY_NAME="cloudlab_michael9"
INSTANCE_TYPE="t3.2xlarge"

SG_FILE="./aws_deployment/security_groups.txt"
OUTPUT_FILE="./aws_deployment/instances.txt"

AMI_PARAMETER="/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"

REGIONS=(
    "eu-central-1"
    "us-east-2"
    "ap-east-1"
)

declare -A INSTANCE_COUNTS=(
    ["eu-central-1"]=30
    ["us-east-2"]=15
    ["ap-east-1"]=5
)

if [[ ! -f "${SG_FILE}" ]]; then
    echo "Error: ${SG_FILE} does not exist."
    echo "Run configure_security_groups.sh first."
    exit 1
fi

> "${OUTPUT_FILE}"

for REGION in "${REGIONS[@]}"
do
    COUNT="${INSTANCE_COUNTS[$REGION]}"

    echo "========================================"
    echo "Provisioning ${COUNT} instances in ${REGION}"

    SG_ID=$(awk -v region="${REGION}" \
        '$1 == region {print $2}' "${SG_FILE}")

    if [[ -z "${SG_ID}" ]]; then
        echo "Error: Security group for ${REGION} not found."
        exit 1
    fi

    AMI_ID=$(aws ssm get-parameter \
        --region "${REGION}" \
        --name "${AMI_PARAMETER}" \
        --query 'Parameter.Value' \
        --output text)

    echo "AMI: ${AMI_ID}"
    echo "Security Group: ${SG_ID}"

    INSTANCE_IDS=$(aws ec2 run-instances \
        --region "${REGION}" \
        --image-id "${AMI_ID}" \
        --instance-type "${INSTANCE_TYPE}" \
        --count "${COUNT}" \
        --key-name "${KEY_NAME}" \
        --security-group-ids "${SG_ID}" \
        --associate-public-ip-address \
        --credit-specification CpuCredits=unlimited \
        --tag-specifications \
        "ResourceType=instance,Tags=[{Key=Project,Value=MANTA},{Key=Experiment,Value=Figure11b},{Key=Region,Value=${REGION}}]" \
        --query 'Instances[].InstanceId' \
        --output text)

    for INSTANCE_ID in ${INSTANCE_IDS}
    do
        echo "${REGION} ${INSTANCE_ID}" >> "${OUTPUT_FILE}"
    done
done

echo "========================================"
echo "Waiting for all instances to enter the running state..."

for REGION in "${REGIONS[@]}"
do
    INSTANCE_IDS=$(awk -v region="${REGION}" \
        '$1 == region {print $2}' "${OUTPUT_FILE}")

    aws ec2 wait instance-running \
        --region "${REGION}" \
        --instance-ids ${INSTANCE_IDS}
done

echo "========================================"
echo "EC2 provisioning completed."
echo
cat "${OUTPUT_FILE}"