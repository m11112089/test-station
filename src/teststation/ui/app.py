"""PySide6 主視窗(README 5.3)。

站台**唯一的人機介面**:命令、數值、DUT console、測試計畫、記錄、
topic 工具,以及依序啟動整站。

報告不在這裡 —— 事後用 `uv run visualization <data.csv>` 產出來自己開。

視窗本身不認識任何裝置 —— 選到哪個 node,面板就去問那個 node 要 `describe`。
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit,
    QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from ..client import session
from ..core.topics import LOG_SERVICE
from .bridge import Station
from .launch import LOG_RUN, Launcher
from .panels import (
    MONO, CommandPanel, ConsolePanel, LogPanel, NodeList, PlanPanel,
    TopicToolsPanel, ValueTable,
)

WINDOW_TITLE = "test-station"


def project_root() -> Path:
    """從當前目錄往上找專案根(有 pyproject.toml 的那層),找不到就用當前目錄。"""
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return here


class LaunchBar(QWidget):
    """選一個 launch 檔一鍵啟動整站,輸出直接顯示在下面。

    node 是這個行程的子行程 —— **關掉 UI 就是結束測試**。
    """

    def __init__(self, station: Station, root: Path) -> None:
        super().__init__()
        self._root = root
        self._launcher = Launcher(station, root, self)

        self._choose = QPushButton("選擇 launch 檔並啟動")
        self._choose.clicked.connect(self._pick)
        self._stop = QPushButton("關閉整站")
        self._stop.setEnabled(False)
        self._stop.clicked.connect(lambda: self._launcher.shutdown(keep_log=True))
        self._save = QPushButton("存成 launch 檔")
        self._save.setToolTip("把現在的 node 與設定值存下來,下次直接開")
        self._save.clicked.connect(self._save_launch)
        self._state = QLabel("尚未啟動")

        self._output = QPlainTextEdit(readOnly=True)
        self._output.setFont(MONO)
        self._output.setMaximumBlockCount(2000)

        bar = QHBoxLayout()
        bar.addWidget(self._choose)
        bar.addWidget(self._stop)
        bar.addWidget(self._save)
        bar.addWidget(self._state, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self._output, 1)

        self._launcher.output.connect(self._output.appendPlainText)
        self._launcher.state.connect(self._show_state)

    def _pick(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "選擇 launch 檔", str(self._root / "launch"), "YAML (*.yaml *.yml)"
        )
        if not path:
            return
        if self._launcher.running:
            self._state.setText("已經有 node 在跑了,先關閉整站")
            return
        self._choose.setEnabled(False)
        self._stop.setEnabled(True)
        self._launcher.start(Path(path))

    def _show_state(self, text: str, started: int, failed: int) -> None:
        note = f"{started} 個 node" + (f",{failed} 個未就緒" if failed else "")
        self._state.setText(f"{text} · {note}" if started or failed else text)
        self._choose.setEnabled(not self._launcher.running)
        self._stop.setEnabled(self._launcher.running)

    # ------------------------------------------------------- 存成 launch 檔

    def _save_launch(self) -> None:
        """把這次啟動的 node 與**目前的設定值**寫成一份 launch 檔。

        設定值來自 `Station.settings` —— 只有 node 自己標成 `setting` 的命令,
        而且同一個設定取最後一次。所以 `output_on` 這類動作不會被存進去,
        下次開檔也不會意外送電。
        """
        specs = self._launcher.specs
        if not specs and not self._launcher.station.settings:
            self._state.setText("還沒有東西可以存 —— 先啟動整站或設定一些值")
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "存成 launch 檔", str(self._root / "launch" / "station.yaml"),
            "YAML (*.yaml *.yml)")
        if not path:
            return

        settings = self._launcher.station.settings
        # Log 是 UI 自己起的,不在 specs 裡,但它的設定(要錄哪些 topic)要存下來,
        # 否則下次開檔會變成什麼都沒選。
        nodes = []
        log_settings = sorted(settings.get(LOG_SERVICE, {}).values())
        if log_settings:
            nodes.append({"run": LOG_RUN, "setting": log_settings})
        for spec in specs:
            if spec["run"] == LOG_RUN:
                continue        # 上面已經寫過一筆了,而且啟動時本來就會跳過
            entry = {"run": spec["run"]}
            if spec.get("args"):
                entry["args"] = spec["args"]
            service = self._launcher.service_of(spec["run"])
            lines = sorted(settings.get(service, {}).values()) if service else []
            if lines:
                entry["setting"] = lines
            nodes.append(entry)

        document = {"nodes": nodes, "ready_timeout": self._launcher.ready_timeout}
        Path(path).write_text(
            "# 由 UI 產生:node 清單與目前的設定值\n"
            + yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        kept = sum(len(n.get("setting", ())) for n in nodes)
        self._state.setText(f"已存 {path}({len(nodes)} 個 node、{kept} 筆設定)")

    def start_log(self) -> None:
        self._launcher.start_log()

    def shutdown(self) -> None:
        """UI 關掉時一併收掉所有 node,Log 最後關(README 3.1 的反向順序)。"""
        self._launcher.shutdown()


class MainWindow(QMainWindow):
    def __init__(self, root: Path) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1280, 860)

        self.station = Station(self)
        self._nodes = NodeList(self.station)
        self._commands = CommandPanel(self.station)
        self._console = ConsolePanel(self.station)
        self._plans = PlanPanel(self.station, root / "plans")
        self._log = LogPanel(self.station)
        self._tools = TopicToolsPanel(self.station)
        self._values = ValueTable(self.station)
        self._launch = LaunchBar(self.station, root)

        tabs = QTabWidget()
        tabs.addTab(self._commands, "命令")
        tabs.addTab(self._console, "DUT console")
        tabs.addTab(self._plans, "測試計畫")
        tabs.addTab(self._log, "記錄")
        tabs.addTab(self._tools, "topic 工具")
        tabs.addTab(self._launch, "啟動整站")

        self._tabs = tabs

        upper = QSplitter(Qt.Horizontal)
        upper.addWidget(self._nodes)
        upper.addWidget(tabs)
        upper.setSizes([260, 1020])

        main = QSplitter(Qt.Vertical)
        main.addWidget(upper)
        main.addWidget(self._values)
        main.setSizes([560, 300])
        self.setCentralWidget(main)

        self._nodes.selected.connect(self._on_node)
        # Log 一開始就起來 —— 忘記啟動它的代價是整場測試沒錄到。
        self._launch.start_log()
        self.statusBar().showMessage("等待 node 出現…")
        self.station.nodes_changed.connect(
            lambda names: self.statusBar().showMessage(
                f"{len(names)} 個 node" if names else "沒有 node —— 用「啟動整站」分頁")
        )
        self.station.start()

    def _on_node(self, node: str) -> None:
        self._commands.set_node(node)
        self._console.set_node(node)
        # Log 有自己的面板(勾選 topic、run 清單),通用命令按鈕做不到那些,
        # 所以選到它就切過去。選回一般裝置再切回命令面板。
        # 只在這兩個「跟著選取走」的分頁之間換 —— 使用者正在看別的分頁時不打擾他。
        per_node = (self._log, self._commands)
        if self._tabs.currentWidget() in per_node:
            self._tabs.setCurrentWidget(
                self._log if node == LOG_SERVICE else self._commands)

    def closeEvent(self, event) -> None:
        self._launch.shutdown()
        self.station.stop()
        super().closeEvent(event)


def run_ui(argv: list[str] | None = None) -> int:
    app = QApplication(argv or sys.argv[:1])
    root = project_root()
    with session("teststation-ui"):
        window = MainWindow(root)
        window.show()
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(run_ui())
