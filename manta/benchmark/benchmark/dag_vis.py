from __future__ import annotations

import csv
import html
import json
import os
import re
from collections import defaultdict
from statistics import mean
from typing import Any, Dict, List, Optional

ROUND_LINE_RE = re.compile(r"\bRound\s+(\d+):\s+(.*)")
VERTEX_ID_RE = re.compile(r"\(Vertex(\d+)\)")
PARENT_RE = re.compile(r"\[(w?)(\d+|\?),\s*(\d+|\?)\]")
COMMIT_CHECK_RE = re.compile(
    r"DAG_COMMIT_CHECK\s+path=(?P<path>\S+)\s+"
    r"leader_round=(?P<leader_round>\d+)\s+"
    r"leader_node=(?P<leader_node>\d+)\s+"
    r"support_round=(?P<support_round>\d+)\s+"
    r"support_basis=(?P<support_basis>\S+)\s+"
    r"(?:trigger_round=(?P<trigger_round>\d+)\s+)?"
    r"stake=(?P<stake>\d+)\s+"
    r"threshold=(?P<threshold>\d+)\s+"
    r"result=(?P<result>\S+)\s+"
    r"support_set=\[(?P<support_set>[^\]]*)\]"
)
COMMITTED_RE = re.compile(r"DAG_COMMITTED\s+round=(?P<round>\d+)\s+node=(?P<node>\d+)")


def _split_ints(raw: str) -> List[int]:
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def _sorted_primary_logs(log_files: List[str]) -> List[str]:
    return sorted(path for path in log_files if os.path.basename(path).startswith("primary-"))


def select_consensus_log(log_files: List[str]) -> Optional[str]:
    primary_logs = _sorted_primary_logs(log_files)
    return primary_logs[0] if primary_logs else None


def parse_round_snapshot_line(line: str) -> Dict[str, Any]:
    match = ROUND_LINE_RE.search(line)
    if not match:
        return {}

    round_num = int(match.group(1))
    payload = match.group(2).strip()
    vertices = []
    known_parent_count = 0
    unknown_parent_count = 0
    known_vertex_count = 0

    for chunk in payload.split(" --- "):
        vertex_match = VERTEX_ID_RE.search(chunk)
        if not vertex_match:
            continue

        vertex_id = int(vertex_match.group(1))
        prefix = chunk.split(" (solid_wave_vertices:", 1)[0]
        parent_blob = prefix[vertex_match.end():]
        parent_blob = parent_blob.split(" weak=", 1)[0]

        parents = []
        vertex_known_parents = 0
        vertex_unknown_parents = 0
        for parent_match in PARENT_RE.finditer(parent_blob):
            weak_flag, parent_round, parent_node = parent_match.groups()
            if parent_round == "?" or parent_node == "?":
                vertex_unknown_parents += 1
                continue

            parents.append(
                {
                    "round": int(parent_round),
                    "node": int(parent_node),
                    "weak": weak_flag == "w",
                }
            )
            vertex_known_parents += 1

        if vertex_known_parents:
            known_vertex_count += 1
        known_parent_count += vertex_known_parents
        unknown_parent_count += vertex_unknown_parents
        vertices.append(
            {
                "vertex": vertex_id,
                "parents": parents,
                "strong_parent_count": sum(1 for item in parents if not item["weak"]),
                "weak_parent_count": sum(1 for item in parents if item["weak"]),
            }
        )

    return {
        "round": round_num,
        "raw": payload,
        "vertices": sorted(vertices, key=lambda item: item["vertex"]),
        "known_parent_count": known_parent_count,
        "unknown_parent_count": unknown_parent_count,
        "known_vertex_count": known_vertex_count,
    }


def round_snapshot_score(snapshot: Dict[str, Any]) -> tuple[int, int, int, int, int]:
    vertices = snapshot.get("vertices", [])
    return (
        snapshot.get("known_parent_count", 0),
        snapshot.get("known_vertex_count", 0),
        len(vertices),
        -snapshot.get("unknown_parent_count", 0),
        sum(len(vertex["parents"]) for vertex in vertices),
    )


