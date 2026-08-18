"""Shared AWS instance manager configured for Chitu."""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[3] / 'manta/benchmark/benchmark/instance.py'
_SPEC = importlib.util.spec_from_file_location('_shared_aws_instance', _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

class InstanceManager(_MODULE.InstanceManager):
    PROTOCOL_SUBDIR = 'chitu'

AWSError = _MODULE.AWSError
