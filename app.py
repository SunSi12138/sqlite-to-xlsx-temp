from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import parselmouth
from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QProgressBar, QSlider, QVBoxLayout, QWidget

from audio_core import ProcessResult, process_vocals

APP_NAME = "AutoVocal Prototype"

class WaveformWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(78)
        self._peaks = np.zeros(120, dtype=float)

    def set_audio(self, path: str | None) -> None:
        if not path:
            self._peaks = np.zeros(120, dtype=float)
            self.update()
            return
        try:
            sound = parselmouth.Sound(path)
            data = sound.values.mean(axis=0).astype(float)
            if len(data) == 0:
                raise ValueError
            bins = 120
            edges = np.linspace(0, len(data), bins + 1, dtype=int)
            peaks = []
            for a, b in zip(edges[:-1], edges[1:]):
                seg = data[a:b]
                peaks.append(float(np.max(np.abs(seg))) if len(seg) else 0.0)
            arr = np.asarray(peaks)
            scale = np.percentile(arr, 95) if np.any(arr) else 1.0
            self._peaks = np.clip(arr / max(scale, 1e-8), 0.0, 1.0)
        except Exception:
            self._peaks = np.zeros(120, dtype=float)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        mid = h / 2.0
        painter.setPen(QPen(QColor("#7C8CFF"), 1.5))
        step = w / len(self._peaks)
        path = QPainterPath()
        for i, p in enumerate(self._peaks):
            x = i * step + step / 2
            amp = max(1.0, p * h * 0.38)
            path.moveTo(x, mid - amp)
            path.lineTo(x, mid + amp)
        painter.drawPath(path)

class AudioCard(QFrame):
    file_changed = Signal(str)

    def __init__(self, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        self.setObjectName("audioCard")
        self.setAcceptDrops(True)
        self.path = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(9)
        top = QHBoxLayout()
        text = QVBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("muted")
        text.addWidget(title_label)
        text.addWidget(subtitle_label)
        top.addLayout(text)
        top.addStretch()
        self.button = QPushButton("选择 WAV")
        self.button.setObjectName("secondaryButton")
        self.button.clicked.connect(self.browse)
        top.addWidget(self.button)
        root.addLayout(top)
        self.filename = QLabel("拖入文件，或点击右上角选择")
        self.filename.setObjectName("filename")
        root.addWidget(self.filename)
        self.waveform = WaveformWidget()
        root.addWidget(self.waveform)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择人声文件", "", "WAV Audio (*.wav);;All files (*.*)")
        if path:
            self.set_file(path)

    def set_file(self, path: str):
        self.path = path
        self.filename.setText(Path(path).name)
        self.filename.setToolTip(path)
        self.waveform.set_audio(path)
        self.file_changed.emit(path)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if urls and urls[0].toLocalFile().lower().endswith(".wav"):
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self.set_file(urls[0].toLocalFile())
            event.acceptProposedAction()

class SliderRow(QWidget):
    def __init__(self, title: str, description: str, value: int, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        top = QHBoxLayout()
        labels = QVBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("controlTitle")
        desc = QLabel(description)
        desc.setObjectName("muted")
        labels.addWidget(title_label)
        labels.addWidget(desc)
        top.addLayout(labels)
        top.addStretch()
        self.value_label = QLabel(f"{value}%")
        self.value_label.setObjectName("valuePill")
        top.addWidget(self.value_label)
        layout.addLayout(top)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(value)
        self.slider.valueChanged.connect(lambda v: self.value_label.setText(f"{v}%"))
        layout.addWidget(self.slider)

class Worker(QThread):
    progress = Signal(int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, reference: str, user: str, output: str, strength: float, expression: float):
        super().__init__()
        self.reference = reference
        self.user = user
        self.output = output
        self.strength = strength
        self.expression = expression

    def run(self):
        try:
            result = process_vocals(self.reference, self.user, self.output, strength=self.strength, expression_keep=self.expression, progress=lambda value, text: self.progress.emit(value, text))
            self.done.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker: Worker | None = None
        self.output_path = ""
        self.setWindowTitle(APP_NAME)
        self.resize(1120, 820)
        self.setMinimumSize(940, 720)
        central = QWidget()
        central.setObjectName("root")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(38, 30, 38, 30)
        outer.setSpacing(22)
        header = QHBoxLayout()
        brand = QVBoxLayout()
        title = QLabel("AutoVocal")
        title.setObjectName("heroTitle")
        subtitle = QLabel("Reference-based vocal correction · Portable prototype")
        subtitle.setObjectName("heroSubtitle")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        header.addLayout(brand)
        header.addStretch()
        badge = QLabel("V0 · OFFLINE")
        badge.setObjectName("badge")
        header.addWidget(badge)
        outer.addLayout(header)
        intro = QLabel("用一条唱准的参考人声，把你的音高自动拉向参考旋律，同时尽量保留自己的音域与颤音。当前版本专注干净的单人声 WAV。")
        intro.setWordWrap(True)
        intro.setObjectName("intro")
        outer.addWidget(intro)
        cards = QHBoxLayout()
        cards.setSpacing(16)
        self.reference_card = AudioCard("① 参考人声", "Reference vocal · 唱准的版本")
        self.user_card = AudioCard("② 你的演唱", "User vocal · 要修正的版本")
        cards.addWidget(self.reference_card, 1)
        cards.addWidget(self.user_card, 1)
        outer.addLayout(cards)
        controls_frame = QFrame()
        controls_frame.setObjectName("panel")
        controls = QVBoxLayout(controls_frame)
        controls.setContentsMargins(24, 22, 24, 22)
        controls.setSpacing(20)
        controls_title = QLabel("修音参数")
        controls_title.setObjectName("sectionTitle")
        controls.addWidget(controls_title)
        self.strength = SliderRow("修音强度", "0% 保持原唱法，100% 最大程度贴近参考音高", 82)
        self.expression = SliderRow("保留颤音 / 表情", "保留你原本的细微 pitch movement，数值越高越自然", 72)
        controls.addWidget(self.strength)
        controls.addWidget(self.expression)
        outer.addWidget(controls_frame)
        status_frame = QFrame()
        status_frame.setObjectName("statusPanel")
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(20, 16, 20, 16)
        left = QVBoxLayout()
        self.status = QLabel("准备就绪")
        self.status.setObjectName("statusText")
        self.detail = QLabel("建议先用 10–30 秒、无伴奏、单人声 WAV 验证效果。")
        self.detail.setObjectName("muted")
        left.addWidget(self.status)
        left.addWidget(self.detail)
        status_layout.addLayout(left, 2)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        status_layout.addWidget(self.progress, 2)
        outer.addWidget(status_frame)
        actions = QHBoxLayout()
        self.open_button = QPushButton("打开输出")
        self.open_button.setObjectName("secondaryButton")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_output)
        actions.addWidget(self.open_button)
        actions.addStretch()
        self.run_button = QPushButton("开始自动修音  →")
        self.run_button.setObjectName("primaryButton")
        self.run_button.clicked.connect(self.start_processing)
        actions.addWidget(self.run_button)
        outer.addLayout(actions)
        self.setStyleSheet(STYLE)

    def start_processing(self):
        ref = self.reference_card.path
        user = self.user_card.path
        if not ref or not user:
            QMessageBox.warning(self, "缺少文件", "请先选择参考人声和你的演唱两个 WAV 文件。")
            return
        user_path = Path(user)
        output = str(user_path.with_name(user_path.stem + "_autovocal.wav"))
        self.output_path = output
        self.run_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.progress.setValue(2)
        self.status.setText("正在启动分析…")
        self.detail.setText("处理中请不要关闭窗口。")
        self.worker = Worker(ref, user, output, self.strength.slider.value() / 100.0, self.expression.slider.value() / 100.0)
        self.worker.progress.connect(self.on_progress)
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def on_progress(self, value: int, text: str):
        self.progress.setValue(value)
        self.status.setText(text)

    def on_done(self, result: ProcessResult):
        self.run_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self.progress.setValue(100)
        self.status.setText("修音完成")
        self.detail.setText(f"平均修正 {result.mean_abs_correction_cents:.0f} cents · 有效帧 {result.voiced_frames} · {result.duration_seconds:.1f}s")

    def on_failed(self, message: str):
        self.run_button.setEnabled(True)
        self.progress.setValue(0)
        self.status.setText("处理失败")
        self.detail.setText(message)
        QMessageBox.critical(self, "AutoVocal", message)

    def open_output(self):
        if self.output_path and os.path.exists(self.output_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.output_path))

