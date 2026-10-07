"""Qt controller for genuine glove frames, isolated from experiment commands."""
import ipaddress
import json
import os
from pathlib import Path
import sys
import time

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

ROOT = Path(__file__).resolve().parents[1]
GESTURES = {16: '石头', 2: '剪刀', 5: '布', 3: 'OK', 14: '点赞'}


def glove_settings():
    config = dict(host='172.16.36.235', port=7000, hand='left',
                  sdk=str(ROOT/'sdk'/'mhand'/'VDMocapSDK_DataRead.dll'))
    path = ROOT/'windows'/'glove.local.json'
    if path.exists():
        config.update(json.loads(path.read_text('utf-8-sig')))
    config['sdk'] = os.environ.get('OBSERVER_GLOVE_SDK', config['sdk'])
    return config


class GloveController(QObject):
    connection_changed = Signal(bool, str)
    gesture_changed = Signal(str, str)
    frame_received = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.connected = False
        self.buffer = b''
        self.candidate = None
        self.candidate_since = 0
        self.stable = None
        self.stopping = False
        self.reported_failure = False
        self.last_message = '手套未连接'

    @property
    def active(self):
        return self.process is not None and self.process.state() != QProcess.NotRunning

    def connect_glove(self, settings):
        if self.active:
            raise ValueError('请先断开当前手套连接')
        ipaddress.IPv4Address(settings['host'])
        if settings['hand'] not in ('left', 'right') or not 1 <= settings['port'] <= 65535:
            raise ValueError('手套端口或手别无效')
        if not Path(settings['sdk']).is_file():
            raise ValueError('请选择 mHand SDK DLL')
        if self.process:
            self.process.deleteLater()
        self.process = QProcess(self)
        self.buffer, self.stopping = b'', False
        self.reported_failure = False
        self.clear_gesture()
        env = QProcessEnvironment.systemEnvironment()
        env.insert('PYTHONIOENCODING', 'utf-8')
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(ROOT))
        self.process.readyReadStandardOutput.connect(self.read)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.error)
        self.connection_changed.emit(False, '正在连接手套…')
        self.process.start(sys.executable, ['-m', 'windows.glove_worker', '--host', settings['host'],
            '--port', str(settings['port']), '--hand', settings['hand'], '--sdk', settings['sdk']])

    def clear_gesture(self):
        self.candidate = self.stable = None
        self.gesture_changed.emit('—', '等待手套识别')

    def frame(self, event, now=None):
        now = time.monotonic() if now is None else now
        name = GESTURES.get(event['gesture_id'])
        self.frame_received.emit(event)
        if name is None:
            self.clear_gesture()
            return
        if self.candidate != name:
            self.candidate, self.candidate_since, self.stable = name, now, None
            self.gesture_changed.emit(name, '正在确认稳定手势')
        elif self.stable != name and now-self.candidate_since >= .65:
            self.stable = name
            self.gesture_changed.emit(name, '手势已稳定 · 仅显示，不触发刺激')

    def read(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        if len(self.buffer) > 65536:
            self.connection_changed.emit(False, '手套数据格式异常')
            self.disconnect_glove()
            return
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            try:
                event = json.loads(line.decode('utf-8'))
                if event['type'] == 'status':
                    if self.stopping and event['connected']:
                        continue
                    self.last_message = event['message']
                    self.connected = event['connected']
                    self.reported_failure = not self.connected
                    if not self.connected:
                        self.clear_gesture()
                    self.connection_changed.emit(self.connected, event['message'])
                elif event['type'] == 'frame' and self.connected and not self.stopping:
                    self.frame(event)
            except (ValueError, KeyError, TypeError):
                self.connection_changed.emit(False, '手套数据格式异常')
                self.disconnect_glove()

    def error(self, error):
        if error == QProcess.FailedToStart:
            self.reported_failure = True
            self.last_message = '无法启动手套接收进程：'+self.process.errorString()
            self.connection_changed.emit(False, self.last_message)

    def finished(self, code, exit_status):
        was_connected = self.connected
        self.connected = False
        self.clear_gesture()
        if self.stopping:
            self.connection_changed.emit(False, '手套已断开')
        elif not self.reported_failure and (was_connected or code != 0):
            self.connection_changed.emit(False, '手套接收已停止，请检查 mHand Studio 后重新连接')
        else:
            self.connection_changed.emit(False, self.last_message)

    def disconnect_glove(self):
        if self.active:
            self.stopping = True
            self.connected = False
            self.clear_gesture()
            self.connection_changed.emit(False, '正在断开手套…')
            process = self.process
            process.write(b'stop\n')
            timer = QTimer(process)
            timer.setSingleShot(True)
            timer.timeout.connect(process.kill)
            process.finished.connect(timer.stop)
            timer.start(3000)

    def close(self):
        self.disconnect_glove()
        if self.active and not self.process.waitForFinished(3000):
            self.process.kill()
            self.process.waitForFinished(1000)
