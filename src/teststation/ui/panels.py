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
    QAbstractItemView, QApplication, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ..core.topics import is_common
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
    """DUT 雙窗格:上半 DUT 原始收發,下半 node activity。

    調整 DUT profile 的 `parse()` 時用來對照:上面有這行、下面沒有對應的 `PUB`,
    就是 `parse()` 沒接到。
    """

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
    """Start / Mark / Stop、看目前錄到哪、開啟過去的報告。命令送給 `Log`。

    狀態是 Log node 推過來的(`Log/state`),不是這裡猜的 —— 所以 `st` 那邊
    下的 `start`,這個畫面一樣會亮起來。
    """

    SERVICE = "Log"

    report_requested = Signal(str)
    """使用者想看某份報告(帶 report.html 的路徑)。由主視窗決定在哪顯示。"""

    def __init__(self, station: Station, logs_dir: Path) -> None:
        super().__init__("Log")
        self._station = station
        self._logs_dir = logs_dir
        self._recording = False

        self._state = QLabel("等待 Log node…")
        self._state.setWordWrap(True)
        self._state.setTextFormat(Qt.RichText)

        # 狀態是 1 Hz 推過來的,會不停覆蓋自己 —— 回覆與錯誤得有自己的一行,
        # 不然按下去看到的訊息一秒後就被蓋掉了。
        self._notice = QLabel("")
        self._notice.setWordWrap(True)
        self._notice.setTextFormat(Qt.RichText)

        self._run_name = QLineEdit()
        self._run_name.setPlaceholderText("run 名稱,例如 burn_in_45C")
        self._run_name.returnPressed.connect(self._start)
        self._mark_text = QLineEdit()
        self._mark_text.setPlaceholderText("事件標記,例如 DUT power on")
        self._mark_text.returnPressed.connect(self._mark)

        self._start_btn = QPushButton("Start")
        self._start_btn.clicked.connect(self._start)
        self._mark_btn = QPushButton("Mark")
        self._mark_btn.clicked.connect(self._mark)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setStyleSheet(f"background-color: {ERROR_COLOR}; color: white;")
        self._stop_btn.clicked.connect(lambda: self._station.call(self.SERVICE, "stop"))

        self._topics = QListWidget()
        self._topics.setFont(MONO)
        self._topics.setMaximumHeight(140)
        apply_topics = QPushButton("套用選擇")
        apply_topics.clicked.connect(self._apply_topics)
        all_topics = QPushButton("全選")
        all_topics.clicked.connect(lambda: self._check_all(True))
        no_topics = QPushButton("全不選")
        no_topics.clicked.connect(lambda: self._check_all(False))

        self._runs = QListWidget()
        self._runs.setFont(MONO)
        self._runs.itemDoubleClicked.connect(lambda item: self._open(item))
        open_btn = QPushButton("看報告")
        open_btn.clicked.connect(lambda: self._open(self._runs.currentItem()))
        browser_btn = QPushButton("用瀏覽器開")
        browser_btn.clicked.connect(
            lambda: self._open(self._runs.currentItem(), external=True))
        refresh = QPushButton("重新整理")
        refresh.clicked.connect(self.refresh_runs)

        form = QFormLayout()
        form.addRow(self._run_name, self._start_btn)
        form.addRow(self._mark_text, self._mark_btn)
        form.addRow(self._stop_btn, QLabel(""))

        buttons = QHBoxLayout()
        buttons.addWidget(open_btn)
        buttons.addWidget(browser_btn)
        buttons.addWidget(refresh)
        buttons.addStretch()

        picker = QHBoxLayout()
        picker.addWidget(all_topics)
        picker.addWidget(no_topics)
        picker.addWidget(apply_topics)
        picker.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(self._state)
        layout.addWidget(self._notice)
        layout.addWidget(QLabel("要記錄哪些 topic"))
        layout.addWidget(self._topics)
        layout.addLayout(picker)
        layout.addLayout(form)
        layout.addWidget(QLabel("過去的 run"))
        layout.addWidget(self._runs, 1)
        layout.addLayout(buttons)

        station.topics_changed.connect(self._set_topics)
        station.log_state.connect(self._on_state)
        station.reply.connect(self._on_reply)
        self._set_recording(False)
        self.refresh_runs()

    # ------------------------------------------------------------ topic 選擇

    def _set_topics(self, topics: list[str]) -> None:
        """列出可以記錄的 topic。status / activity 不在其中 —— 那是 node 自己的狀態。"""
        checked = set(self.selected_topics())
        self._topics.clear()
        for topic in topics:
            if is_common(topic):
                continue
            item = QListWidgetItem(topic)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if topic in checked else Qt.Unchecked)
            self._topics.addItem(item)

    def selected_topics(self) -> list[str]:
        return [self._topics.item(i).text() for i in range(self._topics.count())
                if self._topics.item(i).checkState() == Qt.Checked]

    def _check_all(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for i in range(self._topics.count()):
            self._topics.item(i).setCheckState(state)

    def _apply_topics(self) -> None:
        chosen = self.selected_topics()
        if not chosen:
            self._notice.setText(
                f'<span style="color:{ERROR_COLOR}">至少要選一個 topic</span>')
            return
        self._station.call(self.SERVICE, "record " + " ".join(chosen))

    def _show_selection(self, selected: list[str]) -> None:
        """把 Log 回報的選擇反映到勾選框 —— 設定可能來自 launch 檔,不是這裡按的。"""
        wanted = set(selected)
        for i in range(self._topics.count()):
            item = self._topics.item(i)
            item.setCheckState(Qt.Checked if item.text() in wanted else Qt.Unchecked)

    # ------------------------------------------------------------ 狀態顯示

    def _on_state(self, state: dict) -> None:
        if not self._recording and "selected" in state:
            self._show_selection(state["selected"])
        recording = bool(state.get("recording"))
        if recording:
            self._state.setText(
                f'<b style="color:{STATUS_COLORS["ok"]}">● 錄製中</b> '
                f'{state.get("run", "")}<br>'
                f'{state.get("rows", 0)} 筆 · {state.get("marks", 0)} 個 mark · '
                f'{state.get("topics", 0)} 個 topic · 起於 {state.get("started", "")}'
                f'<br><span style="color:{UNKNOWN_COLOR}">{state.get("dir", "")}</span>'
            )
        else:
            self._state.setText(
                f'<span style="color:{UNKNOWN_COLOR}">○ 未錄製</span>')
        if recording != self._recording:
            self._set_recording(recording)
            if not recording:
                self.refresh_runs()   # 剛 stop,報告應該出來了

    def _set_recording(self, recording: bool) -> None:
        self._recording = recording
        self._start_btn.setEnabled(not recording)
        self._run_name.setEnabled(not recording)
        self._mark_btn.setEnabled(recording)
        self._mark_text.setEnabled(recording)
        self._stop_btn.setEnabled(recording)

    # -------------------------------------------------------------- 命令

    def _start(self) -> None:
        name = self._run_name.text().strip()
        if name:
            self._station.call(self.SERVICE, f"start {name}")

    def _mark(self) -> None:
        text = self._mark_text.text().strip()
        if text:
            self._station.call(self.SERVICE, f"mark {text}")
            self._mark_text.clear()

    def _on_reply(self, service: str, line: str, reply: str) -> None:
        if service != self.SERVICE:
            return
        colour = ERROR_COLOR if reply.startswith("ERR") else UNKNOWN_COLOR
        self._notice.setText(f'<span style="color:{colour}">{reply}</span>')

    # ------------------------------------------------------------ run 清單

    def refresh_runs(self) -> None:
        """列出 logs/ 底下的 run,最新的在上面。"""
        self._runs.clear()
        if not self._logs_dir.is_dir():
            self._runs.addItem(f"({self._logs_dir} 還不存在)")
            return
        folders = sorted((p for p in self._logs_dir.iterdir() if p.is_dir()),
                         key=lambda p: p.name, reverse=True)
        if not folders:
            self._runs.addItem("(還沒有任何 run)")
            return
        for folder in folders:
            rows = self._count_rows(folder / "data.csv")
            built = (folder / "report.html").is_file()
            note = "報告已產生" if built else "按「看報告」產生"
            item = QListWidgetItem(f"{folder.name}    {rows} 筆 · {note}")
            item.setData(Qt.UserRole, str(folder))
            if not rows:
                item.setForeground(QColor(UNKNOWN_COLOR))
            self._runs.addItem(item)

    @staticmethod
    def _count_rows(csv_path: Path) -> int:
        if not csv_path.is_file():
            return 0
        with csv_path.open(encoding="utf-8", errors="replace") as handle:
            return max(sum(1 for _ in handle) - 1, 0)   # 扣掉標題列

    def _open(self, item: QListWidgetItem | None, *, external: bool = False) -> None:
        """開報告。不存在就現場產一份 —— Log 只負責記錄,報告是事後的事。"""
        folder = item.data(Qt.UserRole) if item else None
        if not folder:
            return
        report = Path(folder) / "report.html"
        if not report.is_file():
            report = self._build_report(Path(folder))
            if report is None:
                return
            self.refresh_runs()
        if external:
            webbrowser.open(report.resolve().as_uri())
        else:
            self.report_requested.emit(str(report.resolve()))

    def _build_report(self, folder: Path) -> Path | None:
        """跟 `uv run visualization <data.csv>` 同一份實作。"""
        from ..visualization import build

        csv_path = folder / "data.csv"
        self._notice.setText(f"產生報告中…{csv_path}")
        QApplication.processEvents()      # 讓上面那行先畫出來
        try:
            return build(csv_path)
        except (ValueError, OSError) as exc:
            self._notice.setText(
                f'<span style="color:{ERROR_COLOR}">產生報告失敗:{exc}</span>')
            return None


# ---------------------------------------------------- 7. topic 工具(hz / pub)

class TopicToolsPanel(QGroupBox):
    """量發佈頻率、餵假資料 —— 跟 `st topic hz` / `st topic pub` 同一套。

    統計與 payload 處理都在 `teststation.client`,這裡只負責選 topic、顯示、觸發。
    """

    def __init__(self, station: Station) -> None:
        super().__init__("topic 工具")
        self._station = station

        self._topics = QComboBox()
        self._topics.setEditable(True)
        self._topics.setFont(MONO)
        measure = QPushButton("量頻率")
        measure.clicked.connect(self._measure)
        stop = QPushButton("停止")
        stop.clicked.connect(lambda: self._station.watch_rate(None))
        self._rate = QLabel("選一個 topic 再按「量頻率」")
        self._rate.setFont(MONO)

        self._pub_topic = QLineEdit()
        self._pub_topic.setPlaceholderText("test/fake/ch1")
        self._pub_topic.setFont(MONO)
        self._pub_body = QLineEdit('{"v": 1.0}')
        self._pub_body.setFont(MONO)
        self._pub_rate = QDoubleSpinBox()
        self._pub_rate.setRange(0.0, 100.0)
        self._pub_rate.setDecimals(1)
        self._pub_rate.setSpecialValueText("送一筆")
        self._pub_count = QDoubleSpinBox()
        self._pub_count.setRange(1, 10000)
        self._pub_count.setDecimals(0)
        self._pub_count.setValue(10)
        send = QPushButton("發佈")
        send.clicked.connect(self._publish)
        self._pub_state = QLabel("")

        top = QHBoxLayout()
        top.addWidget(self._topics, 1)
        top.addWidget(measure)
        top.addWidget(stop)

        pub = QHBoxLayout()
        pub.addWidget(QLabel("topic"))
        pub.addWidget(self._pub_topic, 1)
        pub.addWidget(QLabel("payload"))
        pub.addWidget(self._pub_body, 1)
        pub.addWidget(QLabel("Hz"))
        pub.addWidget(self._pub_rate)
        pub.addWidget(QLabel("筆數"))
        pub.addWidget(self._pub_count)
        pub.addWidget(send)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("量測發佈頻率"))
        layout.addLayout(top)
        layout.addWidget(self._rate)
        layout.addSpacing(12)
        layout.addWidget(QLabel("發佈假資料"))
        layout.addLayout(pub)
        layout.addWidget(self._pub_state)
        layout.addStretch()

        station.topics_changed.connect(self._set_topics)
        station.rate.connect(self._show_rate)
        station.published.connect(self._show_published)

    def _set_topics(self, topics: list[str]) -> None:
        current = self._topics.currentText()
        self._topics.clear()
        self._topics.addItems(topics)
        if current:
            self._topics.setCurrentText(current)

    def _measure(self) -> None:
        topic = self._topics.currentText().strip()
        if topic:
            self._rate.setText(f"量測 {topic} …(eCAL 配對約 2 秒)")
            self._station.watch_rate(topic)

    def _show_rate(self, topic: str, rate) -> None:
        if rate is None:
            self._rate.setText(f"{topic}\n還沒收到足夠的樣本")
            return
        self._rate.setText(
            f"{topic}\n{rate.hz:.3f} Hz   min {rate.min_gap:.3f}s   "
            f"max {rate.max_gap:.3f}s   std {rate.std:.5f}   樣本 {rate.samples}")

    def _publish(self) -> None:
        topic = self._pub_topic.text().strip()
        if not topic:
            return
        rate = self._pub_rate.value() or None
        count = int(self._pub_count.value()) if rate else None
        self._pub_state.setText(f"發佈中 {topic} …")
        self._station.publish(topic, self._pub_body.text(), rate, count)

    def _show_published(self, topic: str, sent: int, note: str) -> None:
        colour = ERROR_COLOR if note.startswith("ERR") else UNKNOWN_COLOR
        self._pub_state.setText(
            f'{topic} ← {sent} 筆 <span style="color:{colour}">{note}</span>')
        self._pub_state.setTextFormat(Qt.RichText)


