"""PySide6 主視窗(README 5.3)。

把六個面板組起來,並提供 launch 檔一鍵啟動(README 3.1)。
視窗本身不認識任何裝置 —— 選到哪個 node,面板就去問那個 node 要 `describe`。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit,
    QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from ..cli.common import session
from .bridge import Station
from .panels import (
    MONO, CommandPanel, ConsolePanel, LogPanel, NodeList, PlanPanel, ReportPanel,
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
    """選一個 launch 檔一鍵啟動整站,輸出直接顯示在下面。"""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root
        self._process: QProcess | None = None

        self._choose = QPushButton("選擇 launch 檔並啟動")
        self._choose.clicked.connect(self._pick)
        self._stop = QPushButton("關閉整站")
        self._stop.setEnabled(False)
        self._stop.clicked.connect(self._terminate)
        self._state = QLabel("尚未啟動")

        self._output = QPlainTextEdit(readOnly=True)
        self._output.setFont(MONO)
        self._output.setMaximumBlockCount(2000)

        bar = QHBoxLayout()
        bar.addWidget(self._choose)
        bar.addWidget(self._stop)
        bar.addWidget(self._state, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self._output, 1)

    def _pick(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "選擇 launch 檔", str(self._root / "launch"), "YAML (*.yaml *.yml)"
        )
        if path:
            self._start(Path(path))

    def _start(self, config: Path) -> None:
        if self._process is not None:
            self._state.setText("已經有一個 launch 在跑了")
            return
        # 走 `st launch` 而不是自己再寫一份啟動邏輯:依序啟動、等 status ok、
        # 反向關閉這些語意只能有一個實作(README 3.1)。
        self._process = QProcess(self)
        self._process.setProgram(sys.executable)
        self._process.setArguments(
            ["-u", "-m", "teststation.cli.main", "launch", str(config)]
        )
        self._process.setWorkingDirectory(str(self._root))
        self._process.setProcessChannelMode(QProcess.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._drain)
        self._process.finished.connect(self._finished)
        self._process.start()
        self._state.setText(f"啟動中:{config.name}")
        self._choose.setEnabled(False)
        self._stop.setEnabled(True)

    def _drain(self) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        self._output.appendPlainText(text.rstrip())

    def _finished(self, code: int, _status) -> None:
        self._state.setText(f"launch 已結束(離開碼 {code})")
        self._choose.setEnabled(True)
        self._stop.setEnabled(False)
        self._process = None

    def _terminate(self) -> None:
        if self._process is not None:
            self._state.setText("關閉中…")
            self._process.terminate()

    def shutdown(self) -> None:
        """UI 關掉時一併收掉 launcher,不要留下佔著 COM 的孤兒 node。"""
        if self._process is not None:
            self._process.terminate()
            if not self._process.waitForFinished(10000):
                self._process.kill()


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
        self._log = LogPanel(self.station, root / "logs")
        self._report = ReportPanel()
        self._tools = TopicToolsPanel(self.station)
        self._values = ValueTable(self.station)
        self._launch = LaunchBar(root)

        tabs = QTabWidget()
        tabs.addTab(self._commands, "命令")
        tabs.addTab(self._console, "DUT console")
        tabs.addTab(self._plans, "測試計畫")
        tabs.addTab(self._log, "Log")
        tabs.addTab(self._report, "報告")
        tabs.addTab(self._tools, "topic 工具")
        tabs.addTab(self._launch, "啟動整站")

        self._tabs = tabs
        self._log.report_requested.connect(self._show_report)

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
        self.statusBar().showMessage("等待 node 出現…")
        self.station.nodes_changed.connect(
            lambda names: self.statusBar().showMessage(
                f"{len(names)} 個 node" if names else "沒有 node —— 用「啟動整站」分頁")
        )
        self.station.start()

    def _show_report(self, path: str) -> None:
        self._report.show_report(path)
        self._tabs.setCurrentWidget(self._report)

    def _on_node(self, node: str) -> None:
        self._commands.set_node(node)
        self._console.set_node(node)

    def closeEvent(self, event) -> None:
        self._launch.shutdown()
        self.station.stop()
        super().closeEvent(event)


def run_ui(argv: list[str] | None = None) -> int:
    app = QApplication(argv or sys.argv[:1])
    root = project_root()
    with session("st-ui"):
        window = MainWindow(root)
        window.show()
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(run_ui())