def collect_best_round_snapshots(log_files: List[str]) -> Dict[int, Dict[str, Any]]:
    best_by_round: Dict[int, Dict[str, Any]] = {}

    for path in log_files:
        if not os.path.exists(path):
            continue

        with open(path, "r", errors="replace") as f:
            for line in f:
                parsed = parse_round_snapshot_line(line)
                if not parsed:
                    continue

                round_num = parsed["round"]
                existing = best_by_round.get(round_num)
                if existing is None or round_snapshot_score(parsed) >= round_snapshot_score(existing):
                    best_by_round[round_num] = parsed

    return best_by_round


def parse_final_dag(path: str) -> Dict[int, List[Dict[str, Any]]]:
    if not path or not os.path.exists(path):
        return {}

    rounds: Dict[int, List[Dict[str, Any]]] = {}
    with open(path, "r", errors="replace") as f:
        for line in f:
            parsed = parse_round_snapshot_line(line)
            if not parsed:
                continue

            rounds[parsed["round"]] = parsed["vertices"]

    return rounds


def parse_consensus_events(log_path: str) -> Dict[str, Any]:
    leader_events: Dict[tuple[str, int, int], Dict[str, Any]] = {}
    leader_markers_by_round: Dict[int, Dict[str, Any]] = {}
    committed_nodes_by_round: Dict[int, set[int]] = defaultdict(set)

    if not log_path or not os.path.exists(log_path):
        return {
            "log_path": log_path,
            "leader_events": leader_events,
            "leader_markers_by_round": leader_markers_by_round,
            "committed_nodes_by_round": committed_nodes_by_round,
        }

    seen_attempts = set()
    with open(log_path, "r", errors="replace") as f:
        for line in f:
            commit_match = COMMIT_CHECK_RE.search(line)
            if commit_match:
                leader_round = int(commit_match.group("leader_round"))
                support_round = int(commit_match.group("support_round"))
                trigger_round_raw = commit_match.group("trigger_round")
                trigger_round = (
                    int(trigger_round_raw) if trigger_round_raw is not None else support_round
                )
                stake = int(commit_match.group("stake"))
                threshold = int(commit_match.group("threshold"))
                result = commit_match.group("result")
                support_set = _split_ints(commit_match.group("support_set"))
                path = commit_match.group("path")
                attempt_key = (
                    path,
                    leader_round,
                    support_round,
                    trigger_round,
                    stake,
                    threshold,
                    result,
                    tuple(support_set),
                )
                if attempt_key in seen_attempts:
                    continue
                seen_attempts.add(attempt_key)

                event_key = (path, leader_round, support_round)
                event = leader_events.setdefault(
                    event_key,
                    {
                        "event_key": list(event_key),
                        "leader_round": leader_round,
                        "leader_node": int(commit_match.group("leader_node")),
                        "support_round": support_round,
                        "trigger_round": trigger_round,
                        "support_basis": commit_match.group("support_basis"),
                        "path": commit_match.group("path"),
                        "threshold": threshold,
                        "attempts": [],
                        "final_result": result,
                        "final_stake": stake,
                        "final_support_set": support_set,
                    },
                )
                leader_markers_by_round.setdefault(
                    leader_round,
                    {
                        "leader_round": leader_round,
                        "leader_node": int(commit_match.group("leader_node")),
                    },
                )
                event["attempts"].append(
                    {
                        "stake": stake,
                        "threshold": threshold,
                        "result": result,
                        "trigger_round": trigger_round,
                        "support_set": support_set,
                    }
                )
                event["leader_node"] = int(commit_match.group("leader_node"))
                event["support_round"] = support_round
                event["trigger_round"] = trigger_round
                event["support_basis"] = commit_match.group("support_basis")
                event["path"] = commit_match.group("path")
                event["threshold"] = threshold

                if result == "committed" or stake >= event["final_stake"]:
                    event["final_result"] = result
                    event["final_stake"] = stake
                    event["final_support_set"] = support_set

                continue

            committed_match = COMMITTED_RE.search(line)
            if committed_match:
                committed_nodes_by_round[int(committed_match.group("round"))].add(
                    int(committed_match.group("node"))
                )

    for event in leader_events.values():
        event["attempts"].sort(key=lambda item: (item["stake"], item["result"]))
        event["attempt_count"] = len(event["attempts"])

    return {
        "log_path": log_path,
        "leader_events": leader_events,
        "leader_markers_by_round": leader_markers_by_round,
        "committed_nodes_by_round": committed_nodes_by_round,
    }


