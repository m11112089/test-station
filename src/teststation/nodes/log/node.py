"""Log node —— 全站唯一會寫檔的 node(README 1.3、4)。

service 名稱固定是 `Log`(沒有 port)。

**一啟動就開檔**,資料夾名是啟動當下的時間。之後用 `record` 指定要記錄哪些 topic,
**設定完就立刻開始寫** —— 不需要再按開始。每收到一筆就寫進 `data.csv` 並立即 flush,
所以 Log 被砍掉時最多只損失最後一行。

`start <name>` / `stop` 仍然存在,給測試計畫劃分 run 用:`start` 關掉現在這個檔、
另開一個帶名字的。手動操作不需要碰它們。

run 資料夾:

| 檔案 | 內容 |
|---|---|
| `data.csv` | long format `t, topic, name, value, unit`,照收到的順序寫 |
| `console.log` | 選到的 DUT `raw` topic 的原始輸出 |

**Log 不產報告。** 記錄與視覺化是兩件事:報告事後用 `uv run visualization <data.csv>`
產生,想換畫法、想重畫舊的 run,都不必重跑測試。

時間戳用的是**各 node 在量測當下蓋的那個**,不是 Log 收到的時間 —— 跨 PC 時
網路延遲不會污染時間軸(README 2.2)。
"""

from __future__ import annotations

import argparse
import csv
import json
import threading
import time
from datetime import datetime
from pathlib import Path

import ecal.nanobind_core as ecal

from ...core.describe import Arg, Command
from ...core.node import Node
from ...core.payload import stamp
from ...core.topics import is_measurement, is_raw, is_recordable
from .sync import CSV_HEADER

DISCOVERY_INTERVAL = 2.0
"""多久重新掃一次有沒有新 topic —— 測試中途插上探棒就會多一個 channel。"""

STATE_INTERVAL = 1.0
_JSON = ecal.DataTypeInformation(name="json", encoding="utf-8")


