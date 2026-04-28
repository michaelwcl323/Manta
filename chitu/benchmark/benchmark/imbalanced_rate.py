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