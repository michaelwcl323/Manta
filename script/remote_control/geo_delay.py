#!/usr/bin/env python3
import subprocess
import sys

def get_experimental_info():
    """获取分配了 10.x.x.x IP 的网卡名称和具体的 IP 地址"""
    try:
        # 使用 ip 命令获取所有 IPv4 地址信息
        output = subprocess.check_output("ip -o -4 addr show", shell=True, text=True)
        for line in output.splitlines():
            # 寻找我们定义的实验网段 (10.x.x.x)
            if " 10." in line:
                parts = line.split()
                iface = parts[1]       # 第二列是网卡名 (例如 eth1, enp1s0f1)
                ip_cidr = parts[3]     # 第四列是 IP/掩码 (例如 10.1.1.1/8)
                ip = ip_cidr.split('/')[0]
                return iface, ip
    except Exception as e:
        print(f"[-] 获取网卡信息失败: {e}")
    return None, None

def run_commands(commands):
    """执行 Shell 命令"""
    try:
        subprocess.run(commands, shell=True, check=True, executable='/bin/bash')
        print("[+] 配置应用成功！")
    except subprocess.CalledProcessError as e:
        print(f"[-] 配置应用失败。退出码: {e.returncode}")

def main():
    print("=== 开始初始化 WAN 节点网络配置 ===")
    
    iface, ip = get_experimental_info()
    if not iface or not ip:
        print("[-] 未找到属于 10.x.x.x 网段的网卡，退出。")
        sys.exit(1)
        
    print(f"[*] 发现实验网卡: {iface}, 本机 IP: {ip}")

    # 判断当前节点角色并生成对应的配置命令
    if ip.startswith("10.1."):
        print("[*] 识别当前节点为: 欧洲 (EU) 节点")
        script = f"""
            # 路由设置
            sudo ip route add 10.2.0.0/16 via 10.4.1.1 2>/dev/null || true
            sudo ip route add 10.3.0.0/16 via 10.4.1.1 2>/dev/null || true
            
            # 清理旧的 TC 规则
            sudo tc qdisc del dev {iface} root 2>/dev/null || true
            
            # TC 队列与分类 (100Mbps 限制)
            sudo tc qdisc add dev {iface} root handle 1: htb default 10
            sudo tc class add dev {iface} parent 1: classid 1:1 htb rate 100mbit
            sudo tc class add dev {iface} parent 1: classid 1:2 htb rate 100mbit
            sudo tc class add dev {iface} parent 1: classid 1:3 htb rate 100mbit
            
            # 单向延迟应用
            sudo tc qdisc add dev {iface} parent 1:1 handle 101: netem delay 10ms   # Intra-EU
            sudo tc qdisc add dev {iface} parent 1:2 handle 102: netem delay 45ms   # EU -> NA
            sudo tc qdisc add dev {iface} parent 1:3 handle 103: netem delay 100ms  # EU -> AS
            
            # 目标 IP 过滤绑定
            sudo tc filter add dev {iface} protocol ip parent 1:0 prio 1 u32 match ip dst 10.1.0.0/16 flowid 1:1
            sudo tc filter add dev {iface} protocol ip parent 1:0 prio 1 u32 match ip dst 10.2.0.0/16 flowid 1:2
            sudo tc filter add dev {iface} protocol ip parent 1:0 prio 1 u32 match ip dst 10.3.0.0/16 flowid 1:3
        """
        
    elif ip.startswith("10.2."):
        print("[*] 识别当前节点为: 北美 (NA) 节点")
        script = f"""
            sudo ip route add 10.1.0.0/16 via 10.4.1.1 2>/dev/null || true
            sudo ip route add 10.3.0.0/16 via 10.4.1.1 2>/dev/null || true
            
            sudo tc qdisc del dev {iface} root 2>/dev/null || true
            sudo tc qdisc add dev {iface} root handle 1: htb default 10
            sudo tc class add dev {iface} parent 1: classid 1:1 htb rate 100mbit
            sudo tc class add dev {iface} parent 1: classid 1:2 htb rate 100mbit
            sudo tc class add dev {iface} parent 1: classid 1:3 htb rate 100mbit
            
            sudo tc qdisc add dev {iface} parent 1:1 handle 101: netem delay 10ms   # Intra-NA
            sudo tc qdisc add dev {iface} parent 1:2 handle 102: netem delay 45ms   # NA -> EU
            sudo tc qdisc add dev {iface} parent 1:3 handle 103: netem delay 60ms   # NA -> AS
            
            sudo tc filter add dev {iface} protocol ip parent 1:0 prio 1 u32 match ip dst 10.2.0.0/16 flowid 1:1
            sudo tc filter add dev {iface} protocol ip parent 1:0 prio 1 u32 match ip dst 10.1.0.0/16 flowid 1:2
            sudo tc filter add dev {iface} protocol ip parent 1:0 prio 1 u32 match ip dst 10.3.0.0/16 flowid 1:3
        """
        
    elif ip.startswith("10.3."):
        print("[*] 识别当前节点为: 亚洲 (AS) 节点")
        script = f"""
            sudo ip route add 10.1.0.0/16 via 10.4.1.1 2>/dev/null || true
            sudo ip route add 10.2.0.0/16 via 10.4.1.1 2>/dev/null || true
            
            sudo tc qdisc del dev {iface} root 2>/dev/null || true
            sudo tc qdisc add dev {iface} root handle 1: htb default 10
            sudo tc class add dev {iface} parent 1: classid 1:1 htb rate 100mbit
            sudo tc class add dev {iface} parent 1: classid 1:2 htb rate 100mbit
            
            sudo tc qdisc add dev {iface} parent 1:1 handle 101: netem delay 100ms  # AS -> EU
            sudo tc qdisc add dev {iface} parent 1:2 handle 102: netem delay 60ms   # AS -> NA
            
            sudo tc filter add dev {iface} protocol ip parent 1:0 prio 1 u32 match ip dst 10.1.0.0/16 flowid 1:1
            sudo tc filter add dev {iface} protocol ip parent 1:0 prio 1 u32 match ip dst 10.2.0.0/16 flowid 1:2
        """
        
    elif ip.startswith("10.4."):
        print("[*] 识别当前节点为: 路由器 (Router)")
        script = """
            sudo sysctl -w net.ipv4.ip_forward=1
        """
        
    elif ip.startswith("10.5."):
        print("[*] 识别当前节点为: 控制器 (Controller)")
        print("[+] 控制节点无需设置延迟和特殊路由，保持直连即可。")
        sys.exit(0)
        
    else:
        print(f"[-] 未知的 IP 段 ({ip})，无法应用配置。")
        sys.exit(1)

    # 运行对应的配置脚本
    run_commands(script)
    print("=== 配置完成 ===")

if __name__ == "__main__":
    main()