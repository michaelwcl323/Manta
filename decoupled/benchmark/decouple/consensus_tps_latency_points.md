# Decouple Consensus TPS-Latency Points

This document lists the aggregated consensus TPS-latency points used by the per-network comparison plots.

Aggregation rule:
- For each network/workload/offered-load combination, use the latest timestamp batch only.
- If multiple runs exist in that batch, report the mean and standard deviation across runs.

## 80ms

### balanced

| Offered Load (tx/s) | Runs Used | Mean Consensus TPS (tx/s) | Std Consensus TPS | Mean Consensus Latency (ms) | Std Consensus Latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| 20,000 | 2 | 18,507.5 | 109.6 | 996 | 5.7 |
| 40,000 | 2 | 35,746.5 | 888.8 | 769.5 | 50.2 |
| 60,000 | 2 | 54,152 | 1,757.9 | 779.5 | 51.6 |
| 80,000 | 2 | 74,005 | 516.2 | 748.5 | 24.7 |
| 100,000 | 2 | 91,567 | 712.8 | 764 | 53.7 |
| 120,000 | 2 | 83,053 | 155.6 | 3,822 | 861.3 |
| 140,000 | 2 | 73,462 | 2,327.8 | 9,844.5 | 3,150.2 |

### custom-high-3

| Offered Load (tx/s) | Runs Used | Mean Consensus TPS (tx/s) | Std Consensus TPS | Mean Consensus Latency (ms) | Std Consensus Latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| 20,000 | 2 | 17,799.5 | 1,120.8 | 927.5 | 14.8 |
| 40,000 | 2 | 35,842 | 1,323.7 | 798 | 26.9 |
| 60,000 | 2 | 54,287 | 742.5 | 780 | 25.5 |
| 80,000 | 2 | 75,198 | 1,541.5 | 731.5 | 7.8 |
| 100,000 | 2 | 93,143.5 | 222.7 | 739.5 | 3.5 |
| 120,000 | 2 | 84,593 | 2,968.4 | 2,408.5 | 804.0 |
| 140,000 | 2 | 69,249.5 | 690.8 | 12,164.5 | 3,011.6 |

### custom-high-5

| Offered Load (tx/s) | Runs Used | Mean Consensus TPS (tx/s) | Std Consensus TPS | Mean Consensus Latency (ms) | Std Consensus Latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| 20,000 | 2 | 19,767.5 | 3.5 | 1,278.5 | 9.2 |
| 40,000 | 2 | 39,510.5 | 46.0 | 1,277.5 | 0.7 |
| 60,000 | 2 | 59,514.5 | 79.9 | 1,285.5 | 3.5 |
| 80,000 | 2 | 78,972.5 | 120.9 | 1,290.5 | 7.8 |
| 100,000 | 2 | 98,705.5 | 4.9 | 1,304.5 | 3.5 |
| 120,000 | 2 | 111,159 | 2,955.7 | 2,196 | 975.8 |
| 140,000 | 2 | 82,459 | 422.8 | 13,622 | 2,954.3 |

## geo

### balanced

| Offered Load (tx/s) | Runs Used | Mean Consensus TPS (tx/s) | Std Consensus TPS | Mean Consensus Latency (ms) | Std Consensus Latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| 20,000 | 2 | 22,778.5 | 7,022.3 | 1,487.5 | 14.8 |
| 40,000 | 2 | 35,538 | 46.7 | 1,849 | 4.2 |
| 60,000 | 2 | 53,347.5 | 119.5 | 1,472 | 15.6 |
| 80,000 | 2 | 70,953.5 | 236.9 | 1,316.5 | 6.4 |
| 100,000 | 2 | 88,579 | 172.5 | 1,502.5 | 10.6 |
| 120,000 | 2 | 98,272.5 | 5,248.9 | 1,692 | 76.4 |
| 140,000 | 2 | 79,361 | 3,894.7 | 8,389 | 4,365.7 |

### custom-high-3

| Offered Load (tx/s) | Runs Used | Mean Consensus TPS (tx/s) | Std Consensus TPS | Mean Consensus Latency (ms) | Std Consensus Latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| 20,000 | 2 | 16,758.5 | 30.4 | 1,479.5 | 3.5 |
| 40,000 | 2 | 33,525 | 19.8 | 1,484 | 7.1 |
| 60,000 | 2 | 50,184 | 75.0 | 1,715 | 2.8 |
| 80,000 | 2 | 66,636 | 410.1 | 1,572 | 29.7 |
| 100,000 | 2 | 83,344 | 323.9 | 1,402.5 | 4.9 |
| 120,000 | 2 | 99,816.5 | 458.9 | 1,499 | 9.9 |
| 140,000 | 2 | 82,458.5 | 1,849.1 | 9,751.5 | 1,590.3 |

