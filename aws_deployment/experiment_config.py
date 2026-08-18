import os
from copy import deepcopy
from pathlib import Path


CONFIG_ENV = 'MANTA_EXPERIMENT_CONFIG'


def load_parameters(protocol, bench_defaults, node_defaults):
    """Overlay one protocol's YAML parameters when run by the root runner."""
    config_value = os.environ.get(CONFIG_ENV)
    if not config_value:
        return bench_defaults, node_defaults

    try:
        import yaml
    except ImportError as error:
        raise RuntimeError(
            'PyYAML is required for the unified experiment configuration; '
            'install the benchmark requirements first'
        ) from error

    config_path = Path(config_value).expanduser().resolve()
    with config_path.open(encoding='utf-8') as config_file:
        config = yaml.safe_load(config_file) or {}

    protocol = os.environ.get('MANTA_PROTOCOL', protocol)
    protocols = config.get('protocols', {})
    if protocol not in protocols:
        raise ValueError(f'protocol {protocol!r} is missing from {config_path}')

    protocol_config = protocols[protocol] or {}
    bench = deepcopy(bench_defaults)
    node = deepcopy(node_defaults)
    bench.update(config.get('benchmark', {}) or {})
    bench.update(protocol_config.get('benchmark', {}) or {})
    node.update(protocol_config.get('node', {}) or {})
    return bench, node
