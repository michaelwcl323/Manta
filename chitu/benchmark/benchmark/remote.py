"""Shared bounded-parallel AWS runner using Chitu benchmark modules."""
import importlib.util
from pathlib import Path
import benchmark.utils as _local_utils

_UTILS_PATH = Path(__file__).resolve().parents[3] / 'manta/benchmark/benchmark/utils.py'
_UTILS_SPEC = importlib.util.spec_from_file_location('_shared_aws_utils', _UTILS_PATH)
_UTILS = importlib.util.module_from_spec(_UTILS_SPEC)
_UTILS_SPEC.loader.exec_module(_UTILS)
if not hasattr(_local_utils, 'write_failure_summary'):
    _local_utils.write_failure_summary = _UTILS.write_failure_summary
_PATH = Path(__file__).resolve().parents[3] / 'manta/benchmark/benchmark/remote.py'
_SPEC = importlib.util.spec_from_file_location('_shared_aws_remote', _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
Bench = _MODULE.Bench
FabricError = _MODULE.FabricError
ExecutionError = _MODULE.ExecutionError
