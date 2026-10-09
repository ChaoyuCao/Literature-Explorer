"""Validated runtime configuration, independent of the installation directory."""
import json
import os
from pathlib import Path

DEFAULTS = {
    'state': '.literature', 'output_dir': 'outputs', 'source': 'pubmed',
    'timeout': 30, 'retries': 2, 'cache_ttl': 86400, 'offline': False,
    'email': '', 'ncbi_api_key': '', 'journal_metrics': None,
}


def load_config(path=None):
    config = dict(DEFAULTS)
    path = path or os.environ.get('LITERATURE_CONFIG')
    if path:
        path = Path(path).expanduser().resolve()
        supplied = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(supplied, dict) or set(supplied) - set(DEFAULTS):
            raise ValueError('Configuration must be an object containing supported settings only.')
        config.update(supplied)
        for key in ('state', 'output_dir', 'journal_metrics'):
            if supplied.get(key):
                value = Path(supplied[key]).expanduser()
                config[key] = str(value if value.is_absolute() else path.parent / value)
    if config['source'] not in ('pubmed', 'europepmc', 'crossref'):
        raise ValueError('Configuration source must be pubmed, europepmc, or crossref.')
    for key, low, high in [('timeout', 1, 300), ('retries', 0, 10), ('cache_ttl', 0, 31536000)]:
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ValueError(f'Invalid configuration setting: {key}.')
    if not isinstance(config['retries'], int) or not isinstance(config['offline'], bool):
        raise ValueError('retries must be an integer and offline must be a boolean.')
    for key in ('state', 'output_dir', 'email', 'ncbi_api_key'):
        if not isinstance(config[key], str):
            raise ValueError(f'Configuration {key} must be text.')
    if config['journal_metrics'] is not None and not isinstance(config['journal_metrics'], str):
        raise ValueError('journal_metrics must be a path or null.')
    config['ncbi_api_key'] = os.environ.get('NCBI_API_KEY', config['ncbi_api_key'])
    config['email'] = os.environ.get('LITERATURE_EMAIL', config['email'])
    return config


def public_config(config):
    return {**config, 'ncbi_api_key': '***' if config.get('ncbi_api_key') else ''}
