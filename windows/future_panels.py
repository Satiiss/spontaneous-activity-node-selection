"""Stage three/four previews. No device, game, or network commands are issued."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGridLayout, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QProgressBar, QSplitter)


def title(text):
    label = QLabel(text)
    label.setObjectName('section')
    return label


def disabled_button(text):
    button = QPushButton(text)
    button.setEnabled(False)
    button.setToolTip('页面预览，等待刺激标定和本次模型接入')
    return button


def readonly_table(columns):
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    table.verticalHeader().hide()
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setAlternatingRowColors(True)
    return table


class CalibrationPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.signature = None
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(title('第三阶段 · 候选刺激标定与模型验证'))
        self.status = QLabel('页面预览 · 刺激标定尚未接入')
        layout.addWidget(self.status)
        self.source = QLabel('等待第二阶段候选分析结果')
        self.source.setWordWrap(True)
        layout.addWidget(self.source)
        self.summary = title('候选 —    已标定 0    最终刺激点 —    模型未生成')
        layout.addWidget(self.summary)
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        candidates = QVBoxLayout(left)
        candidates.addWidget(title('待标定候选'))
        self.table = readonly_table(['电极 ID', '通道 CH', '出度', '标定状态', '响应电极数'])
        candidates.addWidget(self.table, 1)
        split.addWidget(left)
        right = QWidget()
        detail = QVBoxLayout(right)
        detail.setSpacing(8)
        detail.addWidget(title('刺激与响应参数'))
        grid = QGridLayout()
        grid.setVerticalSpacing(3)
        for i, name in enumerate(('刺激波形 / 幅值', '重复次数', '刺激间隔', '基线与响应窗口', '路由与响应通道', '训练 / 验证划分')):
            grid.addWidget(QLabel(name), i, 0)
            grid.addWidget(QLabel('待配置'), i, 1)
        detail.addLayout(grid)
        detail.addWidget(title('最终刺激点与手势映射'))
        self.mapping_table = readonly_table(['手势', '刺激电极', '验证状态'])
        self.mapping_table.setMinimumHeight(132)
        self.mapping_table.setRowCount(3)
        for i, gesture in enumerate(('石头', '剪刀', '布')):
            for j, value in enumerate((gesture, '未确定', '未标定')):
                self.mapping_table.setItem(i, j, QTableWidgetItem(value))
        detail.addWidget(self.mapping_table, 1)
        split.addWidget(right)
        split.setSizes([720, 460])
        layout.addWidget(split, 1)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setFormat('尚未开始标定')
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.buttons = [disabled_button(text) for text in ('开始刺激标定', '暂停标定', '停止并保存', '训练与验证模型', '保存模型')]
        for button in self.buttons:
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def show_analysis(self, analysis):
        signature = (analysis.get('analysis_id'), analysis.get('state')) if analysis else None
        if signature == self.signature:
            return
        self.signature = signature
        result = analysis.get('result') if analysis and analysis.get('state') == 'completed' else None
        rows = result.get('candidates', []) if result else []
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, value in enumerate((row['electrode'], row['channel'], row['out_degree'], '未标定', '—')):
                self.table.setItem(i, j, QTableWidgetItem(str(value)))
        if result:
            origin = 'Windows 本地 H5' if analysis.get('origin') == 'local' else 'Linux 分析'
            self.source.setText(origin+' · '+analysis.get('source_h5', result['source_h5']))
            self.summary.setText(f'候选 {len(rows)}    已标定 0    最终刺激点 —    模型未生成')
            self.status.setText('页面预览 · 候选已读取，刺激标定尚未接入' if rows else '页面预览 · 当前分析没有候选，刺激标定尚未接入')
        else:
            self.source.setText('等待第二阶段候选分析结果')
            self.summary.setText('候选 —    已标定 0    最终刺激点 —    模型未生成')
            self.status.setText('页面预览 · 刺激标定尚未接入')


class GamePreviewPanel(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setSpacing(18)
        layout.addWidget(title('第四阶段 · 石头剪刀布'))
        layout.addWidget(QLabel('页面预览 · 等待第三阶段完成标定并生成经过验证的模型'))
        state = QHBoxLayout()
        for text in ('手套：未连接', '解码模型：未加载', '刺激映射：未确定'):
            state.addWidget(QLabel(text))
        layout.addLayout(state)
        cards = QHBoxLayout()
        for caption, hint in (('你的出拳', '等待手套识别'), ('MEA 解码结果', '等待刺激响应解码')):
            box = QWidget()
            box.setObjectName('card')
            card = QVBoxLayout(box)
            card.addWidget(title(caption))
            empty = QLabel('—')
            empty.setObjectName('metric')
            empty.setAlignment(Qt.AlignCenter)
            card.addWidget(empty, 1)
            note = QLabel(hint)
            note.setAlignment(Qt.AlignCenter)
            card.addWidget(note)
            cards.addWidget(box)
        layout.addLayout(cards, 1)
        countdown = QLabel('倒计时 —    对局结果 —')
        countdown.setObjectName('metric')
        countdown.setAlignment(Qt.AlignCenter)
        layout.addWidget(countdown)
        guide = QLabel('石头 · 剪刀 · 布    |    OK：开始 / 下一局    |    点赞：返回')
        guide.setAlignment(Qt.AlignCenter)
        layout.addWidget(guide)
        controls = QHBoxLayout()
        self.buttons = [disabled_button(text) for text in ('连接手套', '加载本次模型', '开始对局', '下一局', '返回待机')]
        for button in self.buttons:
            controls.addWidget(button)
        layout.addLayout(controls)
