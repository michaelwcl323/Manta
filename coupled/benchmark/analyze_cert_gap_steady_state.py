#!/usr/bin/env python3
"""
Analyze steady-state windows where the cert-k gap is largest.

For each observer node:
1. Read `round_certificate_analysis.csv`
2. Sort certificate arrival times within every round
3. Extract the chosen arrival ranks (default: cert4 and cert7)
4. Slide a window over rounds
5. Keep windows that look steady-state according to configurable thresholds
6. Rank windows by the average cert7-cert4 gap

Outputs:
- a ranked CSV with all window scores
- a text report with the best global window and per-node best windows
"""

import csv
import math
import re
from pathlib import Path
from statistics import mean, median, pstdev


CERT_TIME_PATTERN = re.compile(r"Certificate_(\d+)_Time_Delta_ms$")

# ============================================================================
# Configuration: edit these values directly for a different analysis.
# ============================================================================
CSV_PATH = (
    "results/no_delay/custom5_5/"
    "20260328_063819_n10_r40000_run2/"
    "no_delay_custom5_5_round_certificate_analysis.csv"
)

START_ROUND = None
END_ROUND = None
NODE_IDS = None

CERT_LOW_RANK = 4
CERT_HIGH_RANK = 7

WINDOW_SIZE = 50
WINDOW_STEP = 1
TOP_K = 10

MIN_VALID_RATIO = 0.90
MAX_CERT_LOW_MAX_JUMP_MS = 250.0
MAX_CERT_HIGH_MAX_JUMP_MS = 250.0
MAX_GAP_MAX_JUMP_MS = 120.0
MAX_GAP_P95_JUMP_MS = 80.0
MAX_GAP_DRIFT_RATIO = 0.20

OUTPUT_DIR = None


