"""UI 的各個面板(README 5.3)。

**通用 UI,不為個別裝置撰寫畫面。** 這裡沒有任何一行認識 BTM-4208SD 或 PFR-100L:
按鈕是照 `describe` 長出來的,數值表是照資料 topic 長出來的。
新增一種裝置不用動這個檔。
"""

from __future__ import annotations

import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .bridge import Station

STATUS_COLORS = {
    "ok": "#2e7d32",
    "reconnecting": "#ef6c00",
}
UNKNOWN_COLOR = "#9e9e9e"
ERROR_COLOR = "#c62828"
MONO = QFont("Consolas")


def status_color(status: str) -> QColor:
    if status.startswith("error"):
        return QColor(ERROR_COLOR)
    return QColor(STATUS_COLORS.get(status, UNKNOWN_COLOR))


# ------------------------------------------------------------- 1. Node 清單

class NodeList(QGroupBox):
    """從 eCAL 註冊資訊自動列出,燈號依 `status` topic。"""

    selected = Signal(str)

    def __init__(self, station: Station) -> None:
        super().__init__("Node")
        self._statuses: dict[str, str] = {}
        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_selection)

        layout = QVBoxLayout(self)
        layout.addWidget(self._list)

        station.nodes_changed.connect(self._set_nodes)
        station.node_status.connect(self._set_status)

    def current(self) -> str | None:
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _set_nodes(self, nodes: list[str]) -> None:
        keep = self.current()
        self._list.clear()
        for name in nodes:
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, name)
            item.setForeground(status_color(self._statuses.get(name, "")))
            item.setToolTip(self._statuses.get(name, "no heartbeat"))
            self._list.addItem(item)
            if name == keep:
                self._list.setCurrentItem(item)
        if keep is None and nodes:
            self._list.setCurrentRow(0)

    def _set_status(self, node: str, status: str) -> None:
        self._statuses[node] = status
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.data(Qt.UserRole) == node:
                item.setForeground(status_color(status))
                item.setToolTip(status)

    def _on_selection(self, current: QListWidgetItem | None, _previous) -> None:
        if current:
            self.selected.emit(current.data(Qt.UserRole))


# ----------------------------------------------------------- 2. 命令面板

class CommandPanel(QGroupBox):
    """依 `describe` 自動產生按鈕與輸入框,另附自由輸入的命令列與歷史。"""

    def __init__(self, station: Station) -> None:
        super().__init__("命令")
        self._station = station
        self._node: str | None = None

        self._generated = QWidget()
        self._generated_layout = QVBoxLayout(self._generated)
        self._generated_layout.setContentsMargins(0, 0, 0, 0)

        self._free = QLineEdit()
        self._free.setPlaceholderText("自由輸入,例如 set_channel ch3 SoC_heatsink")
        self._free.returnPressed.connect(self._send_free)
        self._free.setFont(MONO)

        self._history = QPlainTextEdit(readOnly=True)
        self._history.setFont(MONO)

        layout = QVBoxLayout(self)
        layout.addWidget(self._generated)
        layout.addWidget(self._free)
        layout.addWidget(QLabel("歷史"))
        layout.addWidget(self._history, 1)

        station.describe_ready.connect(self._build)
        station.reply.connect(self._log_reply)

    def set_node(self, node: str) -> None:
        self._node = node
        self.setTitle(f"命令 — {node}")
        self._clear()
        self._generated_layout.addWidget(QLabel("讀取命令清單…"))
        self._station.describe(node)

    def _clear(self) -> None:
        while self._generated_layout.count():
            item = self._generated_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _build(self, node: str, commands: list[dict]) -> None:
        if node != self._node:
            return  # 使用者已經換選別的 node 了
        self._clear()
        if not commands:
            self._generated_layout.addWidget(QLabel("拿不到 describe(node 還活著嗎?)"))
            return
        for command in commands:
            self._generated_layout.addWidget(self._row(node, command))

    def _row(self, node: str, command: dict) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)

        inputs: list[tuple[str, QWidget]] = []
        for spec in command.get("args", []):
            label = spec["name"] + (f" ({spec['unit']})" if "unit" in spec else "")
            layout.addWidget(QLabel(label))
            # 有 min/max 就用 spin box,範圍限制直接落在 widget 上(README 2.3)。
            if "min" in spec or "max" in spec:
                field = QDoubleSpinBox()
                field.setDecimals(3)
                field.setMinimum(float(spec.get("min", -1e9)))
                field.setMaximum(float(spec.get("max", 1e9)))
            else:
                field = QLineEdit()
                field.setPlaceholderText(spec["name"])
            layout.addWidget(field)
            inputs.append((spec["name"], field))

        button = QPushButton(command.get("label") or command["name"])
        button.setToolTip(command["name"])
        if command.get("style") == "danger":
            button.setStyleSheet(f"background-color: {ERROR_COLOR}; color: white;")
        button.clicked.connect(
            lambda _checked=False, n=node, c=command["name"], f=inputs:
            self._send(n, c, f)
        )
        layout.addWidget(button)
        layout.addStretch()
        return row

    def _send(self, node: str, method: str, inputs: list[tuple[str, QWidget]]) -> None:
        parts = [method]
        for _name, field in inputs:
            if isinstance(field, QDoubleSpinBox):
                parts.append(f"{field.value():g}")
            else:
                parts.append(field.text().strip())
        self._station.call(node, " ".join(p for p in parts if p))

    def _send_free(self) -> None:
        line = self._free.text().strip()
        if line and self._node:
            self._station.call(self._node, line)
            self._free.clear()

    def _log_reply(self, node: str, line: str, reply: str) -> None:
        colour = ERROR_COLOR if reply.startswith("ERR") else STATUS_COLORS["ok"]
        self._history.appendHtml(
            f'<span style="color:{UNKNOWN_COLOR}">{node}</span> &gt; {line}<br>'
            f'<span style="color:{colour}">{reply}</span>'
        )