class LogNode(Node):
    device = "Log"

    components = ecal.init.ALL
    """Log 要自己找出站上有哪些資料 topic,所以需要 MONITORING 元件。"""

    def __init__(self, logs_dir: Path) -> None:
        super().__init__()
        self.logs_dir = logs_dir
        self._file_lock = threading.Lock()
        self._subs: dict[str, ecal.Subscriber] = {}
        self._state_pub: ecal.Publisher | None = None

        self._run: str | None = None
        self._run_dir: Path | None = None
        self._data = None
        self._writer = None
        self._console = None
        self._rows = 0
        self._started_at = ""
        self._last_discovery = 0.0
        self._last_state = 0.0
        self._discovery_failed = False
        self._selected: list[str] = []

    # -------------------------------------------------------------- 命令

    def commands(self) -> list[Command]:
        return [
            Command("record", "選要記錄的 topic", self._cmd_record,
                    args=(Arg("topics"),), variadic=True, setting=True),
            Command("start", "換一個新檔", self._cmd_start, args=(Arg("run_name"),)),
            Command("stop", "關檔", self._cmd_stop, style="danger"),
        ]

    def _cmd_record(self, topics: str) -> str:
        """設定要記錄哪些 topic。**整份清單一次給完,會覆蓋上一次。**

        檔案在啟動時就開好了,所以**設定完就立刻開始寫**,不需要再下 start。
        是 setting(不是動作),所以 UI 可以把它存進 launch 檔,下次直接帶起來。
        """
        self._selected = sorted({t for t in topics.replace(",", " ").split() if t})
        self._unsubscribe()
        self._last_discovery = 0.0       # 下一次 poll 立刻照新清單重訂
        return f"{len(self._selected)} topic(s)"

    def _open_run(self, run_name: str = "") -> Path:
        """開一個新的 run 資料夾。名字是**當下的時間**,有給 run_name 就接在後面。"""
        folder = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        run_dir = self.logs_dir / (f"{folder}_{run_name}" if run_name else folder)
        run_dir.mkdir(parents=True, exist_ok=True)

        with self._file_lock:
            self._data = (run_dir / "data.csv").open("w", newline="", encoding="utf-8")
            self._writer = csv.writer(self._data)
            self._writer.writerow(CSV_HEADER)
            self._data.flush()
            self._console = (run_dir / "console.log").open("w", encoding="utf-8")
            self._rows = 0

        self._run = run_name
        self._run_dir = run_dir
        self._started_at = stamp()
        self._last_discovery = 0.0   # 下一次 poll 立刻照現在的選擇訂閱
        self.activity("SYS", f"writing to {run_dir}")
        return run_dir

    def _close_run(self) -> str:
        if self._run_dir is None:
            return "no file open"
        run_dir, rows = self._run_dir, self._rows
        self._unsubscribe()
        with self._file_lock:
            for handle in (self._data, self._console):
                if handle is not None:
                    handle.close()
            self._data = self._writer = self._console = None
        self._run = self._run_dir = None
        self.activity("SYS", f"closed {run_dir} ({rows} rows)")
        return f"{run_dir / 'data.csv'} ({rows} rows)"

    def _cmd_start(self, run_name: str) -> str:
        """換一個新檔。給測試計畫劃分 run 用 —— 手動操作不需要碰它。"""
        self._close_run()
        return str(self._open_run(run_name))

    def _cmd_stop(self) -> str:
        """關檔。**不產報告** —— 那是 `uv run visualization <data.csv>` 的事。"""
        return self._close_run()

    # -------------------------------------------------------------- 訂閱

    def _interesting(self, topic: str) -> bool:
        """只有被 `record` 選到的 topic 才訂閱。

        共通 topic(status / activity)與影像擋掉是保險 —— 就算有人手滑選了也不該進檔案。
        raw 是選得到的:它進 console.log,不進 data.csv。
        """
        if topic.startswith(f"{self.prefix}/") or not is_recordable(topic):
            return False
        return topic in self._selected

    def _discover(self) -> None:
        try:
            topics = {t.topic_name for t in ecal.monitoring.get_monitoring().publishers}
        except RuntimeError as exc:
            # 這裡靜默失敗過一次:少了 MONITORING 元件,結果一個 topic 都沒訂到,
            # 而檔案照開、報告照產,只是裡面沒有資料。寧可吵。
            if not self._discovery_failed:
                self._discovery_failed = True
                self.activity("ERR", f"discovery failed, nothing will be recorded: {exc}")
            return
        self._discovery_failed = False
        for topic in sorted(topics):
            if topic in self._subs or not self._interesting(topic):
                continue
            sub = ecal.Subscriber(topic)
            sub.set_receive_callback(
                (lambda t: lambda _t, _dt, data, *_: self._on_message(t, data.buffer))(topic)
            )
            self._subs[topic] = sub
            self.activity("SYS", f"subscribed {topic}")

    def _unsubscribe(self) -> None:
        for sub in self._subs.values():
            sub.remove_receive_callback()
        self._subs.clear()

    def _on_message(self, topic: str, raw: bytes) -> None:
        if self._run_dir is None:
            return
        if is_raw(topic):
            self._write_console(topic, raw.decode("utf-8", "replace").rstrip())
            return
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except (json.JSONDecodeError, ValueError):
            return
        if not is_measurement(payload):
            return  # 例如某個 node 自己的狀態 topic
        self._write_row(payload.get("t", stamp()), topic,
                        payload.get("name", ""), payload["v"], payload.get("unit", ""))

    # -------------------------------------------------------------- 寫檔

    def _write_row(self, t: str, topic: str, name: str, value, unit: str) -> None:
        with self._file_lock:
            if self._writer is None or self._data is None:
                return
            self._writer.writerow([t, topic, name, value, unit])
            self._data.flush()  # 逐筆 flush:最多損失最後一行(README 6)
            self._rows += 1

    def _write_console(self, topic: str, text: str) -> None:
        with self._file_lock:
            if self._console is None:
                return
            self._console.write(f"{stamp()}  {topic}  {text}\n")
            self._console.flush()

    # -------------------------------------------------------------- 生命週期

    def open_device(self) -> None:
        """Log 沒有裝置可連,一起來就是 ok —— 而且立刻開檔。"""
        self._state_pub = ecal.Publisher(f"{self.prefix}/state", _JSON)
        if self._run_dir is None:
            self._open_run()

    def close_device(self) -> None:
        self._close_run()
        self._unsubscribe()

    def poll(self) -> None:
        now = time.monotonic()
        if self._run_dir is not None and now - self._last_discovery >= DISCOVERY_INTERVAL:
            self._last_discovery = now
            self._discover()
        if now - self._last_state >= STATE_INTERVAL:
            self._last_state = now
            self._publish_state()
        self._stop.wait(0.2)

    def _publish_state(self) -> None:
        """給 UI 看的錄製狀態。故意不含 `v`,才不會被當成量測值收進報告。"""
        if self._state_pub is None:
            return
        self._state_pub.send(json.dumps({
            "t": stamp(),
            "recording": self._run_dir is not None,
            "run": self._run or "",
            "dir": str(self._run_dir) if self._run_dir else "",
            "file": str(self._run_dir / "data.csv") if self._run_dir else "",
            "rows": self._rows,
            "selected": list(self._selected),
            "topics": len(self._subs),
            "started": self._started_at,
        }, ensure_ascii=False).encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="log", description=__doc__)
    parser.add_argument("--logs-dir", default="logs", help="run 資料夾放哪(預設 logs/)")
    args = parser.parse_args(argv)

    node = LogNode(Path(args.logs_dir).resolve())
    node.logs_dir.mkdir(parents=True, exist_ok=True)
    print(f"{node.prefix} starting, logs in {node.logs_dir}")
    try:
        node.run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