def _resolve_csv_path(csv_path):
    if csv_path:
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {path}")
        return path

    candidates = sorted(
        Path("results").glob("**/*_round_certificate_analysis*.csv"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            "No round_certificate_analysis CSV files found under results/."
        )
    return candidates[0]


def _to_int(value):
    if value in (None, "", "UNKNOWN"):
        return None
    return int(value)


def _to_float(value):
    if value in (None, "", "UNKNOWN"):
        return None
    return float(value)


def _discover_certificate_columns(fieldnames):
    pairs = []
    for field in fieldnames:
        match = CERT_TIME_PATTERN.match(field)
        if match:
            pairs.append((int(match.group(1)), field))
    return [field for _, field in sorted(pairs)]


def _load_rows(csv_path):
    with open(csv_path, "r", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    cert_columns = _discover_certificate_columns(fieldnames)
    if not cert_columns:
        raise ValueError("No certificate time columns found in the CSV.")
    return rows, cert_columns


def _sorted_certificate_values(row, cert_columns):
    values = [_to_float(row.get(column)) for column in cert_columns]
    values = [value for value in values if value is not None]
    return sorted(values)


def _filter_rows(rows, node_ids=None, start_round=None, end_round=None):
    allowed_nodes = set(node_ids) if node_ids is not None else None
    filtered = []

    for row in rows:
        node_id = _to_int(row.get("Node_ID"))
        round_id = _to_int(row.get("Round"))
        if node_id is None or round_id is None:
            continue
        if allowed_nodes is not None and node_id not in allowed_nodes:
            continue
        if start_round is not None and round_id < start_round:
            continue
        if end_round is not None and round_id > end_round:
            continue
        filtered.append(row)

    return filtered


def _group_rows_by_node(rows):
    grouped = {}
    for row in rows:
        node_id = _to_int(row.get("Node_ID"))
        if node_id is None:
            continue
        grouped.setdefault(node_id, []).append(row)

    for node_rows in grouped.values():
        node_rows.sort(key=lambda row: _to_int(row.get("Round")) or -1)
    return grouped


def _cv(values):
    if not values:
        return math.inf
    avg = mean(values)
    if abs(avg) < 1e-12:
        return math.inf
    return pstdev(values) / abs(avg)


def _drift_ratio(values):
    if len(values) < 4:
        return 0.0

    midpoint = len(values) // 2
    first_half = values[:midpoint]
    second_half = values[midpoint:]
    if not first_half or not second_half:
        return 0.0

    first_mean = mean(first_half)
    second_mean = mean(second_half)
    denominator = max(abs(mean(values)), 1e-12)
    return abs(second_mean - first_mean) / denominator


def _adjacent_jump_stats(values):
    if len(values) < 2:
        return {
            "max_jump_abs": 0.0,
            "max_jump_ratio": 0.0,
            "p95_jump_abs": 0.0,
            "p95_jump_ratio": 0.0,
        }

    diffs = [abs(values[index] - values[index - 1]) for index in range(1, len(values))]
    base = max(abs(mean(values)), 1e-12)
    diff_ratios = [diff / base for diff in diffs]
    ordered_diffs = sorted(diffs)
    ordered_ratios = sorted(diff_ratios)
    p95_index = min(len(ordered_diffs) - 1, math.ceil(len(ordered_diffs) * 0.95) - 1)

    return {
        "max_jump_abs": max(diffs),
        "max_jump_ratio": max(diff_ratios),
        "p95_jump_abs": ordered_diffs[p95_index],
        "p95_jump_ratio": ordered_ratios[p95_index],
    }


def _build_round_metric(row, cert_columns, low_rank, high_rank):
    sorted_values = _sorted_certificate_values(row, cert_columns)
    if len(sorted_values) < high_rank:
        return None

    low_value = sorted_values[low_rank - 1]
    high_value = sorted_values[high_rank - 1]
    return {
        "node_id": _to_int(row.get("Node_ID")),
        "round": _to_int(row.get("Round")),
        "cert_low": low_value,
        "cert_high": high_value,
        "gap": high_value - low_value,
        "round_end": _to_float(row.get("Round_End_Time_ms")),
        "certificate_count": _to_int(row.get("Certificate_Count")) or 0,
    }


def _window_metrics(node_id, window_rows, cert_columns, low_rank, high_rank):
    round_metrics = [
        _build_round_metric(row, cert_columns, low_rank, high_rank)
        for row in window_rows
    ]
    valid_metrics = [item for item in round_metrics if item is not None]
    row_count = len(window_rows)
    valid_ratio = (len(valid_metrics) / row_count) if row_count else 0.0

    if not valid_metrics:
        return {
            "node_id": node_id,
            "start_round": _to_int(window_rows[0].get("Round")),
            "end_round": _to_int(window_rows[-1].get("Round")),
            "row_count": row_count,
            "valid_rows": 0,
            "valid_ratio": valid_ratio,
            "cert_low_rank": low_rank,
            "cert_high_rank": high_rank,
            "steady_state": False,
            "cert_low_mean_ms": None,
            "cert_high_mean_ms": None,
            "gap_mean_ms": None,
            "cert_low_cv": None,
            "cert_high_cv": None,
            "gap_cv": None,
            "gap_drift_ratio": None,
        }

    low_values = [item["cert_low"] for item in valid_metrics]
    high_values = [item["cert_high"] for item in valid_metrics]
    gap_values = [item["gap"] for item in valid_metrics]
    round_end_values = [
        item["round_end"] for item in valid_metrics if item["round_end"] is not None
    ]

    cert_low_mean = mean(low_values)
    cert_high_mean = mean(high_values)
    gap_mean = mean(gap_values)
    cert_low_cv = _cv(low_values)
    cert_high_cv = _cv(high_values)
    gap_cv = _cv(gap_values)
    gap_drift = _drift_ratio(gap_values)
    cert_low_jump = _adjacent_jump_stats(low_values)
    cert_high_jump = _adjacent_jump_stats(high_values)
    gap_jump = _adjacent_jump_stats(gap_values)

    steady_state = (
        valid_ratio >= MIN_VALID_RATIO
        and cert_low_jump["max_jump_abs"] <= MAX_CERT_LOW_MAX_JUMP_MS
        and cert_high_jump["max_jump_abs"] <= MAX_CERT_HIGH_MAX_JUMP_MS
        and gap_jump["max_jump_abs"] <= MAX_GAP_MAX_JUMP_MS
        and gap_jump["p95_jump_abs"] <= MAX_GAP_P95_JUMP_MS
        and gap_drift <= MAX_GAP_DRIFT_RATIO
    )

    return {
        "node_id": node_id,
        "start_round": _to_int(window_rows[0].get("Round")),
        "end_round": _to_int(window_rows[-1].get("Round")),
        "row_count": row_count,
        "valid_rows": len(valid_metrics),
        "valid_ratio": valid_ratio,
        "cert_low_rank": low_rank,
        "cert_high_rank": high_rank,
        "steady_state": steady_state,
        "cert_low_mean_ms": cert_low_mean,
        "cert_low_median_ms": median(low_values),
        "cert_high_mean_ms": cert_high_mean,
        "cert_high_median_ms": median(high_values),
        "gap_mean_ms": gap_mean,
        "gap_median_ms": median(gap_values),
        "cert_low_cv": cert_low_cv,
        "cert_high_cv": cert_high_cv,
        "gap_cv": gap_cv,
        "cert_low_max_jump_ms": cert_low_jump["max_jump_abs"],
        "cert_low_max_jump_ratio": cert_low_jump["max_jump_ratio"],
        "cert_high_max_jump_ms": cert_high_jump["max_jump_abs"],
        "cert_high_max_jump_ratio": cert_high_jump["max_jump_ratio"],
        "gap_max_jump_ms": gap_jump["max_jump_abs"],
        "gap_max_jump_ratio": gap_jump["max_jump_ratio"],
        "gap_p95_jump_ms": gap_jump["p95_jump_abs"],
        "gap_p95_jump_ratio": gap_jump["p95_jump_ratio"],
        "gap_drift_ratio": gap_drift,
        "round_end_mean_ms": mean(round_end_values) if round_end_values else None,
        "first_valid_round": min(item["round"] for item in valid_metrics),
        "last_valid_round": max(item["round"] for item in valid_metrics),
    }


def _scan_windows_for_node(node_id, node_rows, cert_columns, low_rank, high_rank):
    if len(node_rows) < WINDOW_SIZE:
        return []

    windows = []
    for start_index in range(0, len(node_rows) - WINDOW_SIZE + 1, WINDOW_STEP):
        window_rows = node_rows[start_index:start_index + WINDOW_SIZE]
        windows.append(
            _window_metrics(
                node_id,
                window_rows,
                cert_columns,
                low_rank,
                high_rank,
            )
        )
    return windows


def _rank_windows(windows):
    return sorted(
        windows,
        key=lambda item: (
            0 if item["steady_state"] else 1,
            -(item["gap_mean_ms"] or -1e18),
            item["gap_max_jump_ratio"] if item["gap_max_jump_ratio"] is not None else math.inf,
            item["start_round"],
        ),
    )


def _format_metric(value, decimals=3):
    if value is None:
        return "n/a"
    return f"{value:.{decimals}f}"


def _write_windows_csv(output_path, windows):
    fieldnames = [
        "node_id",
        "start_round",
        "end_round",
        "first_valid_round",
        "last_valid_round",
        "row_count",
        "valid_rows",
        "valid_ratio",
        "steady_state",
        "cert_low_rank",
        "cert_high_rank",
        "cert_low_mean_ms",
        "cert_low_median_ms",
        "cert_high_mean_ms",
        "cert_high_median_ms",
        "gap_mean_ms",
        "gap_median_ms",
        "cert_low_cv",
        "cert_high_cv",
        "gap_cv",
        "cert_low_max_jump_ms",
        "cert_low_max_jump_ratio",
        "cert_high_max_jump_ms",
        "cert_high_max_jump_ratio",
        "gap_max_jump_ms",
        "gap_max_jump_ratio",
        "gap_p95_jump_ms",
        "gap_p95_jump_ratio",
        "gap_drift_ratio",
        "round_end_mean_ms",
    ]

    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for window in windows:
            writer.writerow(window)


def _build_report_text(
    csv_path,
    ranked_windows,
    steady_windows,
    per_node_best,
    low_rank,
    high_rank,
):
    lines = [
        "Certificate gap steady-state analysis",
        "=" * 80,
        f"CSV: {csv_path}",
        f"Arrival ranks compared: cert{low_rank} vs cert{high_rank}",
        f"Sliding window: {WINDOW_SIZE} rounds, step={WINDOW_STEP}",
        (
            "Steady-state thresholds: "
            f"valid_ratio>={MIN_VALID_RATIO:.2f}, "
            f"cert{low_rank}_max_jump<={MAX_CERT_LOW_MAX_JUMP_MS:.0f} ms, "
            f"cert{high_rank}_max_jump<={MAX_CERT_HIGH_MAX_JUMP_MS:.0f} ms, "
            f"gap_max_jump<={MAX_GAP_MAX_JUMP_MS:.0f} ms, "
            f"gap_p95_jump<={MAX_GAP_P95_JUMP_MS:.0f} ms, "
            f"gap_drift<={MAX_GAP_DRIFT_RATIO:.2f}"
        ),
        "Interpretation: a steady-state window should not contain sudden per-round jumps.",
        "",
    ]

    if steady_windows:
        best = steady_windows[0]
        lines.extend(
            [
                "Best steady-state window overall",
                "-" * 80,
                f"Observer node: {best['node_id']}",
                f"Rounds: {best['start_round']}-{best['end_round']}",
                (
                    f"Gap mean: {_format_metric(best['gap_mean_ms'])} ms "
                    f"(cert{high_rank} - cert{low_rank})"
                ),
                (
                    f"cert{low_rank} mean: {_format_metric(best['cert_low_mean_ms'])} ms, "
                    f"cert{high_rank} mean: {_format_metric(best['cert_high_mean_ms'])} ms"
                ),
                (
                    f"valid_ratio={_format_metric(best['valid_ratio'])}, "
                    f"gap_max_jump={_format_metric(best['gap_max_jump_ms'])} ms, "
                    f"gap_p95_jump={_format_metric(best['gap_p95_jump_ms'])} ms, "
                    f"gap_drift={_format_metric(best['gap_drift_ratio'])}"
                ),
                "",
                f"Top {min(TOP_K, len(steady_windows))} steady-state windows",
                "-" * 80,
            ]
        )

        for index, item in enumerate(steady_windows[:TOP_K], start=1):
            lines.append(
                (
                    f"{index}. node {item['node_id']} | rounds {item['start_round']}-{item['end_round']} | "
                    f"gap_mean={_format_metric(item['gap_mean_ms'])} ms | "
                    f"cert{low_rank}={_format_metric(item['cert_low_mean_ms'])} ms | "
                    f"cert{high_rank}={_format_metric(item['cert_high_mean_ms'])} ms | "
                    f"gap_max_jump={_format_metric(item['gap_max_jump_ms'])} ms"
                )
            )
        lines.append("")
    else:
        lines.extend(
            [
                "No windows satisfied the current steady-state thresholds.",
                "",
                f"Top {min(TOP_K, len(ranked_windows))} windows overall (including non-steady windows)",
                "-" * 80,
            ]
        )
        for index, item in enumerate(ranked_windows[:TOP_K], start=1):
            lines.append(
                (
                    f"{index}. node {item['node_id']} | rounds {item['start_round']}-{item['end_round']} | "
                    f"steady={item['steady_state']} | "
                    f"gap_mean={_format_metric(item['gap_mean_ms'])} ms | "
                    f"gap_max_jump={_format_metric(item['gap_max_jump_ms'])} ms | "
                    f"gap_p95_jump={_format_metric(item['gap_p95_jump_ms'])} ms | "
                    f"gap_drift={_format_metric(item['gap_drift_ratio'])}"
                )
            )
        lines.append("")

    lines.extend(
        [
            "Best steady-state window by observer node",
            "-" * 80,
        ]
    )
    for node_id in sorted(per_node_best):
        item = per_node_best[node_id]
        if item is None:
            lines.append(f"node {node_id}: no steady-state window matched the thresholds")
            continue
        lines.append(
            (
                f"node {node_id}: rounds {item['start_round']}-{item['end_round']} | "
                f"gap_mean={_format_metric(item['gap_mean_ms'])} ms | "
                f"cert{low_rank}={_format_metric(item['cert_low_mean_ms'])} ms | "
                f"cert{high_rank}={_format_metric(item['cert_high_mean_ms'])} ms | "
                f"gap_max_jump={_format_metric(item['gap_max_jump_ms'])} ms"
            )
        )

    lines.append("")
    return "\n".join(lines) + "\n"


def analyze_cert_gap_steady_state(
    csv_path,
    low_rank=4,
    high_rank=7,
    start_round=None,
    end_round=None,
    node_ids=None,
    output_dir=None,
):
    if low_rank <= 0 or high_rank <= 0:
        raise ValueError("certificate ranks must be positive")
    if low_rank >= high_rank:
        raise ValueError("low_rank must be smaller than high_rank")
    if WINDOW_SIZE <= 0 or WINDOW_STEP <= 0:
        raise ValueError("WINDOW_SIZE and WINDOW_STEP must be positive")

    csv_path = _resolve_csv_path(csv_path)
    rows, cert_columns = _load_rows(csv_path)
    filtered_rows = _filter_rows(
        rows,
        node_ids=node_ids,
        start_round=start_round,
        end_round=end_round,
    )
    if not filtered_rows:
        raise ValueError("No rows left after applying the selected filters.")

    grouped = _group_rows_by_node(filtered_rows)
    if not grouped:
        raise ValueError("No observer nodes were found in the filtered rows.")

    all_windows = []
    for node_id, node_rows in grouped.items():
        all_windows.extend(
            _scan_windows_for_node(
                node_id,
                node_rows,
                cert_columns,
                low_rank,
                high_rank,
            )
        )

    if not all_windows:
        raise ValueError(
            f"No sliding windows available. Try reducing WINDOW_SIZE (current={WINDOW_SIZE})."
        )

    ranked_windows = _rank_windows(all_windows)
    steady_windows = [item for item in ranked_windows if item["steady_state"]]

    per_node_best = {}
    for node_id in grouped:
        node_steady = [
            item for item in steady_windows
            if item["node_id"] == node_id
        ]
        per_node_best[node_id] = node_steady[0] if node_steady else None

    output_dir = Path(output_dir) if output_dir else csv_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"cert{low_rank}_cert{high_rank}"
    windows_csv_path = output_dir / f"{prefix}_steady_state_window_scores.csv"
    report_path = output_dir / f"{prefix}_steady_state_report.txt"

    _write_windows_csv(windows_csv_path, ranked_windows)
    report_text = _build_report_text(
        csv_path,
        ranked_windows,
        steady_windows,
        per_node_best,
        low_rank,
        high_rank,
    )
    report_path.write_text(report_text)

    return {
        "csv_path": csv_path,
        "windows_csv_path": windows_csv_path,
        "report_path": report_path,
        "best_steady_window": steady_windows[0] if steady_windows else None,
        "per_node_best": per_node_best,
        "steady_window_count": len(steady_windows),
        "total_window_count": len(ranked_windows),
    }


def main():
    result = analyze_cert_gap_steady_state(
        CSV_PATH,
        low_rank=CERT_LOW_RANK,
        high_rank=CERT_HIGH_RANK,
        start_round=START_ROUND,
        end_round=END_ROUND,
        node_ids=NODE_IDS,
        output_dir=OUTPUT_DIR,
    )

    print(f"Selected CSV: {result['csv_path']}")
    print(f"Saved window scores to: {result['windows_csv_path']}")
    print(f"Saved report to: {result['report_path']}")
    print(
        f"Steady-state windows: {result['steady_window_count']} / "
        f"{result['total_window_count']}"
    )
    best = result["best_steady_window"]
    if best is None:
        print("No steady-state window satisfied the current thresholds.")
    else:
        print(
            "Best steady-state window: "
            f"node {best['node_id']}, rounds {best['start_round']}-{best['end_round']}, "
            f"gap_mean={best['gap_mean_ms']:.3f} ms"
        )


if __name__ == "__main__":
    main()
