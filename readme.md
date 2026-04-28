
# Manta

## Introduction

This repo provides an implementation of our proposed framework Manta. It also includes implementation of some representative DAG-based BFT protocols, like Tusk, DAG-rider, Chitu and Mahi-Mahi.


## Repo Structure

In this repo, the folder is as follow

- readme.md
- tusk (include the codebase of **Tusk and DAG-rider**)
- manta (include the codebase of **Manta**)
    - manta_full: The full version of Manta
    - manta_scalable: More details can be seen in README.md under this folder
- mahi-mahi (include the codebase of **Mahi-Mahi**)
- chitu (include the codebase of **Chitu**)

## How to run each protocol

### Protocol Parameters

| Protocol | $\sigma$ | $\kappa$ | $ref$ | $cov$ | 
| --- | --- | --- | --- | --- |
| Tusk | 1 | 2 | 7 | 7 |
| DAG-Rider / Mahi-Mahi / Chitu | 1 | 3 | 7 | 7 |
| Manta | 2 | 2 | 4 | 7 |

### Quick Start

All protocols can be run after the following procedures.

1. Install Rust, tmux, and Python packages
   
   Open any protocol folders, run the shell `script/remote_control/environment_setup.sh`

2. Install Fabric

   Run the script `sudo apt install fabric`

3. Run the protocol

   Go to `<protocol folder>/benchmark` and run `fab local`. 
   
   If you run on Cloudlab, modify `cloudlab_settings.json` and run `fab cloudlab-remote`

   If you run on AWS, modify `settings.json` and run `fab remote`



## Appendix

1. Structure of cloudlab_settings.json

``` json
{
    "key": {
        "path": ""
    },
    "ssh_key_password": "",
    "port": ,
    "repo": {
        "name": "",
        "url": "",
        "branch": ""
    },
    "hosts": [
        {
            "hostname": "",
            "username": "",
            "port": 22,
            "region": ""
        }
        <depend on your machine number>
    ]
}
```

2. Structure of settings.json

```json
{
    "key": {
        "name": 
        "path": 
    },
    "port": ,
    "repo": {
        "name": 
        "url": 
        "branch": 
    },
    "instances": {
        "type": "",
        "regions": [
        ]
    },
    "network": {
        "host_ip": "private"
    },
    "hosts": [
        <ip of each machine>
    ]
}

```

3. Setting up the delay we mention in paper

```shell
"bash -lc 'set -e; SELF_IP=$(hostname -I | awk \"{for(i=1;i<=NF;i++) if (\\$i ~ /^10\\.10\\.1\\./) {print \\$i; exit}}\" ); MY_IP_LAST=$(echo \"$SELF_IP\" | cut -d. -f4); if [ -z \"$SELF_IP\" ] || [ -z \"$MY_IP_LAST\" ]; then echo \"ERROR: failed to detect SELF_IP\"; hostname -I; exit 1; fi; if [ \"$MY_IP_LAST\" = \"1\" ]; then TARGET=10.10.1.2; else TARGET=10.10.1.1; fi; IFACE=$(ip route get $TARGET | awk \"{for (i=1;i<=NF;i++) if (\\$i==\\\"dev\\\") {print \\$(i+1); exit}}\" ); SRC_IP=$(ip route get $TARGET | awk \"{for (i=1;i<=NF;i++) if (\\$i==\\\"src\\\") {print \\$(i+1); exit}}\" ); if [ -z \"$IFACE\" ] || [ -z \"$SRC_IP\" ]; then echo \"ERROR: failed to detect IFACE/SRC_IP\"; ip route get $TARGET || true; exit 1; fi; echo \"Using IFACE=$IFACE SELF_IP=$SELF_IP SRC_IP=$SRC_IP NODE_ID=$MY_IP_LAST TARGET=$TARGET\"; sudo tc qdisc del dev \"$IFACE\" root 2>/dev/null || true; sudo tc qdisc add dev \"$IFACE\" root handle 1: prio bands 4 priomap 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0; sudo tc qdisc add dev \"$IFACE\" parent 1:2 handle 20: netem delay 51.3ms 1ms; sudo tc qdisc add dev \"$IFACE\" parent 1:3 handle 30: netem delay 76.6ms 1.5ms; sudo tc qdisc add dev \"$IFACE\" parent 1:4 handle 40: netem delay 117.15ms 2.5ms; if [ \"$MY_IP_LAST\" -ge 1 ] && [ \"$MY_IP_LAST\" -le 6 ]; then for i in 7 8 9; do sudo tc filter add dev \"$IFACE\" protocol ip parent 1:0 prio 1 u32 match ip dst 10.10.1.$i/32 flowid 1:2; done; sudo tc filter add dev \"$IFACE\" protocol ip parent 1:0 prio 1 u32 match ip dst 10.10.1.10/32 flowid 1:4; echo \"Configured EU rules\"; elif [ \"$MY_IP_LAST\" -ge 7 ] && [ \"$MY_IP_LAST\" -le 9 ]; then for i in 1 2 3 4 5 6; do sudo tc filter add dev \"$IFACE\" protocol ip parent 1:0 prio 1 u32 match ip dst 10.10.1.$i/32 flowid 1:2; done; sudo tc filter add dev \"$IFACE\" protocol ip parent 1:0 prio 1 u32 match ip dst 10.10.1.10/32 flowid 1:3; echo \"Configured NA rules\"; elif [ \"$MY_IP_LAST\" -eq 10 ]; then for i in 1 2 3 4 5 6; do sudo tc filter add dev \"$IFACE\" protocol ip parent 1:0 prio 1 u32 match ip dst 10.10.1.$i/32 flowid 1:4; done; for i in 7 8 9; do sudo tc filter add dev \"$IFACE\" protocol ip parent 1:0 prio 1 u32 match ip dst 10.10.1.$i/32 flowid 1:3; done; echo \"Configured AS rules\"; else echo \"WARNING: unexpected node id $MY_IP_LAST, no filters installed\"; fi; echo \"=== qdisc ===\"; sudo tc qdisc show dev \"$IFACE\"; echo \"=== filter ===\"; sudo tc filter show dev \"$IFACE\"'"
```