### custom-high-5

| Offered Load (tx/s) | Runs Used | Mean Consensus TPS (tx/s) | Std Consensus TPS | Mean Consensus Latency (ms) | Std Consensus Latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| 20,000 | 2 | 16,450.5 | 50.2 | 1,604.5 | 26.2 |
| 40,000 | 2 | 32,865.5 | 62.9 | 1,568 | 21.2 |
| 60,000 | 2 | 49,360 | 18.4 | 1,707 | 22.6 |
| 80,000 | 2 | 65,404 | 162.6 | 1,397 | 18.4 |
| 100,000 | 2 | 81,413 | 125.9 | 1,420.5 | 9.2 |
| 120,000 | 2 | 97,997 | 230.5 | 1,613 | 32.5 |
| 140,000 | 2 | 82,581 | 2,568.2 | 8,896.5 | 1,618.6 |

## geo_uniform

### balanced

| Offered Load (tx/s) | Runs Used | Mean Consensus TPS (tx/s) | Std Consensus TPS | Mean Consensus Latency (ms) | Std Consensus Latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| 20,000 | 1 | 13,898 | 0 | 1,659 | 0 |
| 40,000 | 2 | 27,814 | 9.9 | 1,651 | 12.7 |
| 60,000 | 2 | 41,869 | 216.4 | 1,670 | 7.1 |
| 80,000 | 2 | 57,466.5 | 539.5 | 1,837 | 131.5 |
| 100,000 | 2 | 70,079 | 322.4 | 2,301 | 12.7 |
| 120,000 | 2 | 84,255 | 357.8 | 3,277.5 | 275.1 |
| 140,000 | 2 | 75,016 | 936.2 | 15,410.5 | 1,931.1 |

### custom-high-3

| Offered Load (tx/s) | Runs Used | Mean Consensus TPS (tx/s) | Std Consensus TPS | Mean Consensus Latency (ms) | Std Consensus Latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| 20,000 | 2 | 11,012 | 130.1 | 1,915.5 | 10.6 |
| 40,000 | 2 | 21,788.5 | 27.6 | 1,542.5 | 0.7 |
| 60,000 | 2 | 34,552 | 1,296.8 | 1,901 | 267.3 |
| 80,000 | 2 | 45,435.5 | 1,567.7 | 2,213.5 | 212.8 |
| 100,000 | 2 | 55,250 | 725.5 | 1,695.5 | 17.7 |
| 120,000 | 2 | 81,256.5 | 3,563.1 | 3,048.5 | 19.1 |
| 140,000 | 2 | 73,178 | 1,265.7 | 15,458.5 | 4,643.6 |

### custom-high-5

| Offered Load (tx/s) | Runs Used | Mean Consensus TPS (tx/s) | Std Consensus TPS | Mean Consensus Latency (ms) | Std Consensus Latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| 20,000 | 2 | 10,083 | 5.7 | 1,660.5 | 0.7 |
| 40,000 | 2 | 20,193.5 | 34.6 | 1,666 | 0 |
| 60,000 | 2 | 30,955 | 950.4 | 1,846.5 | 72.8 |
| 80,000 | 2 | 42,081.5 | 156.3 | 1,738 | 12.7 |
| 100,000 | 2 | 52,078 | 428.5 | 1,846 | 4.2 |
| 120,000 | 2 | 82,829 | 7,922.4 | 2,918.5 | 525.4 |
| 140,000 | 2 | 73,507 | 158.4 | 15,169.5 | 7,397.0 |

## no-delay

Missing workloads in current data: custom-high-3, custom-high-5.

### balanced

| Offered Load (tx/s) | Runs Used | Mean Consensus TPS (tx/s) | Std Consensus TPS | Mean Consensus Latency (ms) | Std Consensus Latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| 20,000 | 2 | 17,100.5 | 1,171.7 | 838.5 | 85.6 |
| 40,000 | 2 | 35,146.5 | 292.0 | 909 | 11.3 |
| 60,000 | 2 | 54,295 | 1,702.7 | 774 | 4.2 |
| 80,000 | 2 | 74,230 | 175.4 | 756.5 | 4.9 |
| 100,000 | 2 | 92,436 | 2,061.9 | 738.5 | 21.9 |
| 120,000 | 2 | 77,719.5 | 1,764.2 | 5,753 | 114.6 |
| 140,000 | 2 | 68,052 | 944.7 | 17,095 | 2,979.7 |
