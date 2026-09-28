"""Offscreen Qt + real HTTP service integration; no equipment access."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication
from windows.app import Window, configure_fonts

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'observer-ui-test-token'


def run():
    app = QApplication([])
    configure_fonts(app)
    output = ROOT/'artifacts'/'validation'
    output.mkdir(parents=True, exist_ok=True)
    runtime = os.environ.get('OBSERVER_SERVICE_PYTHON', sys.executable)
    env = dict(os.environ)
    checks = []
    with tempfile.TemporaryDirectory() as directory:
        process = subprocess.Popen([runtime, '-m', 'linux.service', '--port', '0', '--mode', 'mock',
             '--output', directory, '--token', TOKEN], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        window = None
        try:
            line = process.stdout.readline().strip()
            assert 'Observer mock:' in line, line
            host = line.split('http://', 1)[1].split(' ', 1)[0]
            window = Window(host, TOKEN)
            window.show()
            def wait_for(predicate, limit=8):
                until = time.monotonic()+limit
                while time.monotonic() < until:
                    app.processEvents()
                    if predicate():
                        return
                    time.sleep(.02)
                raise AssertionError('Qt/HTTP condition timed out')
            window.connect_service()
            wait_for(lambda: window.online and window.worker is None)
            assert window.start_button.isEnabled()
            checks.append('Connect to an independent service using HTTP and token')
            # Use the real Windows start button: default 600 seconds, then stop early.
            window.start_recording()
            wait_for(lambda: window.job and window.job['elapsed_s'] > 5)
            assert window.job['duration_s'] == 600
            assert window.job['mode'] == 'mock'
            assert window.table.rowCount() == 1024
            assert window.job['total_spikes'] > 0
            assert window.raster.job['raster']
            wait_for(lambda: window.worker is None)
            assert not window.start_button.isEnabled()
            assert window.stop_button.isEnabled()
            checks.append('Start button requests 600 seconds; raster, heat map, 1024 rates populated')
            window.grab().save(str(output/'live_1340.png'))
            window.resize(1040, 730)
            app.processEvents()
            window.grab().save(str(output/'live_1040.png'))
            jid = window.job['job_id']
            wait_for(lambda: window.worker is None)
            window.connect_service()
            assert not window.connected
            time.sleep(.3)
            window.connect_service()
            wait_for(lambda: window.online and window.job['job_id'] == jid and window.worker is None)
            checks.append('Disconnect/reconnect keeps the same recording task')
            window.stop_recording()
            wait_for(lambda: window.job['state'] == 'stopped')
            assert window.job['files']
            assert window.job['elapsed_s'] < 600
            checks.append('Stop returns acknowledged stopped state and closed H5 path')
            window.grab().save(str(output/'stopped.png'))
            # Start request retry keeps its id until acknowledgement.
            wait_for(lambda: window.worker is None)
            window.connected = False
            window.received('/v1/status', None, 'test disconnect')
            frozen = window.time_label.text()
            app.processEvents()
            assert window.time_label.text() == frozen and not window.online
            checks.append('Connection loss freezes status instead of inventing a countdown')
            (output/'ui_report.json').write_text(json.dumps(dict(checks=checks, passed=len(checks),
                hardware_access=False, real_time_test='~6 seconds; 600-second target stopped early'), ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(checks, ensure_ascii=False))
        finally:
            if window:
                window.job = None
                window.close()
                app.processEvents()
            # The task has already stopped. The disposable test service can exit.
            process.terminate()
            process.wait(timeout=8)
            process.stdout.close()
            process.stderr.close()


if __name__ == '__main__':
    run()
