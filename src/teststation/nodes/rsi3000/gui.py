"""RSI3000E 的專用畫面。

RSI3000E 是 DUT 不是測試機台,所以有自己的視窗,**不跟站台 UI 共用**。
它跟 node 的溝通方式跟其他命令來源完全一樣:命令走 `teststation.client` 的
service 呼叫,畫面資料走 topic —— 這裡沒有任何捷徑。

```
uv run python -m teststation.nodes.rsi3000.gui                       # 站上只有一台時自動找到
uv run python -m teststation.nodes.rsi3000.gui --node RSI3000/COM6
```

* 即時 2×2 顯示四通道(縮小 4 倍,預設 ``>> 4`` 固定縮放;Auto-Contrast 改用各通道
  當下全幅 min/max 拉伸 —— **只動顯示,統計永遠讀原始 12-bit DN**)
* RGB 疊圖:R、(Gr + Gb) / 2、B 疊成一張 1024×768 的彩色影像(不做白平衡)
* 全幅與 ROI 的 min / max / mean / std(DN);ROI 可拖曳或輸入(通道座標 2048×1536)
* sensor 開關、張數 / 曝光 / 間隔、拍攝、自由命令列;console 輸出與命令回覆顯示在下方

**不存檔。** 要留影像請另外處理,這個畫面只看。
"""

from __future__ import annotations

import argparse
import sys

import ecal.nanobind_core as ecal
import numpy as np
from PySide6.QtCore import QObject, QPoint, QRect, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ... import client
from ...core.topics import IMAGE_PREFIX, RAW, STATUS
from . import image
from .protocol import DEVICE

DOWNSCALE = 4
CELL_W = image.CHANNEL_WIDTH // DOWNSCALE    # 512
CELL_H = image.CHANNEL_HEIGHT // DOWNSCALE   # 384
MONTAGE_W = CELL_W * 2
MONTAGE_H = CELL_H * 2
RGB_DOWNSCALE = image.CHANNEL_WIDTH // MONTAGE_W   # 2:RGB 疊圖跟 2×2 畫面一樣大

EXPOSURE_UNITS = {"us": 1.0, "ms": 1e3, "s": 1e6}
"""曝光欄位的單位 → 乘上多少變成 µs。node 收的一律是 µs。"""

MONO = QFont("Consolas")
OVERLAY = QColor(0, 255, 0)


# ------------------------------------------------------------- 純計算

def to_8bit(sample: np.ndarray, low: int, high: int, auto_contrast: bool) -> np.ndarray:
    """12-bit → 8-bit 顯示值:固定 `>> 4`,或 Auto-Contrast 時把 [low, high] 拉伸到 0–255。"""
    if not auto_contrast:
        return (sample >> 4).astype(np.uint8)
    span = max(1, high - low)
    stretched = (sample.astype(np.int32) - low) * 255 // span
    return stretched.clip(0, 255).astype(np.uint8)


def preview(plane: np.ndarray, full: image.Stat, auto_contrast: bool) -> np.ndarray:
    """原始平面 → 縮小 4 倍的 8-bit 顯示用拷貝。**只給畫面用**,不回頭影響統計。"""
    return to_8bit(plane[::DOWNSCALE, ::DOWNSCALE], full.min, full.max, auto_contrast)


