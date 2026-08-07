# Copyright(C) Facebook, Inc. and its affiliates.
# This file is used to generate a list of rate with zipf

# Zipf distribution constant

import math
from typing import Optional

class Zipf:
    r: object
    imax: float
    v: float
    q: float
    s: float
    oneminusq: float
    oneminusq_inv: float
    hxm: float
    hx0_minus_hxm: float

    def __init__(self, r: object, s: float, v: float, imax: int):
        if s <= 1.0 or v < 1:
            raise ValueError("s must be > 1.0 and v must be >= 1")
        self.r = r
        self.imax = float(imax)
        self.v = float(v)
        self.q = float(s)
        self.oneminusq = 1.0 - self.q
        self.oneminusq_inv = 1.0 / self.oneminusq
        self.hxm = self.h(self.imax + 0.5)
        self.hx0_minus_hxm = (
            self.h(0.5) - math.exp(math.log(self.v) * (-self.q)) - self.hxm
        )
        self.s = 1 - self.hinv(
            self.h(1.5) - math.exp(-self.q * math.log(self.v + 1.0))
        )

    def h(self, x: float) -> float:
        return math.exp(self.oneminusq * math.log(self.v + x)) * self.oneminusq_inv

    def hinv(self, x: float) -> float:
        return math.exp(self.oneminusq_inv * math.log(self.oneminusq * x)) - self.v

    def uint64(self) -> int:
        k = 0.0
        while True:
            r = self.r.random()  # r in [0, 1)
            ur = self.hxm + r * self.hx0_minus_hxm
            x = self.hinv(ur)
            k = math.floor(x + 0.5)
            if k - x <= self.s:
                break
            if ur >= self.h(k + 0.5) - math.exp(-math.log(k + self.v) * self.q):
                break
        return int(k)


def new_zipf(r: object, s: float, v: float, imax: int) -> Optional[Zipf]:
    if s <= 1.0 or v < 1:
        return None
    return Zipf(r, s, v, imax)


class ZipfAllocator:
    def __init__(self, total_tps: int, nodes: int, s: float) -> None:
        """
        :param total_tps: 总 TPS (例如 60000)
        :param nodes: 节点数 (例如 10)
        :param s: 对应 YCSB 中的 ZIPFIAN_CONSTANT (theta)，通常为 0.99
        """
        if total_tps <= 0 or nodes <= 0:
            raise ValueError("total_tps and nodes must be > 0")
        
        self.total_tps = total_tps
        self.nodes = nodes
        # 在 YCSB 源码中，theta 即 zipfianconstant
        self.theta = s

    def allocate(self) -> list[int]:
        """
        按照 YCSB ZipfianGenerator 的概率密度逻辑分配 TPS。
        结果将确保 60000 TPS 严格分配到各节点，且分布曲线与 Java 源码一致。
        """
        # YCSB 的分布逻辑：第 i 个元素的频率与 (i+1)^-theta 成正比
        # 这里我们将每个 node 视为一个 bucket
        weights = []
        for i in range(1, self.nodes + 1):
            weights.append(1.0 / math.pow(i, self.theta))
        
        sum_weights = sum(weights)
        
        # 计算每个节点应得的理论 TPS (浮点数)
        raw_rates = [(self.total_tps * w / sum_weights) for w in weights]
        
        # 转换为整数并处理舍入误差，确保总和绝对等于 total_tps
        alloc = [int(r) for r in raw_rates]
        remainder = self.total_tps - sum(alloc)
        
        if remainder > 0:
            # 按照小数部分从大到小排序，补齐缺失的 TPS (最大余数法)
            fractions = [(r - int(r)) for r in raw_rates]
            # 这里的索引顺序决定了补齐的优先级
            adjust_indices = sorted(range(self.nodes), key=lambda k: fractions[k], reverse=True)
            for i in range(remainder):
                alloc[adjust_indices[i]] += 1
                
        return alloc


