"""Second-stage candidate progress and results in the same Qt window."""
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
    QPlainTextEdit, QAbstractItemView)

from shared.protocol import ANALYSIS_ACTIVE_STATES


class CandidateMap(QWidget):
    def __init__(self):
        super().__init__()
        self.result = None
        self.selected = None
        self.mapping = []
        self.setMinimumSize(300, 230)

    def set_data(self, result, mapping, selected=None):
        self.result, self.mapping, self.selected = result, mapping, selected
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor('#101d30'))
        p.setFont(QFont('Microsoft YaHei', 9))
        p.setPen(QColor('#8fa8c5'))
        if not self.result:
            p.drawText(self.rect(), Qt.AlignCenter, '分析完成后显示候选空间位置')
            return
        rows = self.mapping
        if not rows:
            return
        xs, ys = [r['x'] for r in rows], [r['y'] for r in rows]
        xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
        area = QRectF(40, 30, self.width()-80, self.height()-80)
        scale = min(area.width()/max(35, xmax-xmin+35), area.height()/max(35, ymax-ymin+35))
        ox, oy = area.center().x()-(xmin+xmax)*scale/2, area.center().y()-(ymin+ymax)*scale/2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor('#344b65'))
        for row in rows:
            p.drawEllipse(QRectF(ox+row['x']*scale-2, oy+row['y']*scale-2, 4, 4))
        candidates = self.result['candidates']
        peak = max([r['out_degree'] for r in candidates]+[1])
        positions = {row['electrode']: row for row in rows}
        for row in candidates:
            xy = positions.get(row['electrode'])
            if not xy:
                continue
            size = 6+6*row['out_degree']/peak
            p.setBrush(QColor('#ffca68') if row['electrode'] == self.selected else QColor('#42d7bd'))
            p.drawEllipse(QRectF(ox+xy['x']*scale-size/2, oy+xy['y']*scale-size/2, size, size))
            if row['electrode'] == self.selected:
                p.setPen(QColor('#ffe5a8'))
                p.drawText(int(ox+xy['x']*scale+9), int(oy+xy['y']*scale-9), str(row['electrode']))
                p.setPen(Qt.NoPen)
        p.setPen(QColor('#8fa8c5'))
        p.drawText(12, 19, f'Y ↓   {ymin:g} – {ymax:g} μm')
        p.drawText(12, self.height()-14, f'X → {xmin:g} – {xmax:g} μm')
        p.drawText(self.width()-220, self.height()-14, '青色：候选   黄色：当前选中')