def compose_rgb(planes: list[np.ndarray], stats: list[image.Stat],
                auto_contrast: bool) -> np.ndarray:
    """四通道疊成一張 RGB:R、G = (Gr + Gb) / 2、B,縮小 2 倍 → (768, 1024, 3) uint8。

    每個 2×2 Bayer quad 正好對應通道平面的一個像素,所以不需要內插,
    這就是最直接的 RGB。沒有白平衡與色彩校正 —— 顏色是 sensor 原始的樣子。
    Auto-Contrast 時三色各自拉伸(等於一個粗略的自動白平衡);G 的範圍取 Gr、Gb 合起來。
    """
    r, gr, gb, b = (plane[::RGB_DOWNSCALE, ::RGB_DOWNSCALE] for plane in planes)
    s_r, s_gr, s_gb, s_b = stats
    g = ((gr.astype(np.uint32) + gb) // 2).astype(np.uint16)
    return np.dstack([
        to_8bit(r, s_r.min, s_r.max, auto_contrast),
        to_8bit(g, min(s_gr.min, s_gb.min), max(s_gr.max, s_gb.max), auto_contrast),
        to_8bit(b, s_b.min, s_b.max, auto_contrast),
    ])


def clamp_roi(x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
    width, height = image.CHANNEL_WIDTH, image.CHANNEL_HEIGHT
    x = min(max(x, 0), width - 1)
    y = min(max(y, 0), height - 1)
    return x, y, min(max(w, 1), width - x), min(max(h, 1), height - y)


def drag_to_roi(start: QPoint, end: QPoint, rgb: bool = False) -> tuple[int, int, int, int]:
    """在畫面上拖曳的兩點 → 通道像素座標的 ROI。

    2×2 畫面以起點所在的那一格為準(縮小 4 倍);RGB 畫面整張就是一格(縮小 2 倍)。
    """
    cell_w, cell_h, scale = ((MONTAGE_W, MONTAGE_H, RGB_DOWNSCALE) if rgb
                             else (CELL_W, CELL_H, DOWNSCALE))
    origin_x = (start.x() // cell_w) * cell_w
    origin_y = (start.y() // cell_h) * cell_h

    def to_channel(point: QPoint) -> tuple[int, int]:
        cx = min(max(point.x() - origin_x, 0), cell_w - 1) * scale
        cy = min(max(point.y() - origin_y, 0), cell_h - 1) * scale
        return cx, cy

    x0, y0 = to_channel(start)
    x1, y1 = to_channel(end)
    return clamp_roi(min(x0, x1), min(y0, y1), abs(x1 - x0) + scale, abs(y1 - y0) + scale)


# ------------------------------------------------------ eCAL → Qt 的橋

class _Call(QRunnable):
    def __init__(self, link: "Link", lines: tuple[str, ...]) -> None:
        super().__init__()
        self._link = link
        self._lines = lines

    def run(self) -> None:
        for line in self._lines:
            try:
                reply = client.call(self._link.node, line)
            except Exception as exc:
                reply = f"ERR {exc}"
            try:
                self._link.reply.emit(line, reply)
            except RuntimeError:
                return   # 視窗已經關了


class Link(QObject):
    """訂閱這台 node 的影像 / raw / status,轉成 Qt signal。

    eCAL 的 callback 在它自己的執行緒上跑;signal 送到主執行緒會自動排隊,
    所以 widget 永遠只在主執行緒被碰到。
    """

    frame = Signal(object)          # image.Frame
    raw = Signal(str)
    status = Signal(str)
    reply = Signal(str, str)        # (送出的命令, 回覆)

    def __init__(self, node: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.node = node
        # 單一執行緒:命令照按的順序一條一條送,不會互相超車。
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._subs: list[ecal.Subscriber] = []
        for name in image.CHANNELS:
            self._subscribe(f"{node}/{IMAGE_PREFIX}{name}", self._on_image)
        self._subscribe(f"{node}/{RAW}",
                        lambda data: self._emit(self.raw, data.decode("utf-8", "replace")))
        self._subscribe(f"{node}/{STATUS}",
                        lambda data: self._emit(self.status, data.decode("utf-8", "replace")))

    def _subscribe(self, topic: str, handler) -> None:
        sub = ecal.Subscriber(topic)
        sub.set_receive_callback(lambda _t, _dt, data, *_: handler(data.buffer))
        self._subs.append(sub)

    def _on_image(self, data: bytes) -> None:
        frame = image.decode(data)
        if frame is not None:
            self._emit(self.frame, frame)

    @staticmethod
    def _emit(signal, value) -> None:
        try:
            signal.emit(value)
        except RuntimeError:
            pass   # 視窗已經關了

    def call(self, *lines: str) -> None:
        """依序送出幾行命令;每一行的回覆由 `reply` 帶回來。"""
        self._pool.start(_Call(self, lines))

    def close(self) -> None:
        for sub in self._subs:
            sub.remove_receive_callback()
        self._subs.clear()


# ------------------------------------------------------------- 影像

class MontageView(QWidget):
    """2×2 四通道或 RGB 疊圖,畫上 ROI;在影像上拖曳就是選 ROI。"""

    roi_dragged = Signal(tuple)

    LABELS = image.CHANNELS

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(MONTAGE_W, MONTAGE_H)
        self._pixels = np.zeros((MONTAGE_H, MONTAGE_W), dtype=np.uint8)
        self._rgb = np.zeros((MONTAGE_H, MONTAGE_W, 3), dtype=np.uint8)
        self._show_rgb = False
        self._roi = clamp_roi(0, 0, image.CHANNEL_WIDTH, image.CHANNEL_HEIGHT)
        self._drag_start: QPoint | None = None
        self._drag_end: QPoint | None = None

    @property
    def showing_rgb(self) -> bool:
        return self._show_rgb

    def set_cell(self, channel: int, cell: np.ndarray) -> None:
        row, col = divmod(channel, 2)
        self._pixels[row * CELL_H:(row + 1) * CELL_H, col * CELL_W:(col + 1) * CELL_W] = cell
        self.update()

    def set_rgb(self, rgb: np.ndarray) -> None:
        self._rgb = np.ascontiguousarray(rgb)
        self.update()

    def show_rgb(self, on: bool) -> None:
        self._show_rgb = on
        self.update()

    def set_roi(self, roi: tuple[int, int, int, int]) -> None:
        self._roi = roi
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        x, y, w, h = self._roi
        if self._show_rgb:
            frame = QImage(self._rgb.data, MONTAGE_W, MONTAGE_H, MONTAGE_W * 3,
                           QImage.Format.Format_RGB888)
            painter.drawImage(0, 0, frame)
            painter.setPen(QPen(OVERLAY, 1))
            painter.drawText(6, 16, "RGB")
            painter.drawRect(x // RGB_DOWNSCALE, y // RGB_DOWNSCALE,
                             w // RGB_DOWNSCALE, h // RGB_DOWNSCALE)
        else:
            frame = QImage(self._pixels.data, MONTAGE_W, MONTAGE_H, MONTAGE_W,
                           QImage.Format.Format_Grayscale8)
            painter.drawImage(0, 0, frame)
            painter.setPen(QPen(OVERLAY, 1))
            for channel, label in enumerate(self.LABELS):
                left = (channel % 2) * CELL_W
                top = (channel // 2) * CELL_H
                painter.drawText(left + 6, top + 16, label)
                painter.drawRect(left + x // DOWNSCALE, top + y // DOWNSCALE,
                                 w // DOWNSCALE, h // DOWNSCALE)
        if self._drag_start is not None and self._drag_end is not None:
            painter.drawRect(QRect(self._drag_start, self._drag_end).normalized())
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = self._drag_end = event.position().toPoint()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_start is not None:
            self._drag_end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_start is None:
            return
        roi = drag_to_roi(self._drag_start, event.position().toPoint(), rgb=self._show_rgb)
        self._drag_start = self._drag_end = None
        self.roi_dragged.emit(roi)


# ------------------------------------------------------------- 視窗

class Rsi3000Window(QMainWindow):
    def __init__(self, link: Link) -> None:
        super().__init__()
        self._link = link
        self._planes: list[np.ndarray | None] = [None] * len(image.CHANNELS)
        self._full: list[image.Stat | None] = [None] * len(image.CHANNELS)
        self._roi_stat: list[image.Stat | None] = [None] * len(image.CHANNELS)
        self._indices: list[int | None] = [None] * len(image.CHANNELS)
        self._roi = clamp_roi(0, 0, image.CHANNEL_WIDTH, image.CHANNEL_HEIGHT)
        self._status = "?"
        self._frame_index: int | None = None

        self._montage = MontageView()
        self._montage.roi_dragged.connect(self._set_roi)
        self._log = QPlainTextEdit(readOnly=True)
        self._log.setFont(MONO)
        self._log.setMaximumBlockCount(5000)

        left = QVBoxLayout()
        left.addWidget(self._montage)
        left.addWidget(self._log, 1)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addLayout(left)
        layout.addWidget(self._build_panel())
        self.setCentralWidget(central)

        link.frame.connect(self._on_frame)
        link.raw.connect(self._log.appendPlainText)
        link.status.connect(self._on_status)
        link.reply.connect(self._on_reply)
        self._refresh_title()
        self._refresh_stats()

    # ---------------------------------------------------------- 右側面板

    def _build_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(440)
        grid = QGridLayout(panel)
        row = 0

        def button(text: str, handler) -> QPushButton:
            widget = QPushButton(text)
            widget.clicked.connect(handler)
            return widget

        grid.addWidget(QLabel("Sensor"), row, 0)
        grid.addWidget(button("開啟 (csp 1)", lambda: self._link.call("sensor_on")), row, 1)
        grid.addWidget(button("關閉 (csp 0)", lambda: self._link.call("sensor_off")), row, 2)
        row += 1

        self._contrast = QPushButton("Auto-Contrast: 關閉")
        self._contrast.setCheckable(True)
        self._contrast.toggled.connect(self._toggle_contrast)
        self._rgb = QPushButton("RGB 疊圖")
        self._rgb.setCheckable(True)
        self._rgb.setToolTip("四通道疊成一張 RGB(G = (Gr + Gb) / 2),不做白平衡")
        self._rgb.toggled.connect(self._toggle_rgb)
        grid.addWidget(QLabel("顯示"), row, 0)
        grid.addWidget(self._contrast, row, 1)
        grid.addWidget(self._rgb, row, 2)
        row += 1

        self._frames = QSpinBox()
        self._frames.setRange(1, 1_000_000)
        self._frames.setValue(3)
        grid.addWidget(QLabel("張數 scnf"), row, 0)
        grid.addWidget(self._frames, row, 1)
        row += 1

        self._exposure = QDoubleSpinBox()
        self._exposure.setDecimals(3)
        self._exposure.setRange(0.001, 1e9)
        self._exposure.setValue(1_000_000)
        self._unit = QComboBox()
        self._unit.addItems(list(EXPOSURE_UNITS))
        grid.addWidget(QLabel("曝光"), row, 0)
        grid.addWidget(self._exposure, row, 1)
        grid.addWidget(self._unit, row, 2)
        row += 1

        self._fps = QDoubleSpinBox()
        self._fps.setDecimals(3)
        self._fps.setRange(0.03, 1000)
        self._fps.setValue(1.0)
        grid.addWidget(QLabel("間隔 (fps)"), row, 0)
        grid.addWidget(self._fps, row, 1)
        row += 1

        grid.addWidget(button("套用設定 (scnf / sce / scft)", self._apply), row, 0, 1, 2)
        grid.addWidget(button("拍攝 (tfc)", lambda: self._link.call("capture")), row, 2)
        row += 1

        roi_row = QHBoxLayout()
        self._roi_edits = []
        for value, limit in zip(self._roi, (image.CHANNEL_WIDTH, image.CHANNEL_HEIGHT) * 2):
            edit = QSpinBox()
            edit.setMinimumWidth(64)
            edit.setRange(0, limit)
            edit.setValue(value)
            roi_row.addWidget(edit)
            self._roi_edits.append(edit)
        roi_row.addWidget(button("套用", self._apply_roi_edits))
        grid.addWidget(QLabel("ROI x/y/w/h"), row, 0)
        grid.addLayout(roi_row, row, 1, 1, 2)
        row += 1
        grid.addWidget(QLabel("(也可在影像上拖曳選取,座標為通道像素)"), row, 0, 1, 3)
        row += 1

        self._command = QLineEdit()
        self._command.setFont(MONO)
        self._command.setPlaceholderText("console 命令,原樣送出")
        self._command.returnPressed.connect(self._send_command)
        grid.addWidget(QLabel("命令"), row, 0)
        grid.addWidget(self._command, row, 1)
        grid.addWidget(button("送出", self._send_command), row, 2)
        row += 1

        self._stats = QLabel()
        self._stats.setFont(MONO)
        self._stats.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(self._stats, row, 0, 1, 3)
        grid.setRowStretch(row, 1)
        return panel

    # ------------------------------------------------------------- 命令

    def exposure_us(self) -> float:
        return self._exposure.value() * EXPOSURE_UNITS[self._unit.currentText()]

    def _apply(self) -> None:
        """三條設定依序送出。換算成 tick 是 node 的事 —— 測試計畫送同樣的命令也得到同樣的值。"""
        self._link.call(
            f"set_frames {self._frames.value()}",
            f"set_exposure {self.exposure_us():.2f}",
            f"set_frame_rate {self._fps.value():g}",
        )

    def _send_command(self) -> None:
        text = self._command.text().strip()
        if text:
            self._link.call(f"send {text}")
            self._command.clear()

    def _on_reply(self, line: str, reply: str) -> None:
        self._log.appendPlainText(f">> {line}\n   {reply}")

    def _on_status(self, status: str) -> None:
        if status != self._status:
            self._status = status
            self._refresh_title()

    # ------------------------------------------------------------- 影像

    def _on_frame(self, frame: image.Frame) -> None:
        channel = frame.channel
        self._planes[channel] = frame.plane
        self._full[channel] = image.stat(frame.plane)
        self._roi_stat[channel] = self._stat_in_roi(frame.plane)
        self._indices[channel] = frame.index
        self._render(channel)
        # 四個通道是分開到的;湊齊同一張的四個才疊 RGB,不混用新舊兩張。
        if len(set(self._indices)) == 1:
            self._render_rgb()
        self._frame_index = frame.index
        self._refresh_title()
        self._refresh_stats()

    def _render(self, channel: int) -> None:
        plane, full = self._planes[channel], self._full[channel]
        if plane is not None and full is not None:
            self._montage.set_cell(channel, preview(plane, full, self._contrast.isChecked()))

    def _render_rgb(self) -> None:
        if not self._rgb.isChecked() or any(p is None for p in self._planes):
            return   # 不顯示 RGB 時不算,省下每張約幾十 ms
        self._montage.set_rgb(compose_rgb(self._planes, self._full, self._contrast.isChecked()))

    def _toggle_contrast(self, on: bool) -> None:
        self._contrast.setText(f"Auto-Contrast: {'開啟' if on else '關閉'}")
        for channel in range(len(image.CHANNELS)):
            self._render(channel)   # 用手上這張重畫,不必等下一張
        self._render_rgb()

    def _toggle_rgb(self, on: bool) -> None:
        self._render_rgb()          # 用手上這張疊,不必等下一張
        self._montage.show_rgb(on)

    def _stat_in_roi(self, plane: np.ndarray) -> image.Stat:
        x, y, w, h = self._roi
        return image.stat(plane[y:y + h, x:x + w])

    def _apply_roi_edits(self) -> None:
        self._set_roi(clamp_roi(*(edit.value() for edit in self._roi_edits)))

    def _set_roi(self, roi: tuple[int, int, int, int]) -> None:
        self._roi = roi
        for edit, value in zip(self._roi_edits, roi):
            edit.setValue(value)
        self._roi_stat = [None if plane is None else self._stat_in_roi(plane)
                          for plane in self._planes]
        self._montage.set_roi(roi)
        self._refresh_stats()

    # ------------------------------------------------------------- 顯示

    def _refresh_title(self) -> None:
        frame = "—" if self._frame_index is None else f"#{self._frame_index}"
        self.setWindowTitle(f"RSI3000 — {self._link.node} | {self._status} | frame {frame}")

    def _refresh_stats(self) -> None:
        x, y, w, h = self._roi
        lines = [f"ROI ({x},{y}) {w}x{h}        [DN 0-4095]", ""]
        for name, full, roi in zip(image.CHANNELS, self._full, self._roi_stat):
            if full is None or roi is None:
                lines += [f"{name:<2}  (無資料)", ""]
                continue
            lines += [
                f"{name:<2} full min {full.min:4} max {full.max:4} "
                f"mean {full.mean:7.1f} std {full.std:7.2f}",
                f"   ROI  min {roi.min:4} max {roi.max:4} "
                f"mean {roi.mean:7.1f} std {roi.std:7.2f}",
                "",
            ]
        self._stats.setText("\n".join(lines))

    def closeEvent(self, event) -> None:
        self._link.close()
        super().closeEvent(event)


# -------------------------------------------------------------- 進入點

def find_node() -> list[str]:
    """站上所有 RSI3000 node 的 service 名稱。"""
    return sorted({s.service_name for s in client.list_services(settle=2.0)
                   if s.service_name.startswith(f"{DEVICE}/")})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rsi3000-gui", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--node", help="node 的 service 名稱,例如 RSI3000/COM6;"
                                       "不給則找站上唯一的那一台")
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    with client.session("rsi3000-gui"):
        node = args.node
        if node is None:
            found = find_node()
            if len(found) != 1:
                what = "沒有找到任何 RSI3000 node" if not found else f"有好幾台:{', '.join(found)}"
                print(f"{what};用 --node 指定", file=sys.stderr)
                return 1
            node = found[0]
        link = Link(node)
        window = Rsi3000Window(link)
        window.show()
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
