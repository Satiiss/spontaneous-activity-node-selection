"""Standalone Windows Qt observation client; no imports from GestureLoop."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid

from shared.protocol import (ACTIVE_STATES, DEFAULT_DURATION_S, DEFAULT_PORT,
                             PROTOCOL_VERSION, START_PATH, STATUS_PATH, STOP_PATH)

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QRectF
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QLineEdit, QHBoxLayout, QVBoxLayout, QGridLayout, QProgressBar, QTableWidget,
    QTableWidgetItem, QHeaderView, QSplitter, QMessageBox, QAbstractItemView)

ROOT = Path(__file__).resolve().parent
ACTIVE = ACTIVE_STATES
STATES = dict(starting='正在准备录制', recording='正在录制', stopping='正在停止',
              finalizing='正在保存和核验', completed='录制完成', stopped='已提前停止',
              failed='录制失败', interrupted='上次任务意外中断')


class Request(QThread):
    done = Signal(str, object, str)

    def __init__(self, base, token, path, data=None):
        super().__init__()
        self.base, self.token, self.path, self.data = base, token, path, data

    def run(self):
        try:
            req = urllib.request.Request(self.base+self.path,
                data=json.dumps(self.data).encode() if self.data is not None else None,
                headers={'Authorization': 'Bearer '+self.token, 'Content-Type': 'application/json'})
            # Local/LAN acquisition traffic must not pass through a system proxy.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=4) as response:
                result = json.load(response)
            if self.path == STATUS_PATH and result.get('protocol') != PROTOCOL_VERSION:
                raise ValueError('不兼容的服务协议')
            self.done.emit(self.path, result, '')
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError):
                try:
                    message = json.loads(exc.read()).get('error', str(exc))
                except Exception:
                    message = str(exc)
            else:
                message = str(exc)
            self.done.emit(self.path, None, message)


class Plot(QWidget):
    def __init__(self, kind):
        super().__init__()
        self.kind, self.job = kind, None
        self.setMinimumSize(300, 145)

    def set_job(self, job):
        self.job = job
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor('#101d30'))
        p.setFont(QFont('Microsoft YaHei', 9))
        p.setPen(QColor('#8fa8c5'))
        area = QRectF(60, 22, self.width()-85, self.height()-65)
        for i in range(6):
            y = area.top()+area.height()*i/5
            p.setPen(QColor('#23354b'))
            p.drawLine(area.left(), y, area.right(), y)
        job = self.job
        if not job:
            p.setPen(QColor('#8fa8c5'))
            p.drawText(self.rect(), Qt.AlignCenter, '连接采集服务并开始录制后显示')
            return
        rates = job.get('rates', [])
        if not rates:
            return
        if self.kind == 'raster':
            end = job['acquired_s']
            start = max(0, end-5)
            span = max(5, end-start)
            channels = [r['channel'] for r in rates]
            low, high = min(channels), max(channels)
            p.setPen(QPen(QColor('#47dbc0'), 1.5))
            for t, c, _ in job.get('raster', []):
                x = area.left()+(t-start)/span*area.width()
                y = area.bottom()-(c-low)/max(1, high-low)*area.height()
                p.drawLine(x, y-1, x, y+1)
            p.setPen(QColor('#8fa8c5'))
            p.drawText(8, 25, f'CH {high}')
            p.drawText(8, int(area.bottom()), f'{low}')
            for i in range(6):
                p.drawText(int(area.left()+area.width()*i/5-10), self.height()-19, f'{start+i:.1f}')
            p.drawText(self.width()-100, self.height()-3, '采集时间 / s')
        else:
            xs, ys = [r['x'] for r in rates], [r['y'] for r in rates]
            xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
            scale = min(area.width()/max(17.5, xmax-xmin+35), area.height()/max(17.5, ymax-ymin+35))
            ox = area.center().x()-(xmax+xmin)*scale/2
            oy = area.center().y()-(ymax+ymin)*scale/2
            size = max(2., min(12., 13*scale))
            p.setPen(Qt.NoPen)
            peak = max(1., max(r['hz'] for r in rates))
            for r in rates:
                value = min(1, r['hz']/peak)
                color = QColor.fromHsvF(.58-.45*value, .7, .25+.75*value)
                p.setBrush(color)
                p.drawRect(QRectF(ox+r['x']*scale-size/2, oy+r['y']*scale-size/2, size, size))
            p.setPen(QColor('#8fa8c5'))
            p.drawText(12, 16, f'Y ↓   μm    {ymin:g} – {ymax:g}')
            p.drawText(12, self.height()-18, f'X → {xmin:g} – {xmax:g} μm')
            p.drawText(self.width()-175, self.height()-18, f'色阶 0 – {peak:.1f} Hz（动态）')


def panel(title, widget):
    box = QWidget()
    box.setObjectName('card')
    layout = QVBoxLayout(box)
    name = QLabel(title)
    name.setObjectName('section')
    layout.addWidget(name)
    layout.addWidget(widget, 1)
    return box


class Window(QMainWindow):
    def __init__(self, host=f'127.0.0.1:{DEFAULT_PORT}', token=''):
        super().__init__()
        self.setWindowTitle('Spontaneous Observer · 自发电活动观测')
        self.resize(1340, 860)
        self.setMinimumSize(1040, 730)
        self.job = None
        self.worker = None
        self.pending_command = None
        self.connection_generation = 0
        self.connected = False
        self.online = False
        self.pending_id = None
        self.mode = None
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        top = QHBoxLayout()
        title = QLabel('自发电活动观测')
        title.setObjectName('title')
        top.addWidget(title)
        top.addWidget(QLabel('SPONTANEOUS OBSERVER  /  01'))
        top.addStretch()
        self.badge = QLabel('尚未连接')
        self.badge.setObjectName('badge')
        top.addWidget(self.badge)
        layout.addLayout(top)
        bar = QHBoxLayout()
        self.host = QLineEdit(host)
        self.host.setPlaceholderText('Linux IP:8765')
        self.token = QLineEdit(token)
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText('采集服务访问令牌')
        self.connect_button = QPushButton('连接服务')
        self.connect_button.clicked.connect(self.connect_service)
        self.start_button = QPushButton('开始 10 分钟录制')
        self.start_button.setObjectName('primary')
        self.start_button.clicked.connect(self.start_recording)
        self.stop_button = QPushButton('提前停止并保存')
        self.stop_button.clicked.connect(self.stop_recording)
        for w in (QLabel('服务地址'), self.host, self.token, self.connect_button, self.start_button, self.stop_button):
            bar.addWidget(w)
        layout.addLayout(bar)
        self.status = QLabel('先连接 Linux 采集服务；首次使用可运行独立 mock 自检。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        metrics = QHBoxLayout()
        self.time_label = QLabel('10:00')
        self.state_label = QLabel('待开始')
        self.count_label = QLabel('0')
        self.channel_label = QLabel('—')
        for label, widget in [('剩余时间', self.time_label), ('录制状态', self.state_label),
                               ('累计 Spike', self.count_label), ('活动 / 映射通道', self.channel_label)]:
            widget.setObjectName('metric')
            metrics.addWidget(panel(label, widget))
        layout.addLayout(metrics)
        self.progress = QProgressBar()
        self.progress.setRange(0, 6000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        layout.addWidget(self.progress)
        self.raster, self.heat = Plot('raster'), Plot('heat')
        layout.addWidget(panel('实时放电栅格图', self.raster), 1)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['通道 CH', '电极 ID', '放电率 Hz'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        bottom = QSplitter(Qt.Horizontal)
        bottom.addWidget(panel('电极活动热力图', self.heat))
        bottom.addWidget(panel('各通道放电率', self.table))
        bottom.setSizes([740, 460])
        layout.addWidget(bottom, 1)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.update_buttons()

    def send(self, path, data=None):
        if self.worker is not None:
            if (path != STATUS_PATH and self.worker.path == STATUS_PATH
                    and self.pending_command is None):
                self.pending_command = (path, data)
                self.update_buttons()
                return True
            return False
        base = 'http://'+self.host.text().strip().removeprefix('http://').rstrip('/')
        worker = Request(base, self.token.text(), path, data)
        worker.connection_generation = self.connection_generation
        self.worker = worker
        worker.done.connect(self.received)
        worker.finished.connect(self.finished)
        worker.start()
        self.update_buttons()
        return True

    def finished(self):
        worker = self.worker
        self.worker = None
        worker.deleteLater()
        if self.pending_command is not None:
            path, data = self.pending_command
            self.pending_command = None
            if self.connected:
                self.send(path, data)
        self.update_buttons()

    def update_buttons(self):
        busy = (self.pending_command is not None
                or (self.worker is not None and self.worker.path != STATUS_PATH))
        active = self.job and self.job['state'] in ACTIVE
        self.start_button.setEnabled(self.online and not active and not busy)
        self.stop_button.setEnabled(bool(self.online and active and not busy))
        self.connect_button.setEnabled(not busy)
        self.host.setEnabled(not self.connected)
        self.token.setEnabled(not self.connected)
        self.connect_button.setText('断开界面连接' if self.connected else '连接服务')
        self.start_button.setText('重试同一录制请求' if self.pending_id else '开始 10 分钟录制')

    def connect_service(self):
        self.connection_generation += 1
        if self.connected:
            self.connected = self.online = False
            self.pending_command = None
            self.status.setText('界面已断开；服务端任务如已启动会继续。重新连接可恢复状态。')
        else:
            self.connected = True
            self.poll()
        self.update_buttons()

    def poll(self):
        if self.connected:
            self.send(STATUS_PATH)

    def start_recording(self):
        self.pending_id = self.pending_id or uuid.uuid4().hex
        self.send(START_PATH, dict(request_id=self.pending_id, duration_s=DEFAULT_DURATION_S))

    def stop_recording(self):
        if self.job:
            self.send(STOP_PATH, dict(job_id=self.job['job_id']))

    def received(self, path, result, error):
        if self.worker is not None and self.worker.connection_generation != self.connection_generation:
            return
        if error:
            self.online = False
            self.badge.setText('连接或请求异常')
            self.status.setText('状态未确认，图形和倒计时冻结；自动重连中。'+error)
            self.update_buttons()
            return
        self.online = True
        if path != STATUS_PATH:
            self.job = result
            if path == START_PATH:
                self.pending_id = None
            self.update_buttons()
            return
        self.apply_snapshot(result)
        self.update_buttons()

    def apply_snapshot(self, result):
        self.mode = result['mode']
        self.badge.setText('MOCK · 模拟采集' if self.mode == 'mock' else 'MAXLAB · 真实采集')
        self.job = job = result.get('job')
        if job is None:
            self.status.setText('服务已连接。'+('当前是模拟数据。' if self.mode == 'mock' else '已选择现场采集模式。'))
            return
        if job['request_id'] == self.pending_id:
            self.pending_id = None
        remain = max(0, math.ceil(job['duration_s']-job['elapsed_s']))
        self.time_label.setText(f'{remain//60:02d}:{remain%60:02d}')
        self.state_label.setText(STATES.get(job['state'], job['state']))
        self.count_label.setText(f"{job['total_spikes']:,}")
        rates = job.get('rates', [])
        self.channel_label.setText(f"{sum(r['hz'] > 0 for r in rates)} / {len(job['mapping'])}")
        self.progress.setValue(round(job['elapsed_s']/job['duration_s']*6000))
        label = '模拟记录' if job['mode'] == 'mock' else '真实记录'
        message = f"{label} · {job['job_id']} · {STATES.get(job['state'], job['state'])}"
        if job.get('error'):
            message += ' · '+job['error']
        if result.get('hardware_fault'):
            message += ' · 硬件故障锁定，须现场核对'
        self.status.setText(message)
        self.raster.set_job(job)
        self.heat.set_job(job)
        if self.table.rowCount() != len(rates):
            self.table.setRowCount(len(rates))
        for i, row in enumerate(rates):
            for j, text in enumerate([str(row['channel']), str(row['electrode']), f"{row['hz']:.2f}"]):
                cell = self.table.item(i, j)
                if cell is None:
                    cell = QTableWidgetItem()
                    self.table.setItem(i, j, cell)
                cell.setText(text)

    def closeEvent(self, event):
        if self.job and self.job['state'] in ACTIVE:
            answer = QMessageBox.question(self, '录制仍在运行',
                '关闭窗口不会停止采集服务。要停止并保存，请先点击“提前停止并保存”。仍要关闭窗口吗？')
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self.timer.stop()
        if self.worker:
            self.worker.wait(5000)
        event.accept()


STYLE = '''
QWidget {background:#0a1322;color:#dfeaf8;font-family:"Microsoft YaHei";font-size:12px;}
QWidget#card {background:#101d30;border:1px solid #273950;border-radius:8px;}
QWidget#card QLabel {background:transparent;border:none;}
QLabel#title {font-size:25px;font-weight:700;} QLabel#section {font-size:14px;font-weight:600;}
QLabel#muted {color:#92a8c3;font-size:11px;} QLabel#metric {font-size:26px;color:#58dcc3;}
QLabel#badge {background:#173a40;color:#70e2d0;border-radius:5px;padding:8px;}
QLineEdit {background:#132239;border:1px solid #334d6b;border-radius:5px;padding:8px;}
QPushButton {background:#233750;border:1px solid #3b526b;border-radius:5px;padding:9px 13px;}
QPushButton:hover {background:#31506f;} QPushButton#primary {background:#1b857a;color:white;}
QPushButton:disabled {background:#182234;color:#60738c;border-color:#263346;}
QPushButton#primary:disabled {background:#18372f;color:#658b80;border-color:#263d35;}
QProgressBar {background:#20314a;border:none;} QProgressBar::chunk {background:#47d6b7;}
QTableWidget {background:#101d30;alternate-background-color:#15243a;gridline-color:#20324a;border:none;}
QHeaderView::section {background:#21334b;color:#c8d9ed;padding:7px;border:none;}
QScrollBar:vertical {background:#142137;width:10px;} QScrollBar::handle:vertical {background:#3c526c;}
'''


def configure_fonts(app):
    # Qt offscreen does not always enumerate the Windows font collection.
    for name in ('msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf'):
        path = Path('C:/Windows/Fonts')/name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont('Microsoft YaHei', 10))
    app.setStyleSheet(STYLE)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default=f'127.0.0.1:{DEFAULT_PORT}')
    parser.add_argument('--token', default='')
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    configure_fonts(app)
    window = Window(args.host, args.token)
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
