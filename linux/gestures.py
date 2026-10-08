"""Durable gesture receipt only. This module never accesses Maxwell hardware."""
from contextlib import closing
import json
import re
import sqlite3
from datetime import datetime, timezone
from shared.gestures import GESTURES


class GestureLog:
    def __init__(self, root):
        self.path = root/'gesture-events.sqlite3'
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, receipt TEXT NOT NULL)')

    def receive(self, data):
        event_id = data.get('event_id')
        gesture_id = data.get('gesture_id')
        frame = data.get('frame_index')
        hand = data.get('hand')
        if not isinstance(event_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', event_id):
            raise ValueError('invalid gesture event_id')
        if type(gesture_id) is not int or gesture_id not in GESTURES:
            raise ValueError('unsupported gesture_id')
        if type(frame) is not int or not 0 <= frame < 2**63 or hand not in ('left', 'right'):
            raise ValueError('invalid glove frame or hand')
        payload = dict(event_id=event_id, gesture_id=gesture_id, frame_index=frame, hand=hand)
        encoded = json.dumps(payload, sort_keys=True)
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT payload, receipt FROM events WHERE event_id=?', (event_id,)).fetchone()
            if old:
                if old[0] != encoded:
                    raise ValueError('event_id already used for another gesture')
                return dict(json.loads(old[1]), duplicate=True)
            receipt = dict(payload, gesture=GESTURES[gesture_id], received_at=datetime.now(timezone.utc).isoformat(),
                           received=True, stimulated=False, duplicate=False)
            db.execute('INSERT INTO events(event_id,payload,receipt) VALUES(?,?,?)',
                       (event_id, encoded, json.dumps(receipt, ensure_ascii=False)))
            db.commit()
        return receipt