# --------------------------------------------------------- 3. 即時數值表

class ValueTable(QGroupBox):
    """所有資料 topic 的最新值、`name`、單位、更新時間。"""

    COLUMNS = ["TOPIC", "NAME", "VALUE", "UNIT", "UPDATED"]

    def __init__(self, station: Station) -> None:
        super().__init__("即時數值")
        self._rows: dict[str, int] = {}
        self._table = QTableWidget(0, len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.setFont(MONO)

        layout = QVBoxLayout(self)
        layout.addWidget(self._table)

        station.measurement.connect(self._update)

    def _update(self, topic: str, payload: dict) -> None:
        row = self._rows.get(topic)
        if row is None:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._rows[topic] = row
            self._table.setItem(row, 0, QTableWidgetItem(topic))
        cells = [
            str(payload.get("name", "")),
            f"{payload.get('v', '')}",
            str(payload.get("unit", "")),
            str(payload.get("t", "")),
        ]
        for offset, text in enumerate(cells, start=1):
            self._table.setItem(row, offset, QTableWidgetItem(text))


# ------------------------------------------------------ 4. DUT console 分頁

class ConsolePanel(QWidget):
    """與 `st console` 相同的雙窗格:上半 DUT 原始收發,下半 node activity。"""

    def __init__(self, station: Station) -> None:
        super().__init__()
        self._station = station
        self._node: str | None = None

        self._dut = QPlainTextEdit(readOnly=True)
        self._dut.setFont(MONO)
        self._activity = QPlainTextEdit(readOnly=True)
        self._activity.setFont(MONO)
        self._entry = QLineEdit()
        self._entry.setPlaceholderText("輸入後 Enter 送給 DUT(send <text>)")
        self._entry.setFont(MONO)
        self._entry.returnPressed.connect(self._send)

        top = QGroupBox("DUT console")
        QVBoxLayout(top).addWidget(self._dut)
        bottom = QGroupBox("node activity")
        QVBoxLayout(bottom).addWidget(self._activity)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(top)
        splitter.addWidget(bottom)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter, 1)
        layout.addWidget(self._entry)

        station.raw_line.connect(self._on_raw)
        station.activity.connect(self._on_activity)
        station.reply.connect(self._on_reply)

    def set_node(self, node: str) -> None:
        self._node = node
        self._dut.clear()
        self._activity.clear()
        self._dut.appendPlainText(f"--- {node}/raw ---")
        self._station.watch_console(node)

    def _on_raw(self, _node: str, text: str) -> None:
        self._dut.appendPlainText(f"[RX] {text}")

    def _on_activity(self, _node: str, payload: dict) -> None:
        self._activity.appendPlainText(
            f"{payload.get('t', '')}  {payload.get('kind', '')} {payload.get('text', '')}"
        )

    def _on_reply(self, _node: str, line: str, reply: str) -> None:
        if line.startswith("send ") and reply.startswith("ERR"):
            self._dut.appendPlainText(reply)

    def _send(self) -> None:
        text = self._entry.text().strip()
        if not text or not self._node:
            return
        self._dut.appendPlainText(f"[TX] > {text}")
        self._station.call(self._node, f"send {text}")
        self._entry.clear()


