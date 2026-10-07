"""Run the Linux analysis worker locally, without blocking Qt or using HTTP."""
import json
import os
from pathlib import Path
import sys
import time
import uuid

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal
from shared.protocol import ANALYSIS_ACTIVE_STATES

ROOT = Path(__file__).resolve().parents[1]


class LocalAnalysis(QObject):
    changed = Signal(object)

    def __init__(self, parent=None, output_root=None):
        super().__init__(parent)
        self.output_root = Path(output_root or ROOT/'data'/'local-analysis')
        self.status = None
        self.process = None
        self.buffer = b''

    @property
    def active(self):
        return bool(self.status and self.status['state'] in ANALYSIS_ACTIVE_STATES)

    def publish(self):
        path = Path(self.status['directory'])/'analysis_state.json'
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.status, ensure_ascii=False, allow_nan=False, indent=2), 'utf-8')
        tmp.replace(path)
        self.changed.emit(self.status)

    def start(self, path):
        if self.active:
            raise ValueError('已有本地分析正在运行')
        path = Path(path).resolve()
        if not path.is_file():
            raise ValueError('H5 文件不存在')
        aid = uuid.uuid4().hex
        output = self.output_root/aid
        output.mkdir(parents=True)
        config = output/'config_used.yaml'
        config.write_bytes((ROOT/'linux'/'analysis.yaml').read_bytes())
        self.status = dict(state='queued', stage='queued', progress=0, message='正在启动本地 H5 分析',
            job_id='local-'+aid, analysis_id=aid, origin='local', mode='file',
            source_h5=str(path), directory=str(output), result=None, error='', log=[])
        self.buffer = b''
        if self.process:
            self.process.deleteLater()
        self.process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert('PYTHONIOENCODING', 'utf-8')
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(ROOT))
        self.process.setStandardErrorFile(str(output/'worker.log'))
        self.process.readyReadStandardOutput.connect(self.read_progress)
        self.process.started.connect(self.started)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        self.publish()
        self.process.start(os.environ.get('OBSERVER_ANALYSIS_PYTHON', sys.executable),
            ['-m', 'linux.analysis_worker', '--mode', 'file', '--input', str(path),
             '--output', str(output), '--config', str(config)])

    def started(self):
        if self.status['state'] == 'queued':
            self.status.update(state='running', message='正在读取本地 H5')
            self.publish()
        elif self.status['state'] == 'cancelling':
            self.process.kill()

    def read_progress(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            if not line.strip() or self.status['state'] != 'running':
                continue
            try:
                event = json.loads(line.decode('utf-8'))
                event['at'] = time.strftime('%H:%M:%S')
                self.status['log'] = (self.status['log']+[event])[-150:]
                self.status.update({k: v for k, v in event.items() if k != 'at'})
                self.publish()
            except Exception as exc:
                self.status.update(state='failed', message='本地分析进度读取失败', error=str(exc))
                self.process.kill()
                self.publish()

    def process_error(self, error):
        if error == QProcess.FailedToStart:
            self.status.update(state='failed', message='无法启动本地分析进程', error=self.process.errorString())
            self.publish()

    def finished(self, code, exit_status):
        self.read_progress()
        if self.status['state'] == 'cancelling':
            self.status.update(state='cancelled', message='本地分析已取消', result=None)
        elif self.status['state'] != 'failed':
            try:
                if code != 0 or exit_status != QProcess.NormalExit:
                    log = (Path(self.status['directory'])/'worker.log').read_text('utf-8')[-2500:]
                    raise RuntimeError(log or f'分析进程退出 {code}')
                result = json.loads((Path(self.status['directory'])/'result.json').read_text('utf-8'))
                self.status.update(state='completed', stage='completed', progress=100, result=result,
                    message='候选分析完成' if result['candidate_count'] else '分析完成，未发现高出度候选')
            except Exception as exc:
                self.status.update(state='failed', message='本地分析失败，可以重新选择文件', error=str(exc), result=None)
        self.publish()

    def cancel(self):
        if self.active:
            self.status.update(state='cancelling', message='正在取消本地分析')
            self.publish()
            self.process.kill()

    def close(self):
        if self.active:
            self.cancel()
            self.process.waitForFinished(5000)
