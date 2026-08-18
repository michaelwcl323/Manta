"""Shared AWS benchmark settings with the Chitu protocol adapter."""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[3] / 'manta/benchmark/benchmark/settings.py'
_SPEC = importlib.util.spec_from_file_location('_shared_aws_settings', _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
Settings = _MODULE.Settings
SettingsError = _MODULE.SettingsError
