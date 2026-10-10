"""One local endpoint for the Web launcher and worker callbacks."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def browser_proxy_options(project_root: str | Path | None = None) -> dict | None:
    """Explicit browser transport, independent of API proxies and Web callbacks."""
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parent.parent
    if 'HARVEST_BROWSER_PROXY' in os.environ:
        proxy = {'server': os.environ['HARVEST_BROWSER_PROXY']}
        if os.environ.get('HARVEST_BROWSER_PROXY_BYPASS'):
            proxy['bypass'] = os.environ['HARVEST_BROWSER_PROXY_BYPASS']
    else:
        local = root / '.runtime/local-services.json'
        proxy = json.loads(local.read_text(encoding='utf-8')).get('browser_proxy') if local.is_file() else None
    if proxy is None:
        return None
    if not isinstance(proxy, dict) or set(proxy) - {'server', 'bypass', 'username', 'password'}:
        raise ValueError('invalid browser proxy configuration')
    if any(not isinstance(value, str) or '\n' in value or '\r' in value for value in proxy.values()):
        raise ValueError('browser proxy fields must be single-line strings')
    try:
        address = urlsplit(proxy.get('server', ''))
        valid = (address.scheme in {'http','https','socks5'} and address.hostname and address.port
                 and address.username is None and address.password is None
                 and address.path in {'','/'} and not address.query and not address.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('browser proxy server must be a URL with a port and no embedded credentials')
    return dict(proxy)


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
    browser_proxy_options(root)  # Invalid transport must fail before launcher stops live services.
    return config


def local_web_url(project_root: str | Path | None = None) -> str:
    return f"http://127.0.0.1:{load_web_config(project_root)['port']}"
