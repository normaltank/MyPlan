"""One local endpoint for the Web launcher and worker callbacks."""
from __future__ import annotations

import json
from pathlib import Path


def load_web_config(project_root: str | Path | None = None) -> dict:
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parent.parent
    config = json.loads((root / 'server/config.json').read_text(encoding='utf-8'))
    local = root / '.runtime/local-services.json'
    if local.is_file():
        override = json.loads(local.read_text(encoding='utf-8'))
        config['port'] = override.get('port', config.get('port', 8000))
    port = config.get('port', 8000)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('local Web port must be an integer between 1 and 65535')
    config['port'] = port
    return config


def local_web_url(project_root: str | Path | None = None) -> str:
    return f"http://127.0.0.1:{load_web_config(project_root)['port']}"