class ExtremeAllocator:
    def __init__(self, total_tps: int, nodes: int) -> None:
        """
        Extreme workload allocator: first node gets 95%, remaining nodes split 5%
        
        :param total_tps: 总 TPS (例如 100000)
        :param nodes: 节点数 (例如 10)
        """
        if total_tps <= 0 or nodes <= 0:
            raise ValueError("total_tps and nodes must be > 0")
        
        self.total_tps = total_tps
        self.nodes = nodes

    def allocate(self) -> list[int]:
        """
        分配 TPS：第一个节点获得 95%，其余节点均分剩余的 5%
        结果确保总和严格等于 total_tps
        """
        if self.nodes <= 1:
            return [self.total_tps]
        
        # 第一个节点获得 95%
        extreme_rate = int(self.total_tps * 0.99)
        remaining_rate = self.total_tps - extreme_rate
        
        # 其余节点均分剩余的 5%
        remaining_share = remaining_rate // (self.nodes - 1)
        remainder = remaining_rate % (self.nodes - 1)
        
        # 构建分配列表
        alloc = [extreme_rate] + [remaining_share] * (self.nodes - 1)
        
        # 将余数分配给前几个节点（从第二个节点开始）
        for i in range(1, min(1 + remainder, self.nodes)):
            alloc[i] += 1
        
        return alloc


class ParetoAllocator:
    def __init__(self, total_tps: int, nodes: int) -> None:
        """
        Pareto (80/20) style allocator:
        - Top 3 nodes share 75% of the total TPS equally
        - Remaining nodes share the last 25% equally
        """
        if total_tps <= 0 or nodes <= 0:
            raise ValueError("total_tps and nodes must be > 0")
        self.total_tps = total_tps
        self.nodes = nodes

    def allocate(self) -> list[int]:
        if self.nodes == 1:
            return [self.total_tps]

        top_k = min(3, self.nodes)
        # 75% 给前 top_k 个节点
        top_total = int(self.total_tps * 0.75)
        rest_total = self.total_tps - top_total

        # 前 top_k 个节点平分 top_total
        top_share = top_total // top_k
        top_remainder = top_total % top_k

        alloc = [top_share] * self.nodes
        for i in range(min(top_remainder, top_k)):
            alloc[i] += 1

        # 剩余节点平分 rest_total
        if self.nodes > top_k:
            rest_nodes = self.nodes - top_k
            rest_share = rest_total // rest_nodes
            rest_remainder = rest_total % rest_nodes
            for i in range(top_k, self.nodes):
                alloc[i] = rest_share
            for i in range(rest_remainder):
                alloc[top_k + i] += 1
        else:
            # 节点数 < 3 时，全部节点只参与 75% 部分，剩下 25% 直接加到第一个节点
            alloc[0] += rest_total

        # 最后确保总和精确等于 total_tps
        diff = self.total_tps - sum(alloc)
        if diff != 0:
            alloc[0] += diff

        return alloc


class TwoHeavyAllocator:
    def __init__(self, total_tps: int, nodes: int) -> None:
        """
        Two-heavy allocator:
        - First two nodes share 70% of the total TPS (each ~35%)
        - Remaining nodes share the last 30% equally
        """
        if total_tps <= 0 or nodes <= 0:
            raise ValueError("total_tps and nodes must be > 0")
        self.total_tps = total_tps
        self.nodes = nodes

    def allocate(self) -> list[int]:
        if self.nodes == 1:
            return [self.total_tps]

        heavy_nodes = min(2, self.nodes)
        heavy_total = int(self.total_tps * 0.70)
        rest_total = self.total_tps - heavy_total

        # 前 heavy_nodes 个节点平分 heavy_total
        heavy_share = heavy_total // heavy_nodes
        heavy_remainder = heavy_total % heavy_nodes

        alloc = [0] * self.nodes
        for i in range(heavy_nodes):
            alloc[i] = heavy_share
        for i in range(heavy_remainder):
            alloc[i] += 1

        # 剩余节点平分 rest_total
        if self.nodes > heavy_nodes:
            rest_nodes = self.nodes - heavy_nodes
            rest_share = rest_total // rest_nodes
            rest_remainder = rest_total % rest_nodes
            for i in range(heavy_nodes, self.nodes):
                alloc[i] = rest_share
            for i in range(rest_remainder):
                alloc[heavy_nodes + i] += 1
        else:
            # 节点数 < 2 时，全部节点只参与 70% 部分，剩下 30% 直接加到第一个节点
            alloc[0] += rest_total

        # 确保总和精确等于 total_tps
        diff = self.total_tps - sum(alloc)
        if diff != 0:
            alloc[0] += diff

        return alloc


