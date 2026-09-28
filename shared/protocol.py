"""Versioned HTTP contract. No GUI, numerical or hardware dependencies."""

PROTOCOL_VERSION = 1
DEFAULT_PORT = 8765
DEFAULT_DURATION_S = 600
ACTIVE_STATES = frozenset({'starting', 'recording', 'stopping', 'finalizing'})
STATUS_PATH = '/v1/status'
START_PATH = '/v1/start'
STOP_PATH = '/v1/stop'
