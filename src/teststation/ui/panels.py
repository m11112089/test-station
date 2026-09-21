"""UI 的各個面板(README 5.3)。

**通用 UI,不為個別裝置撰寫畫面。** 這裡沒有任何一行認識 BTM-4208SD 或 PFR-100L:
按鈕是照 `describe` 長出來的,數值表是照資料 topic 長出來的。
新增一種裝置不用動這個檔。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core.topics import LOG_SERVICE, channel_of, is_common, node_of
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
    """從 eCAL 註冊資訊自動列出,燈號依 `status` topic。

    Log 也列在這裡(看得到它的燈號與 activity),但**點它會切到「Log」分頁** ——
    控制它的地方只有一個,不會變成通用按鈕與專用面板兩套入口。
    """

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
    """所有資料 topic 的最新值、`name`、單位、時間戳。

    `TIME` 是 **node 在量測當下蓋的時間戳**(payload 的 `t`),不是 UI 收到的時間 ——
    跨 PC 時網路延遲不該讓畫面上的時間跟記錄下來的對不起來。
    """

    COLUMNS = ["TOPIC", "NAME", "VALUE", "UNIT", "TIME"]

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
    """要記錄哪些 topic。

    **沒有開始/停止按鈕。** Log node 一啟動就開好檔案了,勾完按「套用選擇」
    那一刻就開始寫 —— 少一個「我按了嗎」的狀態。

    狀態是 Log node 推過來的(`Log/state`),所以測試計畫用 `control` 改了選擇,
    這裡的勾選框也會跟著動。
    """

    SERVICE = LOG_SERVICE

    def __init__(self, station: Station) -> None:
        super().__init__("記錄")
        self._station = station
        self._node_selection: list[str] | None = None

        self._state = QLabel("等待 Log node…")
        self._state.setWordWrap(True)
        self._state.setTextFormat(Qt.RichText)

        # 狀態是 1 Hz 推過來的,會不停覆蓋自己 —— 回覆與錯誤得有自己的一行。
        self._notice = QLabel("")
        self._notice.setWordWrap(True)
        self._notice.setTextFormat(Qt.RichText)

        # 一個 node 一層,channel 掛在底下。node 那一列的勾選框就是「這台全選」。
        self._topics = QTreeWidget()
        self._topics.setFont(MONO)
        self._topics.setHeaderHidden(True)
        self._topics.itemChanged.connect(self._on_item_changed)

        apply_topics = QPushButton("套用選擇")
        apply_topics.setToolTip("按下去就開始記錄勾到的 topic")
        apply_topics.clicked.connect(self._apply_topics)
        all_topics = QPushButton("全選")
        all_topics.clicked.connect(lambda: self._check_all(True))
        no_topics = QPushButton("全不選")
        no_topics.clicked.connect(lambda: self._check_all(False))

        picker = QHBoxLayout()
        picker.addWidget(all_topics)
        picker.addWidget(no_topics)
        picker.addWidget(apply_topics)
        picker.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(self._state)
        layout.addWidget(self._notice)
        layout.addWidget(self._topics, 1)
        layout.addLayout(picker)

        station.topics_changed.connect(self._set_topics)
        station.log_state.connect(self._on_state)
        station.reply.connect(self._on_reply)

    # ------------------------------------------------------------ topic 選擇

    def _set_topics(self, topics: list[str]) -> None:
        """依 node 分層列出可以記錄的 topic。

        `status` / `activity` / `state` 不在其中 —— 那是 node 自己的狀態(README 2.2)。
        """
        checked = set(self.selected_topics())
        grouped: dict[str, list[str]] = {}
        for topic in topics:
            if is_common(topic):
                continue
            grouped.setdefault(node_of(topic), []).append(topic)

        with self._quiet():
            self._topics.clear()
            for node in sorted(grouped):
                parent = QTreeWidgetItem([node])
                # AutoTristate 讓 node 那一列自己反映「全選 / 部分 / 都沒選」。
                parent.setFlags(parent.flags() | Qt.ItemIsUserCheckable
                                | Qt.ItemIsAutoTristate)
                parent.setCheckState(0, Qt.Unchecked)
                for topic in sorted(grouped[node]):
                    child = QTreeWidgetItem([channel_of(topic)])
                    child.setData(0, Qt.UserRole, topic)
                    child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                    child.setCheckState(0, Qt.Checked if topic in checked
                                        else Qt.Unchecked)
                    parent.addChild(child)
                self._topics.addTopLevelItem(parent)
                parent.setExpanded(True)
            # 訊號關著的時候 AutoTristate 不會動,所以父項狀態要自己補。
            self._refresh_parents()

    @contextmanager
    def _quiet(self):
        """重建樹時把 itemChanged 關掉,不然父子連動會自己打自己。"""
        self._topics.blockSignals(True)
        try:
            yield
        finally:
            self._topics.blockSignals(False)

    def _leaves(self):
        for i in range(self._topics.topLevelItemCount()):
            parent = self._topics.topLevelItem(i)
            for j in range(parent.childCount()):
                yield parent.child(j)

    def selected_topics(self) -> list[str]:
        return [leaf.data(0, Qt.UserRole) for leaf in self._leaves()
                if leaf.checkState(0) == Qt.Checked]

    def _on_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        """勾 node 那一列 = 這台全選 / 全不選。

        Qt 的 AutoTristate 只做子 → 父(父自己顯示成部分勾選),父 → 子要自己來。
        """
        if item.childCount() == 0 or item.checkState(0) == Qt.PartiallyChecked:
            return
        with self._quiet():
            for i in range(item.childCount()):
                item.child(i).setCheckState(0, item.checkState(0))

    def _check_all(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        with self._quiet():
            for i in range(self._topics.topLevelItemCount()):
                parent = self._topics.topLevelItem(i)
                parent.setCheckState(0, state)
                for j in range(parent.childCount()):
                    parent.child(j).setCheckState(0, state)

    def _refresh_parents(self) -> None:
        """訊號關著的時候 AutoTristate 不會動,所以父項的狀態手動補一次。"""
        for i in range(self._topics.topLevelItemCount()):
            parent = self._topics.topLevelItem(i)
            states = {parent.child(j).checkState(0) for j in range(parent.childCount())}
            parent.setCheckState(0, states.pop() if len(states) == 1
                                 else Qt.PartiallyChecked)

    def _apply_topics(self) -> None:
        """送出選擇。Log 收到就立刻開始寫 —— 沒有第二個「開始」動作。"""
        self._station.call(self.SERVICE, "record " + " ".join(self.selected_topics()))

    def _sync_selection(self, reported: list[str]) -> None:
        """把 Log 回報的選擇反映到勾選框。

        **只在它真的變了的時候動**。`Log/state` 是 1 Hz 推過來的,每次都重寫的話
        使用者勾第二個框之前,第一個就被蓋回去了 —— 畫面會跟人搶輸入。
        """
        if reported == self._node_selection:
            return
        self._node_selection = list(reported)
        wanted = set(reported)
        with self._quiet():
            for leaf in self._leaves():
                leaf.setCheckState(0, Qt.Checked
                                   if leaf.data(0, Qt.UserRole) in wanted
                                   else Qt.Unchecked)
            self._refresh_parents()

    # ------------------------------------------------------------ 狀態顯示

    def _on_state(self, state: dict) -> None:
        if "selected" in state:
            self._sync_selection(state["selected"])
        if not state.get("recording"):
            self._state.setText(
                f'<span style="color:{UNKNOWN_COLOR}">○ 沒有開啟的檔案</span>')
            return
        colour = STATUS_COLORS["ok"] if state.get("selected") else UNKNOWN_COLOR
        mark = "●" if state.get("selected") else "○"
        self._state.setText(
            f'<b style="color:{colour}">{mark} 記錄中</b> '
            f'{state.get("rows", 0)} 筆 · {len(state.get("selected", ()))} 個 topic<br>'
            f'<span style="color:{UNKNOWN_COLOR}">{state.get("file", "")}</span>')

    def _on_reply(self, service: str, line: str, reply: str) -> None:
        if service != self.SERVICE:
            return
        colour = ERROR_COLOR if reply.startswith("ERR") else UNKNOWN_COLOR
        self._notice.setText(f'<span style="color:{colour}">{reply}</span>')


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