STYLE = r"""
#root { background: #0B0D13; color: #F5F7FF; }
QLabel { color: #F5F7FF; }
#heroTitle { font-size: 34px; font-weight: 800; letter-spacing: -1px; }
#heroSubtitle, #muted { color: #8F97AA; font-size: 12px; }
#intro { color: #C5C9D6; font-size: 14px; padding: 2px 0 4px 0; }
#badge { color: #B7C0FF; background: #181D33; border: 1px solid #2A3154; border-radius: 12px; padding: 8px 12px; font-weight: 700; font-size: 11px; }
#audioCard, #panel, #statusPanel { background: #11141D; border: 1px solid #202431; border-radius: 18px; }
#audioCard:hover { border: 1px solid #39416B; background: #131722; }
#cardTitle, #sectionTitle { font-size: 16px; font-weight: 700; }
#controlTitle, #statusText { font-size: 14px; font-weight: 650; }
#filename { background: #0C0F16; border: 1px solid #1D2230; border-radius: 10px; padding: 9px 11px; color: #C7CCDA; font-family: Consolas, monospace; font-size: 11px; }
#valuePill { color: #D9DEFF; background: #22294A; border-radius: 10px; padding: 6px 10px; min-width: 44px; font-weight: 700; }
QPushButton { border: none; border-radius: 11px; padding: 11px 17px; font-weight: 700; }
#primaryButton { background: #7282FF; color: white; min-width: 190px; padding: 14px 20px; font-size: 14px; }
#primaryButton:hover { background: #8190FF; }
#primaryButton:disabled { background: #34394D; color: #767D90; }
#secondaryButton { background: #1A1E2A; color: #D9DCE7; border: 1px solid #2A3040; }
#secondaryButton:hover { background: #222838; }
#secondaryButton:disabled { color: #5E6472; background: #141720; }
QSlider::groove:horizontal { height: 6px; background: #242938; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #7282FF; border-radius: 3px; }
QSlider::handle:horizontal { width: 18px; height: 18px; margin: -6px 0; border-radius: 9px; background: #EEF0FF; border: 3px solid #7282FF; }
QProgressBar { background: #202431; border: none; border-radius: 4px; height: 8px; }
QProgressBar::chunk { background: #7282FF; border-radius: 4px; }
"""

def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
