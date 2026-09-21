"""依序啟動整站(README 3.1)。

類似 ros launch,但**不是等固定秒數,而是等 node 的 `status` 變成 `ok`**。
這件事很重要:node 未指定 `--port` 時會掃描所有 COM,掃描期間會暫時佔住別人的 port,
同時啟動會讓彼此誤判成「使用中」而跳過。所以下一個 node 必須等前一個掃完、port 佔住了才起。

(這也是 README 3.1 說明不使用 eCAL Sys 的原因:它只能等固定秒數,不理解 `status` 語意。)

就緒判定**直接用 `Station` 現成的探索與 status 訂閱**,不自己再接一次 eCAL ——
UI 本來就一直在收這兩樣東西。

每個 node 底下可以帶一份 `setting`,是它 ready 之後要送過去的設定命令:

```yaml
nodes:
  - run: log
    setting:
      - record BTM-4208SD/COM11/ch1 BTM-4208SD/COM11/ch3
  - run: btm_4208sd
    args: --port COM11
    setting:
      - set_channel ch3 SoC_heatsink
```

**只有 node 在 `describe` 裡標成 `"setting": true` 的命令才該寫在這裡。**
`output_on` 那種動作寫進來,開檔就會送電。UI 存檔時只會存設定,不會存動作。

**node 是 UI 的子行程。** 關掉 UI 就是結束測試:反向順序關閉,Log 最後關,
確保它收得到其他 node 的最後資料。

**Log 由 UI 一開就起,不經過 launch 檔。** 忘記把它列進去就整場沒錄到,那代價太大;
而且它沒有 port、不接在任何線上,本來就不像站上的一台裝置。launch 檔裡若還寫著
`run: log` 會被跳過(否則會有兩個 Log 搶同一個 service 名稱)。
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .. import client
from ..core.topics import LOG_SERVICE
from .bridge import Station

DEFAULT_READY_TIMEOUT = 30.0
LOG_RUN = "log"
"""`run:` 的名字。UI 自己起一個,所以 launch 檔裡的同名條目會被跳過。"""

NODE_MODULE = "teststation.nodes.{run}.node"
"""`run: btm_4208sd` → `python -m teststation.nodes.btm_4208sd.node`。