# ---------------------------------------------------------- 5. 測試計畫

class PlanPanel(QGroupBox):
    """選擇計畫、Run / Abort、進度與輸出。命令送給 `Main-Control`。"""

    SERVICE = "Main-Control"

    def __init__(self, station: Station, plans_dir: Path) -> None:
        super().__init__("測試計畫")
        self._station = station
        self._plans_dir = plans_dir

        self._plans = QComboBox()
        self._output = QPlainTextEdit(readOnly=True)
        self._output.setFont(MONO)
        run = QPushButton("Run")
        run.clicked.connect(self._run)
        abort = QPushButton("Abort")
        abort.setStyleSheet(f"background-color: {ERROR_COLOR}; color: white;")
        abort.clicked.connect(
            lambda: self._station.call(self.SERVICE, "abort"))

        controls = QHBoxLayout()
        controls.addWidget(self._plans, 1)
        controls.addWidget(run)
        controls.addWidget(abort)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self._output, 1)

        station.reply.connect(self._on_reply)
        self.refresh()

    def refresh(self) -> None:
        self._plans.clear()
        if self._plans_dir.is_dir():
            self._plans.addItems(sorted(p.name for p in self._plans_dir.glob("*.py")))
        if not self._plans.count():
            self._plans.addItem("(plans/ 裡沒有計畫)")

    def _run(self) -> None:
        plan = self._plans.currentText()
        if plan.startswith("("):
            return
        self._station.call(self.SERVICE, f"run {plan}")

    def _on_reply(self, service: str, line: str, reply: str) -> None:
        if service == self.SERVICE:
            self._output.appendPlainText(f"> {line}\n{reply}")


# -------------------------------------------------------------- 6. Log 控制

class LogPanel(QGroupBox):
    """Start / Mark / Stop、開啟最近一份報告。命令送給 `Log`。"""

    SERVICE = "Log"

    def __init__(self, station: Station, logs_dir: Path) -> None:
        super().__init__("Log")
        self._station = station
        self._logs_dir = logs_dir

        self._run_name = QLineEdit()
        self._run_name.setPlaceholderText("run 名稱,例如 burn_in_45C")
        self._mark_text = QLineEdit()
        self._mark_text.setPlaceholderText("事件標記,例如 DUT power on")
        self._mark_text.returnPressed.connect(self._mark)

        start = QPushButton("Start")
        start.clicked.connect(self._start)
        mark = QPushButton("Mark")
        mark.clicked.connect(self._mark)
        stop = QPushButton("Stop")
        stop.clicked.connect(lambda: self._station.call(self.SERVICE, "stop"))
        report = QPushButton("開啟最近報告")
        report.clicked.connect(self._open_report)

        self._state = QLabel("")
        self._state.setWordWrap(True)

        form = QFormLayout(self)
        form.addRow(self._run_name, start)
        form.addRow(self._mark_text, mark)
        form.addRow(stop, report)
        form.addRow(self._state)

        station.reply.connect(self._on_reply)

    def _start(self) -> None:
        name = self._run_name.text().strip()
        if name:
            self._station.call(self.SERVICE, f"start {name}")

    def _mark(self) -> None:
        text = self._mark_text.text().strip()
        if text:
            self._station.call(self.SERVICE, f"mark {text}")
            self._mark_text.clear()

    def _open_report(self) -> None:
        reports = sorted(self._logs_dir.glob("*/report.html"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        if not reports:
            self._state.setText(f"{self._logs_dir} 底下還沒有 report.html")
            return
        webbrowser.open(reports[0].resolve().as_uri())
        self._state.setText(f"開啟 {reports[0]}")

    def _on_reply(self, service: str, line: str, reply: str) -> None:
        if service == self.SERVICE:
            colour = ERROR_COLOR if reply.startswith("ERR") else STATUS_COLORS["ok"]
            self._state.setText(f'{line} → <span style="color:{colour}">{reply}</span>')
            self._state.setTextFormat(Qt.RichText)