class AnalysisPanel(QWidget):
    start_requested = Signal()
    cancel_requested = Signal()

    def __init__(self):
        super().__init__()
        self.result = None
        self.mapping = []
        self.signature = None
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        top = QHBoxLayout()
        title = QLabel('第二阶段 · 高出度候选分析')
        title.setObjectName('section')
        top.addWidget(title)
        top.addStretch()
        self.start_button = QPushButton('分析已完成的录制')
        self.cancel_button = QPushButton('取消分析')
        self.start_button.clicked.connect(self.start_requested.emit)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        top.addWidget(self.start_button)
        top.addWidget(self.cancel_button)
        layout.addLayout(top)
        self.status = QLabel('完整录制保存后自动分析')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFormat('%p%')
        layout.addWidget(self.progress)
        stages = QHBoxLayout()
        self.stage_labels = []
        for label in ('① 读取 Spike', '② 检测 Burst', '③ 激活与先后关系', '④ 出度筛选与保存'):
            chip = QLabel(label)
            chip.setAlignment(Qt.AlignCenter)
            stages.addWidget(chip)
            self.stage_labels.append(chip)
        layout.addLayout(stages)
        self.summary = QLabel('Burst —    有向关系 —    候选 —    出度阈值 —')
        self.summary.setObjectName('section')
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['序号', '电极 ID', '通道 CH', '出度', '入度', '出度－入度', 'Burst 参与率'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self.select_candidate)
        self.map = CandidateMap()
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.table)
        split.addWidget(self.map)
        split.setSizes([760, 500])
        layout.addWidget(split, 1)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(150)
        layout.addWidget(self.log)
        self.output = QLabel('')
        self.output.setWordWrap(True)
        self.output.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.output)
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(False)

    def refresh_buttons(self, job, analysis, online, busy, supported):
        state = analysis.get('state') if analysis else 'idle'
        available = bool(online and supported and job and job['state'] == 'completed')
        self.start_button.setEnabled(available and not busy and state not in ANALYSIS_ACTIVE_STATES and state != 'completed')
        self.cancel_button.setEnabled(available and not busy and state in ('queued', 'running'))
        self.start_button.setText('重新分析' if state in ('failed', 'cancelled', 'interrupted') else '分析已完成的录制')

    def show_status(self, analysis, job, supported):
        signature = (job.get('job_id') if job else None,
                     analysis.get('analysis_id') if analysis else None,
                     analysis.get('state') if analysis else None,
                     analysis.get('progress') if analysis else None,
                     len(analysis.get('log', [])) if analysis else 0,
                     supported)
        if signature == self.signature:
            return
        self.signature = signature
        self.mapping = job.get('mapping', []) if job else []
        if not analysis:
            self.result = None
            self.table.setRowCount(0)
            self.map.set_data(None, self.mapping)
            self.progress.setValue(0)
            self.log.clear()
            self.output.clear()
            self.summary.setText('Burst —    有向关系 —    候选 —    出度阈值 —')
            self.status.setText('完整录制保存后自动分析' if supported else '第二阶段需要更新 Linux 服务')
            for chip in self.stage_labels:
                chip.setStyleSheet('color:#8fa8c5;padding:8px;')
            return
        mode = '模拟数据' if analysis.get('mode', job.get('mode') if job else None) == 'mock' else '真实数据'
        details = ''
        if analysis.get('stage') == 'connections':
            details = f" · 电极对 {analysis.get('pairs_done', 0):,} / {analysis.get('pairs_total', 0):,}"
        self.status.setText(mode+' · '+analysis['message']+details)
        if analysis.get('error'):
            self.status.setText(self.status.text()+' · '+analysis['error'])
        self.progress.setValue(round(analysis.get('progress', 0)*10))
        current = {'reading': 0, 'bursts': 1, 'activation': 2, 'connections': 2,
                   'selection': 3, 'saving': 3, 'completed': 4}.get(analysis.get('stage'), -1)
        for i, chip in enumerate(self.stage_labels):
            color = '#58dcc3' if i <= current else '#8fa8c5'
            chip.setStyleSheet(f'color:{color};padding:8px;')
        lines = []
        for event in analysis.get('log', []):
            text = f"{event.get('at', '')}   {event['progress']:5.1f}%   {event['message']}"
            if 'pairs_total' in event:
                text += f"  ({event.get('pairs_done', 0):,}/{event['pairs_total']:,})"
            lines.append(text)
        self.log.setPlainText('\n'.join(lines))
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
        result = analysis.get('result')
        if result and self.result is not result:
            self.result = result
            threshold = '—' if result['threshold'] is None else f"{result['threshold']:g}"
            self.summary.setText(f"Burst {result['burst_count']}    有向关系 {result['edge_count']}    "
                                 f"候选 {result['candidate_count']}    出度阈值 {threshold}")
            self.table.setRowCount(len(result['candidates']))
            for i, row in enumerate(result['candidates']):
                values = [i+1, row['electrode'], row['channel'], row['out_degree'], row['in_degree'],
                          row['out_minus_in'], f"{row['burst_participation']*100:.1f}%"]
                for j, value in enumerate(values):
                    self.table.setItem(i, j, QTableWidgetItem(str(value)))
            self.map.set_data(result, self.mapping)
            if result['candidates']:
                self.table.selectRow(0)
            self.output.setText('分析结果：'+analysis['directory'])
        elif not result:
            self.result = None
            self.table.setRowCount(0)
            self.map.set_data(None, self.mapping)
            self.output.setText('本次分析仅确定候选，刺激标定尚未开始。')

    def select_candidate(self):
        rows = self.table.selectionModel().selectedRows()
        if rows and self.result and rows[0].row() < len(self.result['candidates']):
            electrode = self.result['candidates'][rows[0].row()]['electrode']
            self.map.set_data(self.result, self.mapping, electrode)