def build_annotated_dag_snapshot(final_dag_path: str, log_files: List[str]) -> Dict[str, Any]:
    rounds_map = parse_final_dag(final_dag_path)
    selected_log = select_consensus_log(log_files)
    event_state = parse_consensus_events(selected_log) if selected_log else parse_consensus_events("")
    leader_events = event_state["leader_events"]
    leader_markers_by_round = event_state["leader_markers_by_round"]
    committed_nodes_by_round = event_state["committed_nodes_by_round"]
    leader_event_list = sorted(
        leader_events.values(),
        key=lambda event: (event["leader_round"], event["support_round"], event["path"]),
    )

    support_events_by_round: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for event in leader_event_list:
        support_events_by_round[event["support_round"]].append(event)

    rounds = []
    for round_num in sorted(rounds_map):
        rounds.append(
            {
                "round": round_num,
                "vertices": rounds_map[round_num],
                "leader_event": leader_markers_by_round.get(round_num),
                "support_events": sorted(
                    support_events_by_round.get(round_num, []),
                    key=lambda item: (item["leader_round"], item["support_round"], item["path"]),
                ),
                "committed_nodes": sorted(committed_nodes_by_round.get(round_num, set())),
            }
        )

    leader_event_groups: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for event in leader_event_list:
        leader_event_groups[event["leader_round"]].append(event)
    committed_event_count = sum(
        1
        for events in leader_event_groups.values()
        if any(event["final_result"] == "committed" for event in events)
    )
    attempt_counts = [
        sum(event["attempt_count"] for event in events)
        for events in leader_event_groups.values()
    ]
    support_gaps = [
        min(event["support_round"] - event["leader_round"] for event in events)
        for events in leader_event_groups.values()
    ]

    summary = {
        "selected_log": selected_log,
        "leader_rounds": len(leader_event_groups),
        "committed_leader_rounds": committed_event_count,
        "commit_success_rate": (
            committed_event_count / len(leader_event_groups) if leader_event_groups else 0.0
        ),
        "avg_attempts_per_leader": mean(attempt_counts) if attempt_counts else 0.0,
        "avg_support_gap": mean(support_gaps) if support_gaps else 0.0,
        "max_round": max(rounds_map) if rounds_map else 0,
        "round_count": len(rounds),
    }

    return {"summary": summary, "rounds": rounds}


def export_dag_event_csv(log_files: List[str], output_file: str) -> Optional[str]:
    selected_log = select_consensus_log(log_files)
    if not selected_log:
        return None

    event_state = parse_consensus_events(selected_log)
    leader_event_list = sorted(
        event_state["leader_events"].values(),
        key=lambda event: (event["leader_round"], event["support_round"], event["path"]),
    )
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    with open(output_file, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "path",
                "leader_round",
                "leader_node",
                "support_round",
                "trigger_round",
                "support_basis",
                "attempt_count",
                "final_result",
                "final_stake",
                "threshold",
                "support_set",
            ],
        )
        writer.writeheader()
        for event in leader_event_list:
            writer.writerow(
                {
                    "path": event["path"],
                    "leader_round": event["leader_round"],
                    "leader_node": event["leader_node"],
                    "support_round": event["support_round"],
                    "trigger_round": event.get("trigger_round", event["support_round"]),
                    "support_basis": event["support_basis"],
                    "attempt_count": event["attempt_count"],
                    "final_result": event["final_result"],
                    "final_stake": event["final_stake"],
                    "threshold": event["threshold"],
                    "support_set": ",".join(str(item) for item in event["final_support_set"]),
                }
            )

    return output_file


def export_dag_overview_json(snapshot: Dict[str, Any], output_file: str) -> str:
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(snapshot, f, indent=2)
        f.write("\n")
    return output_file


def _leader_badge(event: Dict[str, Any]) -> str:
    if not event:
        return ""
    return (
        f'<span class="badge badge-leader">leader 轮 r{event["leader_round"]}，节点 {event["leader_node"]}</span>'
    )


def _path_label(path: str) -> str:
    if path == "fast_coin":
        return "fast coin"
    return path


def _support_basis_label(basis: str) -> str:
    if basis == "solid_step_vertices_or_parent_path":
        return "solid-step / parent-path"
    if basis == "solid_wave_vertices":
        return "solid-wave"
    return basis