class ExtremeXAllocator:
    def __init__(self, total_tps: int, nodes: int, x: int) -> None:
        """
        Extreme(x) allocator:
        - First x nodes each get a fixed value 20 TPS
        - Remaining nodes share the rest equally
        """
        if total_tps <= 0 or nodes <= 0:
            raise ValueError("total_tps and nodes must be > 0")
        if x <= 0 or x > nodes:
            raise ValueError("x must be in [1, nodes]")
        if 20 * x > total_tps:
            raise ValueError("20 * x > total_tps; total_tps too small for extreme_x")

        self.total_tps = total_tps
        self.nodes = nodes
        self.x = x

    def allocate(self) -> list[int]:
        if self.nodes == 1:
            # 只有一个节点时，直接给全部 TPS
            return [self.total_tps]

        # 前 x 个节点各固定 20 TPS
        fixed_share = 20
        alloc = [0] * self.nodes
        for i in range(self.x):
            alloc[i] = fixed_share

        used = fixed_share * self.x
        rest_total = self.total_tps - used

        if self.nodes > self.x and rest_total > 0:
            rest_nodes = self.nodes - self.x
            rest_share = rest_total // rest_nodes
            remainder = rest_total % rest_nodes

            for i in range(self.x, self.nodes):
                alloc[i] = rest_share
            for i in range(remainder):
                alloc[self.x + i] += 1
        else:
            # 没有额外节点时，把剩余 TPS 加到第一个节点
            alloc[0] += rest_total

        # 最终保险：总和对齐 total_tps
        diff = self.total_tps - sum(alloc)
        if diff != 0:
            alloc[0] += diff

        return alloc


class CustomAllocator:
    def __init__(self, total_tps: int, nodes: int, percentages: list[float]) -> None:
        """
        Custom workload allocator: allocate TPS based on specified percentages
        
        :param total_tps: 总 TPS (例如 60000)
        :param nodes: 节点数 (例如 10)
        :param percentages: 每个节点的百分比列表 (例如 [0.3, 0.25, 0.2, 0.15, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0])
        """
        if total_tps <= 0 or nodes <= 0:
            raise ValueError("total_tps and nodes must be > 0")
        if len(percentages) != nodes:
            raise ValueError(f"percentages list must have exactly {nodes} elements")
        if any(p < 0 for p in percentages):
            raise ValueError("all percentages must be >= 0")

        # Normalize比例：不再严格要求 sum == 1
        total_percent = sum(percentages)
        if total_percent <= 0:
            raise ValueError("sum of percentages must be > 0")

        normalized = [p / total_percent for p in percentages]

        self.total_tps = total_tps
        self.nodes = nodes
        self.percentages = normalized

    def allocate(self) -> list[int]:
        """
        按照指定的百分比分配 TPS。
        结果确保总和严格等于 total_tps
        """
        # 计算每个节点的理论 TPS
        raw_rates = [self.total_tps * p for p in self.percentages]
        
        # 转换为整数
        alloc = [int(r) for r in raw_rates]
        remainder = self.total_tps - sum(alloc)
        
        if remainder > 0:
            # 按照小数部分从大到小排序，补齐缺失的 TPS
            fractions = [(r - int(r)) for r in raw_rates]
            adjust_indices = sorted(range(self.nodes), key=lambda k: fractions[k], reverse=True)
            for i in range(remainder):
                alloc[adjust_indices[i]] += 1
                
        return alloc