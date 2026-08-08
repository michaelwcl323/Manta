#!/usr/bin/env python3
"""Patch cloudlab_remote.py: stronger kill + verify clean before _update.

Usage:
  python scripts/patch_cloudlab_kill_ensure_clean.py PATH [PATH ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

OLD_KILL_CMD = """        kill_cmd = '''
            # Kill any running benchmark processes
            pkill -9 -f "node.*primary" 2>/dev/null || true
            pkill -9 -f "node.*worker" 2>/dev/null || true
            pkill -9 -f "benchmark_client" 2>/dev/null || true
            # Also kill any wrapper scripts that might still be running
            pkill -9 -f "/tmp/run_(primary|worker|client)-" 2>/dev/null || true
            true
        '''"""

NEW_KILL_CMD = """        # Broad patterns: match release binary and client even if argv layout differs.
        kill_cmd = '''
            pkill -9 -f "target/release/node" 2>/dev/null || true
            pkill -9 -f "[./]*node .*-vv run" 2>/dev/null || true
            pkill -9 -f "[./]*node .* run --keys" 2>/dev/null || true
            pkill -9 -f "node.*primary" 2>/dev/null || true
            pkill -9 -f "node.*worker" 2>/dev/null || true
            pkill -9 -f "benchmark_client" 2>/dev/null || true
            pkill -9 -f "/tmp/run_(primary|worker|client)-" 2>/dev/null || true
            true
        '''"""

ENSURE_METHOD = '''
    def kill_and_ensure_clean(
        self,
        hosts=[],
        delete_logs=False,
        committee=None,
        faults=0,
        *,
        retries: int = 8,
        settle_secs: float = 1.0,
    ):
        """Kill benchmark processes and wait until none remain on selected hosts.

        Call this *before* ``_update`` / ``_config`` / boot so the next cell never
        starts while a previous primary/worker/client is still alive.
        """
        import time

        assert isinstance(hosts, list)
        host_info = self.manager.get_host_info()
        if hosts:
            selected = []
            for h in hosts:
                if isinstance(h, dict):
                    selected.append(h)
                else:
                    username, hostname = h.split("@", 1)
                    selected.append({"hostname": hostname, "username": username, "port": 22})
        else:
            selected = list(host_info)

        # Match the same process families we kill. Keep the check itself out of matches.
        check_cmd = (
            "pgrep -af 'target/release/node|benchmark_client|/tmp/run_(primary|worker|client)-' "
            "2>/dev/null || true"
        )

        last_leftover = {}
        for attempt in range(1, retries + 1):
            self.kill(
                hosts=hosts,
                delete_logs=delete_logs and attempt == 1,
                committee=committee,
                faults=faults,
            )
            time.sleep(settle_secs)

            leftover = {}
            hosts_by_config = {}
            for host in selected:
                username = host.get("username", "root")
                hostname = host["hostname"]
                port = host.get("port", 22)
                hosts_by_config.setdefault((username, port), []).append(hostname)

            for (username, port), hostnames in hosts_by_config.items():
                conn_kwargs = self._get_connection_kwargs({})
                g = Group(
                    *hostnames,
                    user=username,
                    port=port,
                    connect_kwargs=conn_kwargs,
                    connect_timeout=30,
                )
                try:
                    results = g.run(check_cmd, hide=True, warn=True)
                except GroupException as e:
                    Print.warn(f"process-check GroupException (attempt {attempt}): {e}")
                    continue
                try:
                    items = list(results.items())
                except Exception:
                    Print.warn(f"unexpected process-check result type: {type(results)}")
                    continue
                for key, res in items:
                    hostname = (
                        getattr(key, "host", None)
                        or getattr(key, "original_host", None)
                        or str(key)
                    )
                    out = (getattr(res, "stdout", "") or "").strip()
                    lines = [
                        ln
                        for ln in out.splitlines()
                        if ln.strip()
                        and "pgrep -af" not in ln
                        and "bash -c" not in ln
                    ]
                    if lines:
                        leftover[str(hostname)] = lines

            if not leftover:
                Print.info(
                    f"All residual benchmark processes cleared "
                    f"(attempt {attempt}/{retries})."
                )
                return
            last_leftover = leftover
            sample = {h: v[:2] for h, v in list(leftover.items())[:3]}
            Print.warn(
                f"Residual processes remain after kill attempt {attempt}/{retries}: {sample}"
            )

        detail = {h: v[:5] for h, v in last_leftover.items()}
        raise BenchError(
            f"Failed to clear residual benchmark processes after {retries} attempts: {detail}",
            RuntimeError("residual processes still running"),
        )

'''

# Insert ensure_clean call immediately before _update in the run loop.
OLD_UPDATE_PREFIX = """                    # Update nodes (this will also modify attack.rs if trigger_attack is specified)
                    try:
                        if trigger_attack is not None:
                            Print.heading(f'\\nConfiguring nodes: attack={"ENABLED" if trigger_attack else "DISABLED"}')
                        self._update(selected_hosts, bench_parameters.collocate, trigger_attack=trigger_attack)"""

NEW_UPDATE_PREFIX = """                    # Always clear leftovers from the previous cell before update/config.
                    Print.info('Ensuring all residual benchmark processes are stopped...')
                    self.kill_and_ensure_clean(hosts=selected_hosts, delete_logs=False)

                    # Update nodes (this will also modify attack.rs if trigger_attack is specified)
                    try:
                        if trigger_attack is not None:
                            Print.heading(f'\\nConfiguring nodes: attack={"ENABLED" if trigger_attack else "DISABLED"}')
                        self._update(selected_hosts, bench_parameters.collocate, trigger_attack=trigger_attack)"""

OLD_KILL_BEFORE_BOOT = (
    "        Print.info('Killing any existing processes and ports...')\n"
    "        self.kill(hosts=selected_hosts, delete_logs=True, committee=committee, faults=faults)"
)
NEW_KILL_BEFORE_BOOT = (
    "        Print.info('Killing any existing processes and ports...')\n"
    "        self.kill_and_ensure_clean(\n"
    "            hosts=selected_hosts, delete_logs=True, committee=committee, faults=faults\n"
    "        )"
)


def patch_text(text: str, path: Path) -> str:
    if "def kill_and_ensure_clean" in text:
        print(f"  skip (already patched): {path}")
        return text

    if OLD_KILL_CMD not in text:
        raise SystemExit(f"kill_cmd block not found in {path}")
    text = text.replace(OLD_KILL_CMD, NEW_KILL_CMD, 1)

    # Insert ensure method right after kill() method ends (before next def at class level).
    marker = "            raise BenchError('Failed to kill nodes', FabricError(e))\n"
    # There may be multiple similar raises; anchor on the one that ends kill().
    kill_end = text.find(marker)
    if kill_end < 0:
        raise SystemExit(f"kill() end marker not found in {path}")
    # Find the end of this raise line occurrence that belongs to kill — use first after kill_cmd.
    kill_cmd_pos = text.find("Broad patterns: match release binary")
    if kill_cmd_pos < 0:
        kill_cmd_pos = text.find("pkill -9 -f \"target/release/node\"")
    kill_end = text.find(marker, kill_cmd_pos)
    if kill_end < 0:
        raise SystemExit(f"kill() end marker after kill_cmd not found in {path}")
    insert_at = kill_end + len(marker)
    # Skip following blank lines then insert before next method.
    text = text[:insert_at] + "\n" + ENSURE_METHOD + text[insert_at:]

    if OLD_UPDATE_PREFIX not in text:
        raise SystemExit(f"_update prefix not found in {path}")
    if "kill_and_ensure_clean(hosts=selected_hosts, delete_logs=False)\n\n                    # Update nodes" in text:
        pass
    else:
        text = text.replace(OLD_UPDATE_PREFIX, NEW_UPDATE_PREFIX, 1)

    if OLD_KILL_BEFORE_BOOT not in text:
        raise SystemExit(f"pre-boot kill not found in {path}")
    text = text.replace(OLD_KILL_BEFORE_BOOT, NEW_KILL_BEFORE_BOOT, 1)

    # Avoid `&&`-joining a multiline kill script (can confuse remote shells / hang).
    old_join = "g.run(' && '.join(cmd), hide=True, warn=True)"
    new_join = (
        "g.run(\n"
        "                        f\"{delete_logs_cmd}\\n{kill_cmd}\\n{cleanup_db_cmd}\",\n"
        "                        hide=True,\n"
        "                        warn=True,\n"
        "                        shell='/bin/bash',\n"
        "                    )"
    )
    if old_join not in text:
        raise SystemExit(f"kill g.run join not found in {path}")
    text = text.replace(old_join, new_join)

    return text


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    for arg in sys.argv[1:]:
        path = Path(arg)
        original = path.read_text(encoding="utf-8")
        updated = patch_text(original, path)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            print(f"  patched: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
