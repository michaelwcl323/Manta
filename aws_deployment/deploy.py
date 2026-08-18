#!/usr/bin/env python3

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.request import ProxyHandler, build_opener


class DeploymentError(RuntimeError):
    pass


class AwsDeployment:
    def __init__(self, config_path, profile=None):
        self.config_path = Path(config_path).expanduser().resolve()
        self.repo_root = self.config_path.parent.parent
        with self.config_path.open(encoding="utf-8") as config_file:
            self.config = json.load(config_file)
        self.regions = self.config["regions"]
        if not self.regions:
            raise ValueError("regions must not be empty")
        if any(int(item["instance_count"]) < 0 for item in self.regions):
            raise ValueError("instance_count must not be negative")
        if shutil.which("aws") is None:
            raise DeploymentError("AWS CLI is not installed or is not in PATH")
        self.profile = profile

    def path(self, value):
        path = Path(value).expanduser()
        return path if path.is_absolute() else self.repo_root / path

    def aws(self, region, *arguments, output="json"):
        command = ["aws"]
        if self.profile:
            command.extend(["--profile", self.profile])
        command.extend(["--region", region, *arguments])
        if output:
            command.extend(["--output", output])
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode:
            message = result.stderr.strip() or result.stdout.strip()
            raise DeploymentError(message or f"AWS CLI exited with {result.returncode}")
        if output == "json":
            return json.loads(result.stdout or "{}")
        return result.stdout.strip()

    def run_by_region(self, title, operation):
        print(f"\n=== {title} ===")
        successful = []
        failed = []
        results = {}
        for region_config in self.regions:
            region = region_config["name"]
            print(f"\n--- {region} ---")
            try:
                results[region] = operation(region_config)
                successful.append(region)
                print(f"SUCCESS: {region}")
            except (DeploymentError, OSError, ValueError, KeyError) as error:
                failed.append(region)
                print(f"FAILED: {region}: {error}", file=sys.stderr)
        print(f"Successful regions: {' '.join(successful) or 'none'}")
        print(f"Failed regions: {' '.join(failed) or 'none'}")
        return not failed, results

    def import_key(self):
        ssh = self.config["ssh"]
        public_key_path = self.path(ssh["public_key"])
        key_name = ssh["key_name"]
        if not public_key_path.is_file():
            raise FileNotFoundError(f"public key not found: {public_key_path}")

        def import_region(region_config):
            region = region_config["name"]
            response = self.aws(
                region,
                "ec2",
                "describe-key-pairs",
                "--filters",
                f"Name=key-name,Values={key_name}",
            )
            key_pairs = response["KeyPairs"]
            if key_pairs:
                print(f"Reusing {key_pairs[0]['KeyPairId']} ({key_name}).")
                return key_pairs[0]["KeyPairId"]
            response = self.aws(
                region,
                "ec2",
                "import-key-pair",
                "--key-name",
                key_name,
                "--public-key-material",
                f"fileb://{public_key_path}",
            )
            print(f"Imported {response['KeyPairId']} ({key_name}).")
            return response["KeyPairId"]

        success, _ = self.run_by_region("Step 1: Import SSH key", import_region)
        return success

    def controller_ip(self):
        # SSH connections are direct TCP connections and do not use the
        # controller's HTTP(S) proxy. Bypass proxy environment variables here
        # so the security-group CIDR matches the actual SSH source address.
        opener = build_opener(ProxyHandler({}))
        with opener.open("https://checkip.amazonaws.com", timeout=15) as response:
            controller_ip = response.read().decode("ascii").strip()
        octets = controller_ip.split(".")
        if len(octets) != 4 or any(
            not octet.isdigit() or not 0 <= int(octet) <= 255 for octet in octets
        ):
            raise ValueError(f"invalid controller IPv4 address: {controller_ip}")
        return controller_ip

    def configure_security_groups(self):
        security_group = self.config["security_group"]
        group_name = security_group["name"]
        description = security_group["description"]
        output_file = self.path(security_group["output_file"])
        controller_ip = self.controller_ip()
        controller_cidr = f"{controller_ip}/32"
        print(f"Controller IP: {controller_ip}")

        def configure_region(region_config):
            region = region_config["name"]
            vpcs = self.aws(
                region,
                "ec2",
                "describe-vpcs",
                "--filters",
                "Name=is-default,Values=true",
            )["Vpcs"]
            if not vpcs:
                raise ValueError(f"no default VPC found in {region}")
            vpc_id = vpcs[0]["VpcId"]

            groups = self.aws(
                region,
                "ec2",
                "describe-security-groups",
                "--filters",
                f"Name=vpc-id,Values={vpc_id}",
                f"Name=group-name,Values={group_name}",
            )["SecurityGroups"]
            if groups:
                group = groups[0]
                group_id = group["GroupId"]
                print(f"Reusing {group_id}.")
            else:
                group_id = self.aws(
                    region,
                    "ec2",
                    "create-security-group",
                    "--group-name",
                    group_name,
                    "--description",
                    description,
                    "--vpc-id",
                    vpc_id,
                )["GroupId"]
                group = self.aws(
                    region,
                    "ec2",
                    "describe-security-groups",
                    "--group-ids",
                    group_id,
                )["SecurityGroups"][0]
                print(f"Created {group_id}.")

            ssh_rule_exists = any(
                permission.get("IpProtocol") == "tcp"
                and permission.get("FromPort") == 22
                and permission.get("ToPort") == 22
                and any(
                    ip_range.get("CidrIp") == controller_cidr
                    for ip_range in permission.get("IpRanges", [])
                )
                for permission in group.get("IpPermissions", [])
            )
            if ssh_rule_exists:
                print(f"SSH ingress for {controller_cidr} already exists.")
            else:
                self.aws(
                    region,
                    "ec2",
                    "authorize-security-group-ingress",
                    "--group-id",
                    group_id,
                    "--protocol",
                    "tcp",
                    "--port",
                    "22",
                    "--cidr",
                    controller_cidr,
                )
                print(f"Authorized SSH ingress for {controller_cidr}.")

            verified = self.aws(
                region,
                "ec2",
                "describe-security-groups",
                "--group-ids",
                group_id,
            )["SecurityGroups"][0]
            print(json.dumps(verified, indent=2))
            return group_id

        success, group_ids = self.run_by_region(
            "Step 2: Configure security groups", configure_region
        )
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with output_file.open("w", encoding="utf-8") as output:
            for region_config in self.regions:
                region = region_config["name"]
                if region in group_ids:
                    output.write(f"{region} {group_ids[region]}\n")
        print(f"Security group IDs written to {output_file}")
        return success

    def read_region_ids(self, filename):
        path = self.path(filename)
        if not path.is_file():
            raise FileNotFoundError(path)
        values = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                region, resource_id = line.split(maxsplit=1)
                values.setdefault(region, []).append(resource_id)
        return values

    def provision_instances(self):
        ssh = self.config["ssh"]
        security_group = self.config["security_group"]
        instances = self.config["instances"]
        group_ids = self.read_region_ids(security_group["output_file"])
        output_file = self.path(instances["output_file"])
        existing_ids = {}
        if output_file.is_file():
            existing_ids = self.read_region_ids(instances["output_file"])

        def save_instance_ids():
            output_file.parent.mkdir(parents=True, exist_ok=True)
            with output_file.open("w", encoding="utf-8") as output:
                for configured_region in self.regions:
                    configured_region_name = configured_region["name"]
                    for instance_id in existing_ids.get(configured_region_name, []):
                        output.write(f"{configured_region_name} {instance_id}\n")

        def provision_region(region_config):
            region = region_config["name"]
            expected_count = int(region_config["instance_count"])
            region_group_ids = group_ids.get(region, [])
            if len(region_group_ids) != 1:
                raise ValueError(f"expected one security group ID for {region}")
            response = self.aws(
                region,
                "ec2",
                "describe-instances",
                "--filters",
                "Name=tag:Project,Values=MANTA",
                "Name=tag:Experiment,Values=Figure11b",
                "Name=instance-state-name,Values=pending,running,stopping,stopped",
            )
            discovered_ids = sorted(
                instance["InstanceId"]
                for reservation in response["Reservations"]
                for instance in reservation["Instances"]
            )
            current_ids = discovered_ids
            if len(current_ids) > expected_count:
                raise ValueError(
                    f"{region} has {len(current_ids)} recorded instances; "
                    f"expected at most {expected_count}"
                )

            missing_count = expected_count - len(current_ids)
            if missing_count:
                ami_id = self.aws(
                    region,
                    "ssm",
                    "get-parameter",
                    "--name",
                    instances["ami_parameter"],
                )["Parameter"]["Value"]
                response = self.aws(
                    region,
                    "ec2",
                    "run-instances",
                    "--image-id",
                    ami_id,
                    "--instance-type",
                    instances["type"],
                    "--count",
                    str(missing_count),
                    "--key-name",
                    ssh["key_name"],
                    "--security-group-ids",
                    region_group_ids[0],
                    "--associate-public-ip-address",
                    "--credit-specification",
                    "CpuCredits=unlimited",
                    "--tag-specifications",
                    "ResourceType=instance,Tags=[{Key=Project,Value=MANTA},"
                    "{Key=Experiment,Value=Figure11b},"
                    f"{{Key=Region,Value={region}}}]",
                )
                current_ids.extend(
                    instance["InstanceId"] for instance in response["Instances"]
                )
                current_ids = sorted(set(current_ids))
                existing_ids[region] = current_ids
                save_instance_ids()
                print(f"Created {missing_count} instances.")
            else:
                existing_ids[region] = current_ids
                save_instance_ids()
                print(f"Reusing {len(current_ids)} recorded instances.")

            self.aws(
                region,
                "ec2",
                "wait",
                "instance-running",
                "--instance-ids",
                *current_ids,
                output=None,
            )
            return current_ids

        success, instance_ids = self.run_by_region(
            "Step 3: Provision EC2 instances", provision_region
        )
        for region, ids in instance_ids.items():
            existing_ids[region] = ids
        save_instance_ids()
        print(f"Instance IDs written to {output_file}")
        return success

    def collect_addresses(self):
        instances = self.config["instances"]
        instance_ids = self.read_region_ids(instances["output_file"])
        nodes_file = self.path(instances["nodes_file"])
        public_ips_file = self.path(instances["public_ips_file"])

        def collect_region(region_config):
            region = region_config["name"]
            expected_count = int(region_config["instance_count"])
            ids = instance_ids.get(region, [])
            if len(ids) != expected_count:
                raise ValueError(
                    f"expected {expected_count} instances in {region}, found {len(ids)}"
                )
            self.aws(
                region,
                "ec2",
                "wait",
                "instance-status-ok",
                "--instance-ids",
                *ids,
                output=None,
            )
            response = self.aws(
                region,
                "ec2",
                "describe-instances",
                "--instance-ids",
                *ids,
            )
            nodes = []
            for reservation in response["Reservations"]:
                for instance in reservation["Instances"]:
                    public_ip = instance.get("PublicIpAddress")
                    private_ip = instance.get("PrivateIpAddress")
                    if not public_ip or not private_ip:
                        raise ValueError(
                            f"{instance['InstanceId']} does not have both IP addresses"
                        )
                    nodes.append((instance["InstanceId"], public_ip, private_ip))
            if len(nodes) != expected_count:
                raise ValueError(
                    f"AWS returned {len(nodes)} instances in {region}; "
                    f"expected {expected_count}"
                )
            return sorted(nodes)

        success, nodes_by_region = self.run_by_region(
            "Step 4: Collect instance addresses", collect_region
        )
        nodes_file.parent.mkdir(parents=True, exist_ok=True)
        with nodes_file.open("w", encoding="utf-8") as nodes_output, \
                public_ips_file.open("w", encoding="utf-8") as public_output:
            for region_config in self.regions:
                region = region_config["name"]
                for instance_id, public_ip, private_ip in nodes_by_region.get(region, []):
                    nodes_output.write(
                        f"{region} {instance_id} {public_ip} {private_ip}\n"
                    )
                    public_output.write(f"{public_ip}\n")
        print(f"Node information written to {nodes_file}")
        print(f"Public IPs written to {public_ips_file}")
        return success

    def configure_protocol_network(self):
        security_group = self.config["security_group"]
        instances = self.config["instances"]
        base_port = int(self.config["benchmark"]["base_port"])
        end_port = base_port + 2_000
        group_ids = self.read_region_ids(security_group["output_file"])
        nodes_file = self.path(instances["nodes_file"])
        if not nodes_file.is_file():
            raise FileNotFoundError(
                f"{nodes_file}; run collect-addresses before network"
            )

        public_ips = []
        for line_number, line in enumerate(
            nodes_file.read_text(encoding="utf-8").splitlines(), 1
        ):
            fields = line.split()
            if not fields:
                continue
            if len(fields) != 4:
                raise ValueError(
                    f"malformed {nodes_file}:{line_number}: expected "
                    "REGION INSTANCE_ID PUBLIC_IP PRIVATE_IP"
                )
            public_ips.append(fields[2])
        public_ips = sorted(set(public_ips))
        expected_count = sum(int(item["instance_count"]) for item in self.regions)
        if len(public_ips) != expected_count:
            raise ValueError(
                f"expected {expected_count} unique public IPs, found {len(public_ips)}"
            )

        desired_cidrs = {f"{public_ip}/32" for public_ip in public_ips}

        def configure_region(region_config):
            region = region_config["name"]
            region_group_ids = group_ids.get(region, [])
            if len(region_group_ids) != 1:
                raise ValueError(f"expected one security group ID for {region}")
            group_id = region_group_ids[0]
            group = self.aws(
                region, "ec2", "describe-security-groups", "--group-ids", group_id
            )["SecurityGroups"][0]
            existing_cidrs = {
                ip_range["CidrIp"]
                for permission in group.get("IpPermissions", [])
                if permission.get("IpProtocol") == "tcp"
                and permission.get("FromPort") == base_port
                and permission.get("ToPort") == end_port
                for ip_range in permission.get("IpRanges", [])
                if "CidrIp" in ip_range
            }
            missing_cidrs = sorted(desired_cidrs - existing_cidrs)
            if not missing_cidrs:
                print(
                    f"Protocol ingress {base_port}-{end_port} already allows "
                    f"all {len(desired_cidrs)} nodes."
                )
                return group_id
            permission = json.dumps([
                {
                    "IpProtocol": "tcp",
                    "FromPort": base_port,
                    "ToPort": end_port,
                    "IpRanges": [
                        {
                            "CidrIp": cidr,
                            "Description": "Manta experiment node",
                        }
                        for cidr in missing_cidrs
                    ],
                }
            ])
            self.aws(
                region,
                "ec2",
                "authorize-security-group-ingress",
                "--group-id",
                group_id,
                "--ip-permissions",
                permission,
            )
            print(
                f"Authorized {base_port}-{end_port}/TCP for "
                f"{len(missing_cidrs)} node CIDRs."
            )
            return group_id

        success, _ = self.run_by_region(
            "Step 5: Configure inter-node protocol network", configure_region
        )
        return success

    def change_instance_power_state(self, start):
        instances = self.config["instances"]
        instances_file = self.path(instances["output_file"])
        nodes_file = self.path(instances["nodes_file"])
        public_ips_file = self.path(instances["public_ips_file"])
        instance_ids = {}

        def change_region(region_config):
            region = region_config["name"]
            expected_count = int(region_config["instance_count"])
            response = self.aws(
                region,
                "ec2",
                "describe-instances",
                "--filters",
                "Name=tag:Project,Values=MANTA",
                "Name=tag:Experiment,Values=Figure11b",
                "Name=instance-state-name,Values=pending,running,stopping,stopped",
            )
            states = {
                instance["InstanceId"]: instance["State"]["Name"]
                for reservation in response["Reservations"]
                for instance in reservation["Instances"]
            }
            ids = sorted(states)
            if len(ids) != expected_count:
                raise ValueError(
                    f"expected {expected_count} experiment instances in {region}, "
                    f"found {len(ids)}"
                )
            instance_ids[region] = ids

            if start:
                stopping_ids = [i for i in ids if states[i] == "stopping"]
                if stopping_ids:
                    self.aws(
                        region, "ec2", "wait", "instance-stopped",
                        "--instance-ids", *stopping_ids, output=None,
                    )
                stopped_ids = [
                    i for i in ids if states[i] in ("stopping", "stopped")
                ]
                if stopped_ids:
                    print(f"Starting {len(stopped_ids)} instances.")
                    self.aws(
                        region, "ec2", "start-instances",
                        "--instance-ids", *stopped_ids,
                    )
                else:
                    print("All instances are already running or pending.")
                self.aws(
                    region, "ec2", "wait", "instance-running",
                    "--instance-ids", *ids, output=None,
                )
            else:
                pending_ids = [i for i in ids if states[i] == "pending"]
                if pending_ids:
                    self.aws(
                        region, "ec2", "wait", "instance-running",
                        "--instance-ids", *pending_ids, output=None,
                    )
                running_ids = [
                    i for i in ids if states[i] in ("pending", "running")
                ]
                if running_ids:
                    print(f"Stopping {len(running_ids)} instances.")
                    self.aws(
                        region, "ec2", "stop-instances",
                        "--instance-ids", *running_ids,
                    )
                else:
                    print("All instances are already stopped or stopping.")
                self.aws(
                    region, "ec2", "wait", "instance-stopped",
                    "--instance-ids", *ids, output=None,
                )
            return ids

        action = "Start" if start else "Stop"
        success, _ = self.run_by_region(
            f"{action}: EC2 experiment instances", change_region
        )
        instances_file.parent.mkdir(parents=True, exist_ok=True)
        with instances_file.open("w", encoding="utf-8") as output:
            for region_config in self.regions:
                region = region_config["name"]
                for instance_id in instance_ids.get(region, []):
                    output.write(f"{region} {instance_id}\n")

        if not start:
            nodes_file.unlink(missing_ok=True)
            public_ips_file.unlink(missing_ok=True)
            return success
        return (
            success
            and self.collect_addresses()
            and self.configure_protocol_network()
        )

    def stop_instances(self):
        return self.change_instance_power_state(start=False)

    def start_instances(self):
        return self.change_instance_power_state(start=True)

    def destroy_instances(self):
        instances = self.config["instances"]
        instances_file = self.path(instances["output_file"])
        nodes_file = self.path(instances["nodes_file"])
        public_ips_file = self.path(instances["public_ips_file"])
        discovered_ids = {}

        def destroy_region(region_config):
            region = region_config["name"]
            response = self.aws(
                region,
                "ec2",
                "describe-instances",
                "--filters",
                "Name=tag:Project,Values=MANTA",
                "Name=tag:Experiment,Values=Figure11b",
                "Name=instance-state-name,Values=pending,running,stopping,stopped",
            )
            ids = sorted(
                instance["InstanceId"]
                for reservation in response["Reservations"]
                for instance in reservation["Instances"]
            )
            discovered_ids[region] = ids
            if not ids:
                print("No active experiment instances found.")
                return []

            print(f"Terminating {len(ids)} instances: {' '.join(ids)}")
            self.aws(
                region,
                "ec2",
                "terminate-instances",
                "--instance-ids",
                *ids,
            )
            self.aws(
                region,
                "ec2",
                "wait",
                "instance-terminated",
                "--instance-ids",
                *ids,
                output=None,
            )
            return ids

        success, terminated_ids = self.run_by_region(
            "Destroy: Terminate EC2 instances", destroy_region
        )

        remaining_ids = {
            region: ids
            for region, ids in discovered_ids.items()
            if region not in terminated_ids
        }
        instances_file.parent.mkdir(parents=True, exist_ok=True)
        with instances_file.open("w", encoding="utf-8") as output:
            for region_config in self.regions:
                region = region_config["name"]
                for instance_id in remaining_ids.get(region, []):
                    output.write(f"{region} {instance_id}\n")

        if success:
            nodes_file.unlink(missing_ok=True)
            public_ips_file.unlink(missing_ok=True)
        return success


def parse_args():
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Deploy the Manta AWS testbed")
    parser.add_argument(
        "step",
        choices=[
            "import-key",
            "security-groups",
            "provision",
            "collect-addresses",
            "network",
            "stop",
            "start",
            "destroy",
            "all",
        ],
    )
    parser.add_argument(
        "--config",
        default=str(script_dir / "config.json"),
        help="AWS deployment JSON file",
    )
    parser.add_argument("--profile", help="Optional AWS CLI profile name")
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        deployment = AwsDeployment(args.config, args.profile)
        operations = {
            "import-key": deployment.import_key,
            "security-groups": deployment.configure_security_groups,
            "provision": deployment.provision_instances,
            "collect-addresses": deployment.collect_addresses,
            "network": deployment.configure_protocol_network,
            "stop": deployment.stop_instances,
            "start": deployment.start_instances,
            "destroy": deployment.destroy_instances,
        }
        if args.step == "all":
            for step in (
                "import-key",
                "security-groups",
                "provision",
                "collect-addresses",
                "network",
            ):
                operation = operations[step]
                if not operation():
                    return 1
            return 0
        return 0 if operations[args.step]() else 1
    except (DeploymentError, OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
