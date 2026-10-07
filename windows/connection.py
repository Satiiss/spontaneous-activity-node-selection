"""Connection defaults; authentication stays in an ignored local file."""
import json
import os
from pathlib import Path

DEFAULT_HOST = '172.16.14.73:8765'


def connection_defaults(path=None):
    path = Path(path) if path else Path(__file__).with_name('connection.local.json')
    local = {}
    if path.exists():
        local = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(local, dict) or any(type(local.get(k, '')) is not str for k in ('host', 'token')):
            raise ValueError('connection.local.json must contain string host/token fields')
    return (os.environ.get('OBSERVER_HOST', local.get('host', DEFAULT_HOST)),
            os.environ.get('OBSERVER_TOKEN', local.get('token', '')))
