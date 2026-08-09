#!/usr/bin/env python3
"""
单文件节点脚本：放在 10.10.1.1–10.10.1.10 上即可独立配置出口时延，不依赖控制机代码。

部署（每台相同）:
  scp geo_netem_node.py user@10.10.1.X:~/
  ssh user@10.10.1.X 'chmod +x ~/geo_netem_node.py'

本机执行:
  ~/geo_netem_node.py apply
  ~/geo_netem_node.py clean
  ~/geo_netem_node.py show

远程调动（控制机循环 SSH）:
  ssh user@10.10.1.1 'python3 ~/geo_netem_node.py apply'
  或使用仓库里的 simulate_geo_netem.py 对多台批量执行同一命令。

拓扑与默认延迟写在本文件常量里；仅当需要覆盖时才设置环境变量：
  NETIF（强制网卡）、GEO_NETEM_ROUTE_PROBE（选网卡时 ip route get 的目标，默认 10.10.1.1）、
  MYIP、EU_NODES_STR、AM_NODES_STR、AS_NODES_STR、DELAY_* 等。

多网卡机器上「默认路由」网卡可能与发往 10.10.1.x 的网卡不同；本脚本优先按实验网路由选 dev，
避免把 netem 挂在错误接口上（例如规则在 eno1 而流量走 enp5s0f0）。

依赖：Python3、iproute2、无密码 sudo（或 root）。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from typing import List, Sequence

# 默认实验拓扑（与 simulate_geo_netem 一致）；拷贝单文件到各节点后无需任何配置即可 apply。
DEFAULT_NODES: List[str] = [f"10.10.1.{i}" for i in range(1, 11)]
DEFAULT_EU = DEFAULT_NODES[0:6]
DEFAULT_AM = DEFAULT_NODES[6:9]
DEFAULT_AS = DEFAULT_NODES[9:10]


def _parse_ip_list(s: str) -> List[str]:
    return [x for x in s.split() if x]


def _nodes_from_env() -> tuple[List[str], List[str], List[str]]:
    eu = _parse_ip_list(os.environ.get("EU_NODES_STR", " ".join(DEFAULT_EU)))
    am = _parse_ip_list(os.environ.get("AM_NODES_STR", " ".join(DEFAULT_AM)))
    asia = _parse_ip_list(os.environ.get("AS_NODES_STR", " ".join(DEFAULT_AS)))
    return eu, am, asia


def _delay_ms(name: str, default: str) -> str:
    return os.environ.get(name, default)


def detect_myip() -> str:
    r = subprocess.run(
        ["ip", "-4", "route", "get", "10.10.1.1"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode == 0 and r.stdout:
        m = re.search(r"\bsrc\s+(\S+)", r.stdout)
        if m:
            return m.group(1)
    r2 = subprocess.run(
        ["hostname", "-I"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r2.returncode == 0 and r2.stdout:
        for tok in r2.stdout.split():
            if re.match(r"^10\.10\.1\.(10|[1-9])$", tok):
                return tok
    print("[geo_netem_node] 无法解析本机 10.10.1.x 地址", file=sys.stderr)
    sys.exit(1)


def _dev_from_route_get(dst: str) -> str | None:
    r = subprocess.run(
        ["ip", "-4", "route", "get", dst],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0 or not r.stdout:
        return None
    parts = r.stdout.split()
    for i, p in enumerate(parts):
        if p == "dev" and i + 1 < len(parts):
            return parts[i + 1]
    return None


def detect_iface() -> str:
    netif = os.environ.get("NETIF", "").strip()
    if netif:
        return netif
    # 先发往实验网一跳：与 ping 10.10.1.x 走同一出口，避免误用「默认路由」网卡
    probe = os.environ.get("GEO_NETEM_ROUTE_PROBE", "10.10.1.1").strip() or "10.10.1.1"
    dev = _dev_from_route_get(probe)
    if dev:
        return dev
    r = subprocess.run(
        ["ip", "-4", "route", "show", "default"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode == 0 and r.stdout:
        parts = r.stdout.split()
        if len(parts) >= 5:
            return parts[4]
    return "eth0"


def region_of_ip(ip: str) -> str:
    o = ip.rsplit(".", 1)[-1]
    if not o.isdigit():
        print(f"[geo_netem_node] 非法 IP: {ip}", file=sys.stderr)
        sys.exit(1)
    n = int(o)
    if 1 <= n <= 6:
        return "eu"
    if 7 <= n <= 9:
        return "am"
    if n == 10:
        return "as"
    print(f"[geo_netem_node] 未知区域 host octet: {ip}", file=sys.stderr)
    sys.exit(1)


def run_tc(argv: Sequence[str], *, ok_fail: bool = False) -> None:
    cmd = ["tc", *argv]
    if os.geteuid() != 0 and shutil.which("sudo"):
        cmd = ["sudo", "-n", *cmd]
    r = subprocess.run(cmd, check=False)
    if r.returncode != 0 and not ok_fail:
        sys.exit(r.returncode)


def cmd_show(dev: str, myip: str) -> None:
    print(f"===== {myip} ({dev}) =====")
    run_tc(["qdisc", "show", "dev", dev], ok_fail=True)
    run_tc(["class", "show", "dev", dev], ok_fail=True)
    run_tc(["filter", "show", "dev", dev], ok_fail=True)


def cmd_clean(dev: str, myip: str) -> None:
    run_tc(["qdisc", "del", "dev", dev, "root"], ok_fail=True)
    print(f"[{myip}] cleaned tc on {dev}")


def add_filters(
    dev: str,
    target_reg: str,
    flow: str,
    myip: str,
    eu: List[str],
    am: List[str],
    asia: List[str],
) -> None:
    if target_reg == "eu":
        targets = eu
    elif target_reg == "am":
        targets = am
    elif target_reg == "as":
        targets = asia
    else:
        return
    for ip in targets:
        if ip == myip:
            continue
        run_tc(
            [
                "filter",
                "add",
                "dev",
                dev,
                "parent",
                "1:0",
                "protocol",
                "ip",
                "prio",
                "10",
                "u32",
                "match",
                "ip",
                "dst",
                ip,
                "flowid",
                flow,
            ]
        )


def cmd_apply(dev: str, myip: str) -> None:
    eu, am, asia = _nodes_from_env()
    d_eu_am = _delay_ms("DELAY_EU_TO_AM", "50")
    d_eu_as = _delay_ms("DELAY_EU_TO_AS", "80")
    d_am_eu = _delay_ms("DELAY_AM_TO_EU", "50")
    d_am_as = _delay_ms("DELAY_AM_TO_AS", "70")
    d_as_eu = _delay_ms("DELAY_AS_TO_EU", "80")
    d_as_am = _delay_ms("DELAY_AS_TO_AM", "70")

    reg = region_of_ip(myip)
    if reg == "eu":
        d1, r1, d2, r2 = d_eu_am, "am", d_eu_as, "as"
    elif reg == "am":
        d1, r1, d2, r2 = d_am_eu, "eu", d_am_as, "as"
    else:
        d1, r1, d2, r2 = d_as_eu, "eu", d_as_am, "am"

    run_tc(["qdisc", "del", "dev", dev, "root"], ok_fail=True)

    run_tc(["qdisc", "add", "dev", dev, "root", "handle", "1:", "htb", "default", "99"])
    run_tc(
        [
            "class",
            "add",
            "dev",
            dev,
            "parent",
            "1:",
            "classid",
            "1:99",
            "htb",
            "rate",
            "10gbit",
            "ceil",
            "10gbit",
        ]
    )
    run_tc(["qdisc", "add", "dev", dev, "parent", "1:99", "handle", "99:", "pfifo", "limit", "10000"])

    run_tc(
        [
            "class",
            "add",
            "dev",
            dev,
            "parent",
            "1:",
            "classid",
            "1:20",
            "htb",
            "rate",
            "10gbit",
            "ceil",
            "10gbit",
        ]
    )
    run_tc(
        [
            "qdisc",
            "add",
            "dev",
            dev,
            "parent",
            "1:20",
            "handle",
            "20:",
            "netem",
            "delay",
            f"{d1}ms",
            "2ms",
            "distribution",
            "normal",
        ]
    )

    run_tc(
        [
            "class",
            "add",
            "dev",
            dev,
            "parent",
            "1:",
            "classid",
            "1:21",
            "htb",
            "rate",
            "10gbit",
            "ceil",
            "10gbit",
        ]
    )
    run_tc(
        [
            "qdisc",
            "add",
            "dev",
            dev,
            "parent",
            "1:21",
            "handle",
            "21:",
            "netem",
            "delay",
            f"{d2}ms",
            "2ms",
            "distribution",
            "normal",
        ]
    )

    add_filters(dev, r1, "1:20", myip, eu, am, asia)
    add_filters(dev, r2, "1:21", myip, eu, am, asia)

    print(
        f"[{myip}] region={reg} dev={dev} netem ->{r1}={d1}ms ->{r2}={d2}ms (one-way egress)"
    )


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="本机 tc netem 地理时延（节点侧）")
    p.add_argument("mode", choices=("apply", "clean", "show"))
    p.add_argument(
        "--myip",
        default=None,
        metavar="ADDR",
        help="本机在实验网中的 IP；默认读环境变量 MYIP 或自动探测",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    myip = (os.environ.get("MYIP") or "").strip() or args.myip or detect_myip()
    dev = detect_iface()

    if args.mode == "clean":
        cmd_clean(dev, myip)
    elif args.mode == "show":
        cmd_show(dev, myip)
    else:
        cmd_apply(dev, myip)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
