"""Isolated second-stage task; never calls the hardware SDK."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

import yaml

from shared.protocol import ANALYSIS_ACTIVE_STATES
from .analysis_worker import load_config


class AnalysisTask:
    def __init__(self, job, config_path):
        self.job = copy.deepcopy(job)
        self.config_path = Path(config_path)
        self.path = Path(job['directory'])/'analysis_state.json'
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.thread = self.process = None
        self.status = dict(state='idle', progress=0, stage='waiting', message='等待完整录制',
                           job_id=job['job_id'], analysis_id=None, result=None, error='', log=[])
        if self.path.exists():
            self.status = json.loads(self.path.read_text('utf-8'))
            if self.status['job_id'] != job['job_id']:
                raise ValueError('analysis state belongs to another recording')
            if self.status['state'] in ANALYSIS_ACTIVE_STATES:
                self.status.update(state='interrupted', message='服务重启，分析已中断；可以重新分析')
                self._persist()

    def _persist(self):
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.status, ensure_ascii=False, allow_nan=False, indent=2), 'utf-8')
        tmp.replace(self.path)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.status)

    def start(self, request_id):
        with self.lock:
            if self.status['state'] == 'completed' or self.status.get('request_id') == request_id:
                return self.snapshot()
            if self.status['state'] in ANALYSIS_ACTIVE_STATES:
                return self.snapshot()  # one analysis per recording, including automatic start
            if self.job['state'] != 'completed':
                raise ValueError('仅完整录制成功的文件可以分析')
            config = load_config(self.config_path)
            # Trust only manifest files inside the current recording directory.
            directory = Path(self.job['directory']).resolve()
            name = 'spikes.h5' if self.job['mode'] == 'mock' else 'native'
            paths = [Path(p).resolve() for p in self.job['files']
                     if (Path(p).name == name if name.endswith('.h5') else Path(p).name.startswith(name) and Path(p).suffix == '.h5')]
            if len(paths) != 1 or paths[0].parent != directory or not paths[0].is_file():
                raise ValueError('缺少当前会话的完整分析源文件')
            aid = uuid.uuid4().hex
            output = directory/'analysis'/aid
            output.mkdir(parents=True)
            (output/'config_used.yaml').write_text(yaml.safe_dump(config, allow_unicode=True), 'utf-8')
            # Freeze the recording manifest and parameters for this attempt.
            manifest = output/'recording.json'
            manifest.write_text(json.dumps(self.job, ensure_ascii=False, indent=2), 'utf-8')
            self.status = dict(state='queued', progress=0, stage='queued', message='分析任务已排队',
                job_id=self.job['job_id'], analysis_id=aid, request_id=request_id,
                mode=self.job['mode'], source_h5=str(paths[0]), directory=str(output),
                result=None, error='', log=[])
            self.cancel_event.clear()
            self._persist()
            self.thread = threading.Thread(target=self._run, args=(paths[0], output, manifest), daemon=False)
            self.thread.start()
            return self.snapshot()

    def cancel(self, analysis_id):
        with self.lock:
            if analysis_id != self.status['analysis_id']:
                raise ValueError('analysis_id does not match current analysis')
            if self.status['state'] in ANALYSIS_ACTIVE_STATES:
                self.cancel_event.set()
                self.status.update(state='cancelling', message='正在取消分析')
                if self.process is not None and self.process.poll() is None:
                    self.process.terminate()
                self._persist()
            return self.snapshot()

    def _run(self, path, output, manifest):
        error, result = '', None
        try:
            with (output/'worker.log').open('w', encoding='utf-8') as log:
                with self.lock:
                    if self.cancel_event.is_set():
                        return
                    self.process = subprocess.Popen([sys.executable, '-m', 'linux.analysis_worker',
                        '--input', str(path), '--output', str(output), '--config', str(output/'config_used.yaml'),
                        '--mode', self.job['mode'], '--session', str(manifest)],
                        cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=log,
                        text=True, encoding='utf-8', env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
                    self.status.update(state='running', message='正在读取记录')
                    self._persist()
                for line in self.process.stdout:
                    if len(line) > 100000:
                        raise ValueError('oversized analysis progress event')
                    event = json.loads(line)
                    with self.lock:
                        if self.cancel_event.is_set():
                            break
                        event['at'] = time.strftime('%H:%M:%S')
                        self.status['log'] = (self.status['log']+[event])[-150:]
                        self.status.update({k: v for k, v in event.items() if k != 'at'})
                        self._persist()
                code = self.process.wait(timeout=5)
                if not self.cancel_event.is_set():
                    if code:
                        raise RuntimeError(f'分析进程退出 {code}: '+(output/'worker.log').read_text('utf-8')[-2500:])
                    result = json.loads((output/'result.json').read_text('utf-8'))
        except Exception as exc:
            error = str(exc)
        finally:
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=5)
                self.process.stdout.close()
            with self.lock:
                if self.cancel_event.is_set():
                    self.status.update(state='cancelled', message='分析已取消', result=None)
                elif error:
                    self.status.update(state='failed', message='分析失败，可以重新分析', error=error, result=None)
                else:
                    self.status.update(state='completed', stage='completed', progress=100,
                                       message='候选分析完成' if result['candidate_count'] else '分析完成，未发现高出度候选', result=result)
                self._persist()
