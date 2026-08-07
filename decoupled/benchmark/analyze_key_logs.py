import argparse
import csv
import statistics
import subprocess
from pathlib import Path
import re
from typing import Dict, List, Optional, Tuple

CREATED_HEADER_RE = re.compile(r"Created ([^ ]+):")
CREATED_HEADER_BATCH_RE = re.compile(r"Created (B\d+\([^ ]+\)) -> ([^ ]+=)")
HEADER_DEBUG_RE = re.compile(r"Created ([^ ]+): B(\d+)\(([^,]+),")
ASSEMBLED_HEADER_RE = re.compile(r"header's id is ([^ ]+)")
ASSEMBLED_CERT_RE = re.compile(r"Assembled ([^ ]+):")
RECEIVED_CERT_RE = re.compile(r"digest: ([^ ]+)")

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(Path(__file__).resolve().parent / "key_logs.csv"))
    parser.add_argument("--run-extract", action="store_true", help="Run extract_key_logs.py first")
    parser.add_argument("--details-out", default=None, help="Write per-match CSV files to this directory")
    return parser.parse_args()

def summary_stats(values: List[float]) -> str:
    if not values:
        return "count=0"
    values = sorted(values)
    def pct(p):
        idx = int(round((p / 100) * (len(values) - 1)))
        return values[idx]
    return (
        f"count={len(values)}, "
        f"min={values[0]:.3f}ms, "
        f"p50={pct(50):.3f}ms, "
        f"p95={pct(95):.3f}ms, "
        f"max={values[-1]:.3f}ms, "
        f"mean={statistics.mean(values):.3f}ms"
    )

def write_details(path: Path, rows: List[List[str]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

def main():
    args = parse_args()
    if args.run_extract:
        script_dir = Path(__file__).resolve().parent
        subprocess.check_call(
            ["python", str(script_dir / "extract_key_logs.py"), "--output", str(script_dir / "key_logs.csv")]
        )

    input_path = Path(args.input)
    start_times: List[float] = []
    start_events_by_source: Dict[str, List[float]] = {}
    gen_batch_by_source: Dict[str, List[Tuple[float, str]]] = {}
    header_times: Dict[str, float] = {}
    header_time_by_key: Dict[Tuple[int, str], float] = {}
    cert_times_by_header: Dict[str, float] = {}
    cert_times_by_digest: Dict[str, float] = {}
    send_times_by_digest: Dict[str, List[float]] = {}
    batch_to_header: Dict[str, str] = {}
    batch_to_header_key: Dict[str, Tuple[int, str]] = {}

    with input_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            event = row["event"]
            ts = row["timestamp_posix"]
            if not ts:
                continue
            ts_val = float(ts)
            line = row["line"]

            if event == "start_batch":
                start_times.append(ts_val)
                start_events_by_source.setdefault(row["source"], []).append(ts_val)
            elif event == "generate_header":
                m_key = HEADER_DEBUG_RE.search(line)
                if m_key:
                    round_num = int(m_key.group(2))
                    author = m_key.group(3)
                    header_time_by_key[(round_num, author)] = ts_val
                m = CREATED_HEADER_RE.search(line)
                if m:
                    header_id = m.group(1)
                    header_times[header_id] = ts_val
            elif event == "generate_certificate":
                m_header = ASSEMBLED_HEADER_RE.search(line)
                m_cert = ASSEMBLED_CERT_RE.search(line)
                if m_header:
                    cert_times_by_header[m_header.group(1)] = ts_val
                if m_cert:
                    cert_times_by_digest[m_cert.group(1)] = ts_val
            elif event == "send_certificate":
                m = RECEIVED_CERT_RE.search(line)
                if m:
                    digest = m.group(1)
                    send_times_by_digest.setdefault(digest, []).append(ts_val)
            elif event == "header_batch":
                m = CREATED_HEADER_BATCH_RE.search(line)
                if m:
                    header = m.group(1)
                    digest = m.group(2)
                    batch_to_header.setdefault(digest, header)
                    # header display: B{round}({author})
                    header_match = re.match(r"B(\d+)\(([^)]+)\)", header)
                    if header_match:
                        round_num = int(header_match.group(1))
                        author = header_match.group(2)
                        batch_to_header_key[digest] = (round_num, author)
            elif event == "generate_batch":
                # Capture batch digest in source worker log
                m = re.search(r"Batch ([^ ]+) contains", line)
                if m:
                    digest = m.group(1)
                    gen_batch_by_source.setdefault(row["source"], []).append((ts_val, digest))

    # start_batch -> generate_header (by worker order -> batch digest -> header key)
    start_to_header = []
    for source, starts in start_events_by_source.items():
        starts_sorted = sorted(starts)
        gen_batches = sorted(gen_batch_by_source.get(source, []), key=lambda x: x[0])
        i = j = 0
        while i < len(starts_sorted) and j < len(gen_batches):
            start_ts = starts_sorted[i]
            gen_ts, digest = gen_batches[j]
            if gen_ts < start_ts:
                j += 1
                continue
            # map batch digest -> header key -> header time
            header_key = batch_to_header_key.get(digest)
            if header_key and header_key in header_time_by_key:
                header_ts = header_time_by_key[header_key]
                if header_ts >= start_ts:
                    start_to_header.append((header_ts - start_ts) * 1000.0)
            i += 1
            j += 1

    # generate_header -> generate_certificate (match by header digest prefix)
    header_to_cert = []
    cert_header_items: List[Tuple[str, float]] = list(cert_times_by_header.items())
    for short_id, h_ts in header_times.items():
        matches = [(full_id, c_ts) for full_id, c_ts in cert_header_items if full_id.startswith(short_id)]
        if len(matches) != 1:
            continue
        _, c_ts = matches[0]
        if c_ts >= h_ts:
            header_to_cert.append((c_ts - h_ts) * 1000.0)

    # generate_certificate -> send_certificate (match by certificate digest)
    cert_to_send = []
    for digest, c_ts in cert_times_by_digest.items():
        send_list = send_times_by_digest.get(digest, [])
        if not send_list:
            continue
        send_time = min(t for t in send_list if t >= c_ts) if any(t >= c_ts for t in send_list) else None
        if send_time is not None:
            cert_to_send.append((send_time - c_ts) * 1000.0)

    print("start_batch -> generate_header (via batch digest):", summary_stats(start_to_header))
    print("generate_header -> generate_certificate (by header digest prefix):", summary_stats(header_to_cert))
    print("generate_certificate -> send_certificate (by cert digest):", summary_stats(cert_to_send))

    if args.details_out:
        out_dir = Path(args.details_out)
        write_details(out_dir / "start_to_header.csv", [["delta_ms"]] + [[f"{x:.3f}"] for x in start_to_header])
        write_details(out_dir / "header_to_cert.csv", [["delta_ms"]] + [[f"{x:.3f}"] for x in header_to_cert])
        write_details(out_dir / "cert_to_send.csv", [["delta_ms"]] + [[f"{x:.3f}"] for x in cert_to_send])
        if batch_to_header:
            write_details(
                out_dir / "batch_to_header.csv",
                [["batch_digest", "header"]] + [[k, v] for k, v in batch_to_header.items()],
            )
    elif batch_to_header:
        out_path = Path(__file__).resolve().parent / "batch_to_header.csv"
        write_details(
            out_path,
            [["batch_digest", "header"]] + [[k, v] for k, v in batch_to_header.items()],
        )

if __name__ == "__main__":
    main()