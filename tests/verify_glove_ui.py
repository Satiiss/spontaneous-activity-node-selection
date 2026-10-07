"""UI/child-process checks use fake SDK frames, never open a real UDP socket."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication
from windows.app import Window, configure_fonts

ROOT = Path(__file__).resolve().parents[1]
CHILD = '''import json,time,sys
print(json.dumps(dict(type='status',connected=True,message='TEST: connected')),flush=True)
for i in range(12):
 print(json.dumps(dict(type='frame',gesture_id=16,frame_index=i,frequency=60)),flush=True)
 time.sleep(.1)
sys.stdin.readline()
print(json.dumps(dict(type='status',connected=False,message='TEST: disconnected')),flush=True)
'''


def run():
    app = QApplication([])
    configure_fonts(app)
    window = Window()
    window.tabs.setCurrentIndex(3)
    window.show()
    page = window.game_panel
    checks = []
    original_start = QProcess.start
    captured = []

    def start_fake(process, executable, arguments):
        captured.append(arguments)
        original_start(process, sys.executable, ['-u', '-c', CHILD])

    def wait_for(predicate, limit=8):
        until = time.monotonic()+limit
        while time.monotonic() < until:
            app.processEvents()
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError('Glove UI timed out')

    try:
        assert page.connect_button.isEnabled()
        assert all(not button.isEnabled() for button in page.buttons)
        assert not page.glove.active
        with tempfile.TemporaryDirectory() as directory:
            sdk = Path(directory)/'test-only.dll'
            sdk.write_bytes(b'test SDK placeholder; never loaded')
            page.sdk_path = str(sdk)
            page.ip_input.setText('127.0.0.1')
            page.hand_input.setCurrentIndex(1)
            with patch.object(QProcess, 'start', start_fake), patch.object(page, 'save_settings'):
                page.connect_button.click()
                wait_for(lambda: page.glove.connected)
                assert '--hand' in captured[0] and 'right' in captured[0]
                assert '127.0.0.1' in captured[0] and '7000' in captured[0]
                assert not page.ip_input.isEnabled()
                wait_for(lambda: page.glove.stable == '石头')
                assert page.player_gesture.text() == '石头'
                assert '手套帧' in page.stream_info.text()
                assert all(not button.isEnabled() for button in page.buttons)
                checks += ['Connection button launches isolated worker with IP/port/hand',
                    'Received SDK frame updates actual gesture display',
                    '650 ms stability confirmation; experiment controls remain disabled']
                output = ROOT/'artifacts'/'validation'
                output.mkdir(parents=True, exist_ok=True)
                window.resize(1040, 730)
                app.processEvents()
                window.grab().save(str(output/'glove_ui_test_1040.png'))
                page.connect_button.click()
                wait_for(lambda: not page.glove.active)
                assert not page.glove.connected
                assert page.player_gesture.text() == '—'
                assert page.ip_input.isEnabled()
                assert page.connect_button.text() == '连接手套'
                checks.append('Disconnect clears stale gesture and permits reconnect')
                page.connect_button.click()
                wait_for(lambda: page.glove.connected)
                page.glove.close()
                assert not page.glove.active
                checks.append('Close cleans up a reconnected worker')
            page.sdk_path = str(Path(directory)/'missing.dll')
            page.connect_button.click()
            assert not page.glove.active
            assert 'SDK' in page.connection.text()
            checks.append('Missing SDK is reported without starting a connection')
        for gesture_id, name in [(16, '石头'), (2, '剪刀'), (5, '布'), (3, 'OK'), (14, '点赞')]:
            page.glove.frame(dict(gesture_id=gesture_id, frame_index=1, frequency=60), now=0)
            page.glove.frame(dict(gesture_id=gesture_id, frame_index=2, frequency=60), now=.7)
            assert page.glove.stable == name
        page.glove.frame(dict(gesture_id=0, frame_index=3, frequency=60))
        assert page.player_gesture.text() == '—'
        checks.append('Five original gesture IDs match; unknown ID clears display')
        (output/'glove_ui_report.json').write_text(json.dumps(dict(checks=checks, passed=len(checks),
            real_glove_connected=False, maxwell_stimulation=False), ensure_ascii=False, indent=2), 'utf-8')
        print(json.dumps(checks, ensure_ascii=False))
    finally:
        window.close()
        app.processEvents()


if __name__ == '__main__':
    run()