走模組路徑而不是 console script:名字不會跟系統上別的指令撞,而且 UI 用哪個
Python 跑,node 就用同一個。
"""


class Launcher(QObject):
    """把一份 launch yaml 依序跑起來,一次一個 node。

    每個步驟都由 signal 推動,沒有阻塞的迴圈 —— 畫面在啟動過程中照樣能動。
    """

    output = Signal(str)
    """要顯示在畫面上的一行(已加好前綴)。"""

    state = Signal(str, int, int)
    """(說明文字, 已啟動, 未就緒)。"""

    finished = Signal()

    def __init__(self, station: Station, root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._station = station
        self._root = root
        self._specs: list[dict] = []
        self._index = 0
        self._failed = 0
        self._ready_timeout = DEFAULT_READY_TIMEOUT
        self._processes: list[list] = []   # [label, QProcess],label 會被改寫
        self._log_process: QProcess | None = None

        self._before: set[str] = set()
        self._settings: list[str] = []
        self._waiting_for: str | None = None
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self._give_up)

        station.nodes_changed.connect(self._on_nodes)
        station.node_status.connect(self._on_status)

    @property
    def running(self) -> bool:
        return bool(self._processes)

    # --------------------------------------------------- 一直都在的 Log

    def start_log(self) -> None:
        """UI 起來就把 Log 帶起來。它不在 `_processes` 裡,所以永遠最後關。"""
        if self._log_process is not None:
            return
        self._log_process = self._spawn_process(LOG_RUN, [])
        self._log_process.readyReadStandardOutput.connect(
            lambda: self._drain(self._log_process, LOG_SERVICE))
        self.output.emit(f"--- starting {LOG_RUN}")

    @property
    def station(self) -> Station:
        return self._station

    @property
    def ready_timeout(self) -> float:
        return self._ready_timeout

    def service_of(self, run: str) -> str | None:
        """`run: btm_4208sd` 啟動起來之後註冊成哪個 service(`BTM-4208SD/COM11`)。

        存 launch 檔時要靠它把設定值配回正確的 node。
        """
        for index, (label, _process) in enumerate(self._processes):
            if index < len(self._specs) and self._specs[index]["run"] == run:
                return label if label != run else None
        return None

    @property
    def specs(self) -> list[dict]:
        """這次啟動用的 node 清單(存 launch 檔時要重建 `run` / `args`)。"""
        return list(self._specs)

    # ------------------------------------------------------------- 啟動

    def start(self, config: Path) -> None:
        document = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        self._specs = document.get("nodes") or []
        if not self._specs:
            self.output.emit(f"{config} 裡沒有 nodes")
            self.finished.emit()
            return
        self._ready_timeout = float(document.get("ready_timeout", DEFAULT_READY_TIMEOUT))
        self._index = 0
        self._failed = 0
        self._next()

    def _next(self) -> None:
        if self._index >= len(self._specs):
            self.state.emit("全部啟動完成", len(self._processes), self._failed)
            self.finished.emit()
            return
        spec = self._specs[self._index]
        run = spec["run"]
        if run == LOG_RUN:
            # UI 已經有一個 Log 了。兩個會搶同一個 service 名稱,也會各寫各的檔。
            self.output.emit(f"--- skipping {LOG_RUN}(UI 已經起了一個)")
            self._apply_to_log(spec.get("setting") or [])
            self._advance()
            return
        args = spec.get("args", "")
        arguments = args.split() if isinstance(args, str) else [str(a) for a in args]

        self._before = set(self._station.nodes)
        self._settings = list(spec.get("setting") or [])
        self.output.emit(f"--- starting {run} {args}".rstrip())
        self.state.emit(f"啟動中:{run}", len(self._processes), self._failed)

        process = self._spawn_process(run, arguments)
        process.readyReadStandardOutput.connect(
            lambda p=process, label=run: self._drain(p, label))
        self._processes.append([run, process])
        self._waiting_for = None
        self._deadline.start(int(self._ready_timeout * 1000))

    def _spawn_process(self, run: str, arguments: list[str]) -> QProcess:
        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setArguments(["-u", "-m", NODE_MODULE.format(run=run), *arguments])
        process.setWorkingDirectory(str(self._root))
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.start()
        return process

    def _drain(self, process: QProcess, label: str) -> None:
        text = bytes(process.readAllStandardOutput()).decode("utf-8", "replace")
        for line in text.splitlines():
            if line.strip():
                self.output.emit(f"[{label}] {line}")

    # ------------------------------------------------------------- 就緒

    def _on_nodes(self, nodes: list[str]) -> None:
        """新的 service 出現了 —— 把前綴換成它真正的名字,然後等它變 ok。"""
        if not self._deadline.isActive() or self._waiting_for is not None:
            return
        new = set(nodes) - self._before
        if not new:
            return
        self._waiting_for = sorted(new)[0]
        if self._processes:
            self._processes[-1][0] = self._waiting_for

    def _on_status(self, node: str, status: str) -> None:
        if node != self._waiting_for or not self._deadline.isActive():
            return
        if status == "ok":
            self._deadline.stop()
            self.output.emit(f"[{node}] ready")
            self._apply_settings(node)
            self._advance()

    def _apply_to_log(self, settings: list[str]) -> None:
        """launch 檔裡 log 條目的設定仍要生效 —— 跳過的是行程,不是設定。"""
        self._settings = settings
        self._apply_settings(LOG_SERVICE)

    def _apply_settings(self, node: str) -> None:
        """把 launch 檔裡這個 node 的設定送過去。失敗只警示,不中止整站。"""
        for line in self._settings:
            try:
                reply = client.call(node, line, connect_timeout=3.0)
            except Exception as exc:
                reply = f"ERR {exc}"
            else:
                if reply.startswith("OK"):
                    self._station.remember_setting(node, line)
            self.output.emit(f"[{node}] {line} -> {reply}")
        self._settings = []

    def _give_up(self) -> None:
        # README 3.1:紅字警示,但**繼續啟動下一個**,不整批中止。
        label = self._waiting_for or self._specs[self._index]["run"]
        self.output.emit(
            f"[{label}] 超過 {self._ready_timeout:.0f}s 仍未 ok —— 繼續啟動下一個")
        self._failed += 1
        self._advance()

    def _advance(self) -> None:
        self._index += 1
        self._waiting_for = None
        self._next()

    # ------------------------------------------------------------- 關閉

    def shutdown(self, keep_log: bool = False) -> None:
        """反向順序關閉儀器。`keep_log=False` 時連 Log 一起收掉(README 3.1)。

        Log **一定最後關**,而且先收到一個 `stop` 命令 —— 那是它關檔的正常路徑。
        其他 node 已經先走了,所以它這時收到的確實是整場測試的最後資料。

        關掉整站但留著 Log(`keep_log=True`)是為了「關閉整站」按鈕:
        使用者只是想收掉儀器,不是要關掉 UI。
        """
        self._deadline.stop()
        for label, process in reversed(self._processes):
            if process.state() == QProcess.NotRunning:
                continue
            self.output.emit(f"--- stopping {label}")
            self._end(label, process)
        self._processes.clear()

        if not keep_log and self._log_process is not None:
            self.output.emit(f"--- stopping {LOG_SERVICE}")
            self._stop_recording()
            self._end(LOG_SERVICE, self._log_process)
            self._log_process = None
        self.state.emit("已關閉", 0, 0)

    def _stop_recording(self) -> None:
        try:
            reply = client.call(LOG_SERVICE, "stop", connect_timeout=2.0)
        except Exception as exc:                 # Log 已經不理人了就直接往下走
            self.output.emit(f"[{LOG_SERVICE}] stop 失敗:{exc}")
            return
        self.output.emit(f"[{LOG_SERVICE}] {reply}")

    @staticmethod
    def _end(label: str, process: QProcess) -> None:
        """Windows 上 `terminate()` 對 console 程式沒有作用(它送的是視窗訊息),
        等滿逾時只是浪費時間,所以直接 `kill()`。

        資料不會因此損失:Log 是逐筆 flush 的,而且上面已經先讓它正常關檔了。
        """
        process.kill()
        process.waitForFinished(5000)