# ------------------------------------------------------- 8. 報告檢視器

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    WEBVIEW = True
except ImportError:                                    # pragma: no cover
    QWebEngineView = None
    WEBVIEW = False


class ReportPanel(QWidget):
    """把 report.html 直接開在 UI 裡。

    報告是自足的檔案(plotly 內嵌),所以這裡就是一個 file:// 的網頁檢視器 ——
    跟使用者拿瀏覽器開看到的是同一份東西,沒有第二套繪圖邏輯。
    """

    def __init__(self) -> None:
        super().__init__()
        self._path: Path | None = None
        self._where = QLabel("還沒選報告 —— 去 Log 分頁挑一份 run")
        self._where.setWordWrap(True)

        reload_btn = QPushButton("重新載入")
        reload_btn.clicked.connect(lambda: self.show_report(self._path))
        browser_btn = QPushButton("用瀏覽器開")
        browser_btn.clicked.connect(self._external)

        bar = QHBoxLayout()
        bar.addWidget(reload_btn)
        bar.addWidget(browser_btn)
        bar.addWidget(self._where, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        if WEBVIEW:
            self._view = QWebEngineView()
            layout.addWidget(self._view, 1)
        else:
            self._view = None
            layout.addWidget(QLabel(
                "這台機器沒有 QtWebEngine,只能用瀏覽器開。"), 1)

    def show_report(self, path: str | Path | None) -> None:
        if not path:
            return
        self._path = Path(path)
        self._where.setText(str(self._path))
        if self._view is not None:
            from PySide6.QtCore import QUrl
            self._view.load(QUrl.fromLocalFile(str(self._path)))

    def _external(self) -> None:
        if self._path:
            webbrowser.open(self._path.as_uri())
