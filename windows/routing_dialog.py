"""Select server-side CFG files; no Windows file picker or SDK calls."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QComboBox,
                              QCheckBox, QDialogButtonBox)


class RoutingDialog(QDialog):
    def __init__(self, catalog, parent=None):
        super().__init__(parent)
        self.setWindowTitle('选择 Linux 电极路由')
        self.setWindowModality(Qt.WindowModal)
        self.resize(660, 310)
        self.entries = catalog['files']
        layout = QVBoxLayout(self)
        root = QLabel('Linux 路由目录：'+catalog['root'])
        root.setWordWrap(True)
        layout.addWidget(root)
        self.combo = QComboBox()
        for entry in self.entries:
            self.combo.addItem(f"{entry['file']} · {entry['channels']} 通道")
        selected = catalog.get('selected', {}).get('path')
        for index, entry in enumerate(self.entries):
            if entry['path'] == selected:
                self.combo.setCurrentIndex(index)
        layout.addWidget(self.combo)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.details)
        explanation = QLabel('选择文件只更新采集配置与通道映射。请在 MaxLab 下载同一份 CFG 后，勾选下方确认；未确认时不能开始真实录制。')
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.confirmed = QCheckBox('我已在 MaxLab 下载所选 CFG，当前芯片仍使用该路由')
        layout.addWidget(self.confirmed)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('使用所选配置')
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        buttons.button(QDialogButtonBox.Save).setEnabled(bool(self.entries))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.combo.currentIndexChanged.connect(self.changed)
        self.changed()

    def changed(self, *_):
        self.confirmed.setChecked(False)
        if self.entries:
            entry = self.entries[self.combo.currentIndex()]
            self.details.setText(entry['path']+'\nSHA256：'+entry['sha256'])
        else:
            self.details.setText('该目录下没有可用的 CFG 文件，请在 Linux 检查目录或 --routing-root 参数。')

    def selection(self):
        entry = self.entries[self.combo.currentIndex()]
        return dict(file=entry['file'], sha256=entry['sha256'], downloaded=self.confirmed.isChecked())
