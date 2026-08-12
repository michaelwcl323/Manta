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
        if total_tps <= 0 or nodes <= 0:
            raise ValueError("total_tps and nodes must be > 0")
        
        self.total_tps = total_tps
        self.nodes = nodes
        self.theta = s

    def allocate(self) -> list[int]:
        weights = []
        for i in range(1, self.nodes + 1):
            weights.append(1.0 / math.pow(i, self.theta))
        
        sum_weights = sum(weights)
        
        raw_rates = [(self.total_tps * w / sum_weights) for w in weights]
        
        alloc = [int(r) for r in raw_rates]
        remainder = self.total_tps - sum(alloc)
        
        if remainder > 0:
            fractions = [(r - int(r)) for r in raw_rates]
            adjust_indices = sorted(range(self.nodes), key=lambda k: fractions[k], reverse=True)
            for i in range(remainder):
                alloc[adjust_indices[i]] += 1
                
        return alloc


class ExtremeAllocator:
    def __init__(self, total_tps: int, nodes: int) -> None:
        if total_tps <= 0 or nodes <= 0:
            raise ValueError("total_tps and nodes must be > 0")
        
        self.total_tps = total_tps
        self.nodes = nodes

    def allocate(self) -> list[int]:
        if self.nodes <= 1:
            return [self.total_tps]
        
        extreme_rate = int(self.total_tps * 0.99)
        remaining_rate = self.total_tps - extreme_rate
        
        remaining_share = remaining_rate // (self.nodes - 1)
        remainder = remaining_rate % (self.nodes - 1)
        
        alloc = [extreme_rate] + [remaining_share] * (self.nodes - 1)
        
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
        top_total = int(self.total_tps * 0.75)
        rest_total = self.total_tps - top_total

        top_share = top_total // top_k
        top_remainder = top_total % top_k

        alloc = [top_share] * self.nodes
        for i in range(min(top_remainder, top_k)):
            alloc[i] += 1

        if self.nodes > top_k:
            rest_nodes = self.nodes - top_k
            rest_share = rest_total // rest_nodes
            rest_remainder = rest_total % rest_nodes
            for i in range(top_k, self.nodes):
                alloc[i] = rest_share
            for i in range(rest_remainder):
                alloc[top_k + i] += 1
        else:
            alloc[0] += rest_total

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

        heavy_share = heavy_total // heavy_nodes
        heavy_remainder = heavy_total % heavy_nodes

        alloc = [0] * self.nodes
        for i in range(heavy_nodes):
            alloc[i] = heavy_share
        for i in range(heavy_remainder):
            alloc[i] += 1

        if self.nodes > heavy_nodes:
            rest_nodes = self.nodes - heavy_nodes
            rest_share = rest_total // rest_nodes
            rest_remainder = rest_total % rest_nodes
            for i in range(heavy_nodes, self.nodes):
                alloc[i] = rest_share
            for i in range(rest_remainder):
                alloc[heavy_nodes + i] += 1
        else:
            alloc[0] += rest_total

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
            return [self.total_tps]

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
            alloc[0] += rest_total

        diff = self.total_tps - sum(alloc)
        if diff != 0:
            alloc[0] += diff

        return alloc


class CustomAllocator:
    def __init__(
        self,
        base_total_tps: int,
        extra_tps: int | None,
        nodes: int,
        percentages: list[float],
    ) -> None:
        if base_total_tps < 0 or nodes <= 0:
            raise ValueError("base_total_tps must be >= 0 and nodes must be > 0")
        if extra_tps is not None and extra_tps < 0:
            raise ValueError("extra_tps must be >= 0 when provided")
        if len(percentages) != nodes:
            raise ValueError(f"percentages list must have exactly {nodes} elements")
        if any(p < 0 for p in percentages):
            raise ValueError("all percentages must be >= 0")

        total_percent = sum(percentages)
        if total_percent <= 0:
            raise ValueError("sum of percentages must be > 0")

        normalized = [p / total_percent for p in percentages]

        def allocate_weighted(total_tps: int) -> list[int]:
            raw_rates = [total_tps * p for p in normalized]
            allocated = [int(rate) for rate in raw_rates]
            remainder = total_tps - sum(allocated)
            if remainder > 0:
                fractions = [(rate - int(rate)) for rate in raw_rates]
                adjust_indices = sorted(
                    range(nodes),
                    key=lambda index: fractions[index],
                    reverse=True,
                )
                for i in range(remainder):
                    allocated[adjust_indices[i]] += 1
            return allocated

        if extra_tps is None:
            mode = "weighted_rate"
            base_node_rates = allocate_weighted(base_total_tps)
            extra_node_rates = [0] * nodes
        else:
            mode = "base_plus_extra"
            base_share = base_total_tps // nodes
            base_remainder = base_total_tps % nodes
            base_node_rates = [base_share] * nodes
            for i in range(base_remainder):
                base_node_rates[i] += 1

            extra_node_rates = allocate_weighted(extra_tps)

        self.base_total_tps = base_total_tps
        self.extra_tps = extra_tps
        self.total_tps = base_total_tps + (extra_tps or 0)
        self.nodes = nodes
        self.mode = mode
        self.percentages = normalized
        self.base_node_rates = base_node_rates
        self.extra_node_rates = extra_node_rates

    def allocate(self) -> list[int]:
        return [
            base_rate + extra_rate
            for base_rate, extra_rate in zip(self.base_node_rates, self.extra_node_rates)
        ]