def _support_badges(events: List[Dict[str, Any]]) -> str:
    badges = []
    for event in events:
        css = "badge-ok" if event["final_result"] == "committed" else "badge-warn"
        result_text = "已提交" if event["final_result"] == "committed" else event["final_result"]
        badges.append(
            f'<span class="badge {css}">{html.escape(_path_label(event["path"]))} 检查 r{event["leader_round"]}，support 轮 r{event["support_round"]}，触发轮 r{event.get("trigger_round", event["support_round"])}: '
            f'{html.escape(result_text)}，basis={html.escape(_support_basis_label(event["support_basis"]))}</span>'
        )
    return "".join(badges)


def export_dag_overview_html(snapshot: Dict[str, Any], output_file: str) -> Optional[str]:
    all_rounds = snapshot.get("rounds", [])
    if not all_rounds:
        return None
    max_render_rounds = 30
    rounds = all_rounds[:max_render_rounds]
    rendered_round_count = len(rounds)

    all_vertices = [vertex["vertex"] for round_item in rounds for vertex in round_item["vertices"]]
    node_ids = sorted(set(all_vertices))
    round_index = {item["round"]: index for index, item in enumerate(rounds)}

    left_margin = 120
    top_margin = 120
    round_spacing = 140
    node_spacing = 60
    svg_width = left_margin + max(1, len(rounds)) * round_spacing + 120
    svg_height = top_margin + max(1, len(node_ids)) * node_spacing + 140

    node_positions = {}
    edge_lines = []
    node_groups = []
    round_headers = []
    row_labels = []

    for row_index, node_id in enumerate(node_ids):
        y = top_margin + row_index * node_spacing
        row_labels.append(
            f'<text x="24" y="{y + 5}" class="row-label">节点 {node_id}</text>'
        )

    for column_index, round_item in enumerate(rounds):
        round_num = round_item["round"]
        x = left_margin + column_index * round_spacing
        leader_event = round_item.get("leader_event")
        support_events = round_item.get("support_events", [])

        header_lines = [f'<text x="{x}" y="28" class="round-label">r{round_num}</text>']
        if leader_event:
            header_lines.append(
                f'<text x="{x}" y="48" class="round-sub leader-sub">leader 节点 {leader_event["leader_node"]}</text>'
            )
        if support_events:
            summary_parts = [
                f'{_path_label(event["path"])}:r{event["leader_round"]}/s{event["support_round"]}->{("已提交" if event["final_result"] == "committed" else event["final_result"])}'
                for event in support_events
            ]
            header_lines.append(
                f'<text x="{x}" y="68" class="round-sub support-sub">{html.escape(" | ".join(summary_parts))}</text>'
            )

        band_classes = ["round-band"]
        if leader_event:
            band_classes.append("band-leader")
        if support_events:
            if all(event["final_result"] == "committed" for event in support_events):
                band_classes.append("band-committed")
            else:
                band_classes.append("band-pending")
        header_lines.insert(
            0,
            f'<rect x="{x - 48}" y="0" width="96" height="{svg_height - 40}" '
            f'class="{" ".join(band_classes)}" rx="20"></rect>',
        )
        round_headers.extend(header_lines)

        committed_nodes = set(round_item.get("committed_nodes", []))
        leader_node = leader_event["leader_node"] if leader_event else None

        for vertex in round_item["vertices"]:
            vertex_id = vertex["vertex"]
            y = top_margin + node_ids.index(vertex_id) * node_spacing
            node_positions[(round_num, vertex_id)] = (x, y)

            css_classes = ["node"]
            if vertex_id in committed_nodes:
                css_classes.append("node-committed")
            if leader_node == vertex_id:
                css_classes.append("node-leader")

            parent_text = ", ".join(
                f'{"w" if parent["weak"] else ""}{parent["round"]}:{parent["node"]}'
                for parent in vertex["parents"]
            ) or "-"
            tooltip = (
                f"r{round_num} 节点 {vertex_id} | "
                f"强边={vertex['strong_parent_count']} 弱边={vertex['weak_parent_count']} | "
                f"父边={parent_text}"
            )

            node_groups.append(
                f'<g><circle cx="{x}" cy="{y}" r="14" class="{" ".join(css_classes)}">'
                f"<title>{html.escape(tooltip)}</title></circle>"
                f'<text x="{x}" y="{y + 4}" class="node-label">{vertex_id}</text></g>'
            )

    for round_item in rounds:
        round_num = round_item["round"]
        for vertex in round_item["vertices"]:
            child_pos = node_positions.get((round_num, vertex["vertex"]))
            if not child_pos:
                continue
            for parent in vertex["parents"]:
                parent_pos = node_positions.get((parent["round"], parent["node"]))
                if not parent_pos:
                    continue
                css = "edge edge-weak" if parent["weak"] else "edge edge-strong"
                edge_lines.append(
                    f'<line x1="{parent_pos[0]}" y1="{parent_pos[1]}" '
                    f'x2="{child_pos[0]}" y2="{child_pos[1]}" class="{css}"></line>'
                )

    summary = snapshot["summary"]
    success_pct = summary["commit_success_rate"] * 100

    round_cards = []
    for round_item in rounds:
        title = f"Round {round_item['round']}"
        badges = _leader_badge(round_item.get("leader_event")) + _support_badges(
            round_item.get("support_events", [])
        )
        badge_html = badges or '<span class="badge">无特殊事件</span>'
        committed_nodes = round_item.get("committed_nodes", [])
        commit_text = (
            f"已提交节点: {', '.join(str(node) for node in committed_nodes)}"
            if committed_nodes
            else "已提交节点: -"
        )
        round_cards.append(
            '<section class="round-card">'
            f'<h3>{html.escape(title.replace("Round", "第"))} 轮</h3>'
            f'<div class="badges">{badge_html}</div>'
            f'<p>{html.escape(commit_text)}</p>'
            "</section>"
        )

    html_text = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>DAG 总览</title>
  <style>
    :root {{
      --bg: #f7f8fb;
      --panel: #ffffff;
      --panel-2: #f3f5f9;
      --text: #142033;
      --muted: #52627a;
      --leader: #b45309;
      --committed: #15803d;
      --pending: #0369a1;
      --edge: #475569;
      --weak: #0284c7;
      --border: rgba(71, 85, 105, 0.18);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background:
        radial-gradient(circle at top left, rgba(2, 132, 199, 0.10), transparent 32%),
        radial-gradient(circle at top right, rgba(180, 83, 9, 0.10), transparent 26%),
        var(--bg);
      color: var(--text);
      font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      padding: 24px;
      display: grid;
      gap: 20px;
    }}
    .panel {{
      background: rgba(255, 255, 255, 0.94);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 18px 20px;
      box-shadow: 0 18px 35px rgba(15, 23, 42, 0.08);
    }}
    h1, h2, h3 {{
      margin: 0 0 10px;
      font-weight: 700;
    }}
    h1 {{
      font-size: 26px;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
    }}
    .metric {{
      padding: 14px;
      background: var(--panel-2);
      border-radius: 14px;
      border: 1px solid var(--border);
    }}
    .metric .label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .metric .value {{
      font-size: 24px;
      font-weight: 700;
      margin-top: 6px;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      color: var(--muted);
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 10px;
      border-radius: 999px;
      background: rgba(71, 85, 105, 0.08);
      border: 1px solid rgba(71, 85, 105, 0.18);
      margin-right: 8px;
      margin-bottom: 8px;
      white-space: nowrap;
    }}
    .badge-leader {{
      border-color: rgba(180, 83, 9, 0.35);
      background: rgba(245, 158, 11, 0.14);
      color: #92400e;
    }}
    .badge-ok {{
      border-color: rgba(21, 128, 61, 0.35);
      background: rgba(34, 197, 94, 0.14);
      color: #166534;
    }}
    .badge-warn {{
      border-color: rgba(3, 105, 161, 0.35);
      background: rgba(14, 165, 233, 0.14);
      color: #075985;
    }}
    .svg-shell {{
      overflow-x: auto;
      padding-bottom: 8px;
    }}
    svg {{
      min-width: 100%;
      display: block;
    }}
    .round-band {{
      fill: rgba(226, 232, 240, 0.45);
      stroke: rgba(148, 163, 184, 0.28);
    }}
    .band-leader {{
      fill: rgba(245, 158, 11, 0.10);
      stroke: rgba(180, 83, 9, 0.22);
    }}
    .band-committed {{
      fill: rgba(34, 197, 94, 0.10);
      stroke: rgba(21, 128, 61, 0.22);
    }}
    .band-pending {{
      fill: rgba(14, 165, 233, 0.10);
      stroke: rgba(3, 105, 161, 0.22);
    }}
    .edge {{
      fill: none;
      stroke-width: 1.8;
      opacity: 0.9;
    }}
    .edge-strong {{
      stroke: var(--edge);
    }}
    .edge-weak {{
      stroke: var(--weak);
      stroke-dasharray: 6 4;
    }}
    .node {{
      fill: #ffffff;
      stroke: #475569;
      stroke-width: 1.8;
    }}
    .node-committed {{
      fill: rgba(34, 197, 94, 0.16);
      stroke: rgba(21, 128, 61, 0.95);
      stroke-width: 2.2;
    }}
    .node-leader {{
      fill: rgba(245, 158, 11, 0.20);
      stroke: rgba(180, 83, 9, 0.95);
      stroke-width: 2.6;
    }}
    .node-label {{
      fill: var(--text);
      text-anchor: middle;
      font-size: 11px;
      font-weight: 700;
      pointer-events: none;
    }}
    .round-label {{
      fill: var(--text);
      text-anchor: middle;
      font-size: 16px;
      font-weight: 700;
    }}
    .round-sub {{
      text-anchor: middle;
      font-size: 10px;
      fill: var(--muted);
    }}
    .leader-sub {{
      fill: #92400e;
    }}
    .support-sub {{
      fill: #166534;
    }}
    .row-label {{
      fill: var(--muted);
      font-size: 12px;
    }}
    .round-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 12px;
    }}
    .round-card {{
      background: var(--panel-2);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px;
    }}
    .round-card p {{
      margin: 0;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <main>
    <section class="panel">
      <h1>带提交标注的 DAG 总览</h1>
      <p>当前语义：regular 路径在下一轮首个顶点到达时激活检查；之后只要 support round 有晚到证书，就对同一组 leader/support 重新检查，直到成功提交或进入下一次检查窗口。若启用 fast coin，则同一 leader 还会多出一条更早启动的检查路径：在下一轮首个顶点到达时，对前一轮 support round 发起检查；其支持依据优先看 solid-step 摘要，缺失时再退回 parent path。</p>
      <p>来源日志: {html.escape(summary.get("selected_log") or "-")}</p>
      <p>图中仅展示前 {rendered_round_count} 轮；上方统计指标仍基于全量 DAG 轮次与提交事件计算。</p>
      <div class="metrics">
        <div class="metric"><div class="label">Leader 轮数</div><div class="value">{summary["leader_rounds"]}</div></div>
        <div class="metric"><div class="label">成功提交的 Leader</div><div class="value">{summary["committed_leader_rounds"]}</div></div>
        <div class="metric"><div class="label">Leader 提交成功率</div><div class="value">{success_pct:.1f}%</div></div>
        <div class="metric"><div class="label">平均检查次数</div><div class="value">{summary["avg_attempts_per_leader"]:.2f}</div></div>
        <div class="metric"><div class="label">平均 Support 间隔</div><div class="value">{summary["avg_support_gap"]:.2f}</div></div>
      </div>
      <div class="legend" style="margin-top:14px;">
        <span class="badge badge-leader">金色节点 = 该 leader round 选中的 leader</span>
        <span class="badge badge-ok">绿色列 = 本轮触发过检查；regular 路径由下一轮首个顶点启动，fast coin 路径也由下一轮首个顶点启动，但优先使用更早的 solid-step / parent-path 支持依据；之后都可被晚到的 support round 证书再次触发并最终成功提交</span>
        <span class="badge">绿色描边 = 出现在 DAG_COMMITTED 中的顶点</span>
        <span class="badge">蓝色虚线 = weak parent 边</span>
      </div>
    </section>
    <section class="panel svg-shell">
      <svg width="{svg_width}" height="{svg_height}" viewBox="0 0 {svg_width} {svg_height}" xmlns="http://www.w3.org/2000/svg">
        {''.join(round_headers)}
        {''.join(row_labels)}
        {''.join(edge_lines)}
        {''.join(node_groups)}
      </svg>
    </section>
    <section class="panel">
      <h2>各轮事件</h2>
      <div class="round-grid">
        {''.join(round_cards)}
      </div>
    </section>
  </main>
</body>
</html>
"""

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w") as f:
        f.write(html_text)
    return output_file
