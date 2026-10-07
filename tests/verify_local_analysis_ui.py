"""Exercise the new file button and the shared worker without a Linux connection."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication, QFileDialog
from windows.app import Window, configure_fonts

ROOT = Path(__file__).resolve().parents[1]


def run():
    app = QApplication([])
    configure_fonts(app)
    runtime = os.environ.get('OBSERVER_SERVICE_PYTHON', sys.executable)
    os.environ['OBSERVER_ANALYSIS_PYTHON'] = runtime
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    output = ROOT/'artifacts'/'validation'
    output.mkdir(parents=True, exist_ok=True)
    window = Window()
    window.show()
    checks = []

    def wait_for(predicate, limit=30):
        until = time.monotonic()+limit
        while time.monotonic() < until:
            app.processEvents()
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError('Local analysis timed out')

    try:
        with tempfile.TemporaryDirectory(prefix='已有 H5 ') as directory:
            root = Path(directory)
            window.local_analysis.output_root = root/'local-results'
            subprocess.run([runtime, '-c',
                'from tests.analysis_fixture import write_recording; from linux.analysis_worker import analyze_file,load_config; '
                'from pathlib import Path; import sys; r=Path(sys.argv[1]); j=write_recording(r); '
                'analyze_file(j["files"][0], r/"reference", load_config("linux/analysis.yaml"), "mock", '
                'expected_mapping=j["mapping"], expected_rate=j["sample_rate"])',
                str(root)], cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
            source = root/'fixture-recording'/'spikes.h5'
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            panel = window.analysis_panel
            assert not window.online
            assert panel.file_button.isEnabled()
            with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(source), '')) as dialog:
                panel.file_button.click()
                assert dialog.called
            assert window.local_visible
            assert not panel.file_button.isEnabled()
            assert panel.cancel_button.isEnabled()
            wait_for(lambda: window.local_analysis.status['state'] not in ('queued', 'running', 'cancelling'))
            status = window.local_analysis.status
            assert status['state'] == 'completed', status
            result = status['result']
            reference = json.loads((root/'reference'/'result.json').read_text('utf-8'))
            for key in ('candidates', 'electrodes', 'mapping', 'threshold', 'burst_count', 'edge_count',
                        'config', 'algorithm_sha256'):
                assert result[key] == reference[key], key
            assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
            assert result['candidate_count'] > 0
            assert panel.table.rowCount() == result['candidate_count']
            assert len(panel.map.mapping) == len(result['mapping'])
            assert panel.progress.value() == 1000
            assert window.tabs.count() == 4
            assert window.calibration_panel.table.rowCount() == result['candidate_count']
            assert all(not button.isEnabled() for button in window.calibration_panel.buttons+window.game_panel.buttons)
            checks += ['File button works without a Linux connection', 'Real worker progress reaches completion',
                'Local result equals Linux worker result and parameters', 'Original H5 unchanged; coordinates rendered']
            window.apply_snapshot(dict(protocol=1, mode='mock', job=None,
                capabilities=['candidate_analysis_v1'], analysis=None))
            assert panel.result == result
            assert '本地 H5' in panel.status.text()
            checks.append('Linux status polling does not overwrite local results')
            window.resize(1040, 730)
            app.processEvents()
            window.grab().save(str(output/'local_analysis_1040.png'))
            window.tabs.setCurrentIndex(2)
            app.processEvents()
            window.grab().save(str(output/'calibration_preview_1040.png'))
            window.tabs.setCurrentIndex(3)
            app.processEvents()
            window.grab().save(str(output/'game_preview_1040.png'))
            window.tabs.setCurrentIndex(1)
            panel.table.selectRow(0)
            assert panel.map.selected == result['candidates'][0]['electrode']
            window.analyze_local_file(source)
            panel.cancel_button.click()
            wait_for(lambda: window.local_analysis.status['state'] == 'cancelled')
            assert window.local_analysis.status['result'] is None
            assert panel.table.rowCount() == 0
            assert panel.file_button.isEnabled()
            checks.append('Cancel stops local process and permits another file')
            invalid = root/'invalid.h5'
            invalid.write_bytes(b'not an H5')
            window.analyze_local_file(invalid)
            wait_for(lambda: window.local_analysis.status['state'] == 'failed')
            assert panel.table.rowCount() == 0
            assert panel.file_button.isEnabled()
            window.analyze_local_file(source)
            wait_for(lambda: window.local_analysis.status['state'] == 'completed')
            checks.append('Invalid H5 reports failure; valid file can be retried')
            window.online = True
            window.update_buttons()
            assert panel.start_button.text() == '查看 Linux 分析'
            panel.start_button.click()
            assert not window.local_visible
            assert panel.result is None
            checks.append('Existing button returns to Linux analysis view')
        historical = os.environ.get('OBSERVER_REFERENCE_H5')
        if historical:
            window.local_analysis.output_root = output/'historical-local'
            window.analyze_local_file(historical)
            wait_for(lambda: not window.local_analysis.active, limit=600)
            assert window.local_analysis.status['state'] == 'completed', window.local_analysis.status
            result = window.local_analysis.status['result']
            expected_path = os.environ.get('OBSERVER_REFERENCE_RESULT')
            if expected_path:
                expected = json.loads(Path(expected_path).read_text('utf-8'))
                for key in ('candidates', 'electrodes', 'threshold', 'burst_count', 'edge_count'):
                    assert result[key] == expected[key], key
            window.resize(1340, 860)
            app.processEvents()
            window.grab().save(str(output/'historical_local_analysis.png'))
            checks.append(f"Historical H5 local UI: {result['candidate_count']} candidates, {result['burst_count']} bursts")
    finally:
        window.job = None
        window.close()
        app.processEvents()
    (output/'local_analysis_ui_report.json').write_text(json.dumps(dict(checks=checks,
        passed=len(checks), hardware_access=False), ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == '__main__':
    run()
