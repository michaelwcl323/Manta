from datetime import datetime
from pathlib import Path
import re
import shutil
import subprocess
import sys

from benchmark.utils import PathMaker, Print


def benchmark_root():
    return Path(__file__).resolve().parent.parent


def sanitize_network_tag(network_tag):
    tag = re.sub(r"[^A-Za-z0-9._-]+", "_", str(network_tag).strip())
    return tag or "default"


def tag_results_dir(network_tag):
    tag_dir = benchmark_root() / PathMaker.results_path() / sanitize_network_tag(network_tag)
    tag_dir.mkdir(parents=True, exist_ok=True)
    return tag_dir


def run_artifact_filename(prefix, network_tag, rate, suffix="txt"):
    tag = sanitize_network_tag(network_tag)
    return f"{prefix}-{tag}-rate-{rate}.{suffix}"


def create_run_dir(network_tag, nodes, rate, run_index, total_runs, trigger_attack=None):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    attack_suffix = ""
    if trigger_attack is not None:
        attack_suffix = f"-attack-{'on' if trigger_attack else 'off'}"

    run_dir = tag_results_dir(network_tag) / (
        f"run-{timestamp}-n{nodes}-r{rate}-run{run_index + 1}-of-{total_runs}{attack_suffix}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _archive_logs(run_dir):
    source_dir = benchmark_root() / PathMaker.logs_path()
    if not source_dir.exists():
        return None

    archived_logs_dir = run_dir / PathMaker.logs_path()
    if archived_logs_dir.exists():
        shutil.rmtree(archived_logs_dir)
    shutil.copytree(source_dir, archived_logs_dir)
    return archived_logs_dir


def _run_analysis_script(script_name, output_path, logs_dir):
    script_path = benchmark_root() / script_name
    if not script_path.exists():
        Print.warn(f"{script_name} not found: {script_path}")
        return

    command = [sys.executable, str(script_path), "--out", str(output_path)]
    if logs_dir is not None:
        primary_logs = sorted(Path(logs_dir).glob("primary-*.log"))
        command.extend(str(path) for path in primary_logs)

    result = subprocess.run(
        command,
        cwd=str(benchmark_root()),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{script_name} exited with code {result.returncode}: {result.stderr.strip()}"
        )


def write_run_artifacts(
    run_dir,
    result,
    bench_parameters,
    nodes,
    rate,
    run_index,
    network_tag,
    rtt_tag,
    trigger_attack=None,
    archive_logs=True,
):
    run_dir = Path(run_dir)
    summary_path = run_dir / run_artifact_filename("summary", network_tag, rate)
    summary_text = (
        f"network_tag: {sanitize_network_tag(network_tag)}\n"
        f"rtt_tag: {rtt_tag if rtt_tag else 'N/A'}\n"
        f"run_index: {run_index + 1}/{bench_parameters.runs}\n"
        f"nodes: {nodes}\n"
        f"rate: {rate}\n"
        f"workers: {bench_parameters.workers}\n"
        f"faults: {bench_parameters.faults}\n"
        f"collocate: {bench_parameters.collocate}\n"
        f"tx_size: {bench_parameters.tx_size}\n"
        f"trigger_attack: {trigger_attack if trigger_attack is not None else 'N/A'}\n"
        "\n"
        f"{result.result()}"
    )
    summary_path.write_text(summary_text)

    logs_dir = _archive_logs(run_dir) if archive_logs else None

    stats_output = run_dir / run_artifact_filename("valence-stats", network_tag, rate)
    try:
        _run_analysis_script("extract_valence_stats.py", stats_output, logs_dir)
    except Exception as e:
        Print.warn(f"Failed to generate valence stats: {e}")

    adaptive_wait_output = run_dir / run_artifact_filename(
        "adaptive-wait-stats", network_tag, rate
    )
    try:
        _run_analysis_script("extract_adaptive_wait_stats.py", adaptive_wait_output, logs_dir)
    except Exception as e:
        Print.warn(f"Failed to generate adaptive wait stats: {e}")
