#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
from types import SimpleNamespace

class ConnectKwargs(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc
    def __setattr__(self, key, value):
        self[key] = value

from benchmark.cloudlab_remote import CloudLabBench
from benchmark.utils import BenchError, Print

settings = json.loads(Path('cloudlab_settings.json').read_text())
password = settings.get('ssh_key_password') or ''
if password and not os.environ.get('SSH_KEY_PASSWORD'):
    os.environ['SSH_KEY_PASSWORD'] = password

ctx = SimpleNamespace(connect_kwargs=ConnectKwargs())
bench_params = json.loads('{"faults": 0, "nodes": [10], "workers": 1, "collocate": true, "rate_type": "balanced", "rate": [100000], "tx_size": 512, "duration": 120, "runs": 3}')
node_params = json.loads('{"header_size": 1000, "max_header_delay": 50, "gc_depth": 50, "sync_retry_delay": 1000, "sync_retry_nodes": 7, "batch_size": 500000, "max_batch_delay": 50, "allow_cross_step_weak_edges": true, "enable_fast_coin": true, "solid_commit_trigger_on_solid_step": true, "enable_commit_recheck": true, "fast_coin_candidate_threshold": 4, "solid_candidate_threshold": 4, "enable_adaptive_intermediate_spill": true, "adaptive_intermediate_spill_trigger_digests": 2, "adaptive_intermediate_spill_cap_digests": 1, "design_tag": "manta_experiment2", "network_tag": "lan", "load_tag": "balanced_50_50", "attack_enabled": false, "sigma": 2, "kappa": 4, "reference": 4, "coverage": 10}')
try:
    CloudLabBench(ctx).run(bench_params, node_params, False)
except BenchError as exc:
    Print.error(exc)
    sys.exit(1)
