"""Offscreen second-stage UI against a real disposable HTTP service."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication
from windows.app import Window, configure_fonts

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'analysis-ui-test-token'


def run():
    app = QApplication([])
    configure_fonts(app)
    runtime = os.environ.get('OBSERVER_SERVICE_PYTHON', sys.executable)
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    output = ROOT/'artifacts'/'validation'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run([runtime, '-c',
            'from tests.analysis_fixture import write_recording; import sys; from pathlib import Path; write_recording(Path(sys.argv[1]))',
            directory], cwd=ROOT, env=env, check=True)
        process = subprocess.Popen([runtime, '-m', 'linux.service', '--port', '0',
            '--mode', 'mock', '--output', directory, '--token', TOKEN],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        window = None
        try:
            line = process.stdout.readline().strip()
            assert 'Observer mock:' in line, line
            host = line.split('http://', 1)[1].split(' ', 1)[0]
            window = Window(host, TOKEN)
            window.show()

            def wait_for(predicate, limit=20):
                until = time.monotonic()+limit
                while time.monotonic() < until:
                    app.processEvents()
                    if predicate():
                        return
                    time.sleep(.01)
                raise AssertionError('Qt/analysis condition timed out')

            window.connect_service()
            wait_for(lambda: window.online and window.worker is None)
            assert window.job['state'] == 'completed'
            assert window.analysis_panel.start_button.isEnabled()
            window.timer.stop()
            window.poll()
            window.start_analysis()
            assert window.pending_command[0] == '/v1/analyze'
            window.timer.start()
            wait_for(lambda: window.analysis and window.analysis['state'] == 'completed')
            panel = window.analysis_panel
            result = window.analysis['result']
            assert window.tabs.currentIndex() == 1
            assert result['candidate_count'] > 0
            assert panel.table.rowCount() == result['candidate_count']
            assert panel.progress.value() == 1000
            assert panel.map.result == result
            assert '电极对' in panel.log.toPlainText() or '先后关系' in panel.log.toPlainText()
            assert panel.table.item(0, 1).text() == str(result['candidates'][0]['electrode'])
            panel.table.selectRow(0)
            app.processEvents()
            assert panel.map.selected == result['candidates'][0]['electrode']
            window.grab().save(str(output/'analysis_1340.png'))
            window.resize(1040, 730)
            app.processEvents()
            window.grab().save(str(output/'analysis_1040.png'))
            aid = window.analysis['analysis_id']
            wait_for(lambda: window.worker is None)
            window.connect_service()
            assert not window.connected
            window.connect_service()
            wait_for(lambda: window.online and window.analysis['analysis_id'] == aid and window.worker is None)
            assert panel.table.rowCount() == result['candidate_count']
            assert panel.start_button.isEnabled()
            assert panel.start_button.text() == '查看分析结果'
            panel.start_button.click()
            assert window.analysis['analysis_id'] == aid
            checks = ['Completed recording enables stage two', 'Queued analyze command survives status polling',
                'Real worker stages and result render in second tab', 'Candidate order and selection match server',
                'Reconnect restores same result without rerunning analysis', 'Both window sizes render']
            (output/'analysis_ui_report.json').write_text(json.dumps(dict(passed=len(checks), checks=checks,
                hardware_access=False), ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(checks, ensure_ascii=False))
        finally:
            if window:
                window.job = None
                window.close()
                app.processEvents()
            process.terminate()
            process.wait(timeout=8)
            process.stdout.close()
            process.stderr.close()


if __name__ == '__main__':
    run()
