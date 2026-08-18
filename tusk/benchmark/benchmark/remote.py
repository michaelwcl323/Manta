"""Shared bounded-parallel AWS runner using Tusk benchmark modules."""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[3] / 'manta/benchmark/benchmark/remote.py'
_SPEC = importlib.util.spec_from_file_location('_shared_aws_remote', _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
Bench = _MODULE.Bench
FabricError = _MODULE.FabricError
ExecutionError = _MODULE.ExecutionError
