"""Node 基底類別(README 2.1、2.2、6)。

提供所有 node 共通的部分:

* eCAL service ``<device>/<port>``,收一行純文字命令,回 ``OK`` / ``ERR``
* 共通 method ``describe`` / ``set_channel`` / ``reconnect``
* 共通 topic ``status``(1 Hz 心跳)與 ``activity``
* 資料 topic ``<device>/<port>/<channel>`` 的 payload 組裝與發佈
* 斷線重連狀態機:2 s 重開同一個 port,超過 30 s 轉 ``error`` 並停止自動重試

eCAL 6 的 service 是 method-based,而本站的契約是「一行純文字」。兩者這樣接:
node 只註冊一個 eCAL method ``cmd``,request 就是整行命令,response 就是整行回覆。
命令清單一律由 ``describe`` 取得,不依賴 eCAL 自己的 method 列表。
"""

from __future__ import annotations

import json
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime

import ecal.nanobind_core as ecal

from .describe import Arg, Command, check_args, split_args, to_json
from .payload import measurement, stamp

CMD_METHOD = "cmd"
"""承載整行命令的 eCAL method 名稱。"""

HEARTBEAT_INTERVAL = 1.0
RECONNECT_INTERVAL = 2.0
RECONNECT_TIMEOUT = 30.0

_JSON = ecal.DataTypeInformation(name="json", encoding="utf-8")
_TEXT = ecal.DataTypeInformation(name="text", encoding="utf-8")


class DeviceError(Exception):
    """裝置讀寫失敗(USB 被拔、port 消失等),觸發重連狀態機。"""


class Node(ABC):
    """所有 node 的基底。子類別設定 ``device`` 並實作 open/close/poll。"""

    device: str = ""

    components: int = ecal.init.DEFAULT
    """要初始化哪些 eCAL 元件。

    預設的 ``init.DEFAULT``(55)**不含 ``MONITORING``(8)**,少了它
    ``ecal.monitoring.get_monitoring()`` 會丟 ``RuntimeError``。
    需要探索站上有什麼的 node(例如 Log 要訂閱所有資料 topic)必須改成 ``init.ALL``。
    """

    def __init__(self, port: str | None = None) -> None:
        if not self.device:
            raise ValueError("subclass must set `device`")
        self.port = port
        # Log 與 Main-Control 是固定名稱、沒有 port 的 node(README 2.1)。
        self.prefix = f"{self.device}/{port}" if port else self.device

        self._lock = threading.Lock()
        self._names: dict[str, str] = {}
        self._pubs: dict[str, ecal.Publisher] = {}
        self._status_pub: ecal.Publisher | None = None
        self._activity_pub: ecal.Publisher | None = None
        self._server: ecal.ServiceServer | None = None

        self._status = "reconnecting"
        self._stop = threading.Event()
        self._connected = False
        self._retry_deadline: float | None = None
        self._commands = {c.name: c for c in self._build_commands()}

    # ---------------------------------------------------------------- 子類別

    @abstractmethod
    def open_device(self) -> None:
        """開啟連線。失敗請 raise DeviceError。"""

    @abstractmethod
    def close_device(self) -> None:
        """關閉連線。必須可重複呼叫。"""

    @abstractmethod
    def poll(self) -> None:
        """跑一次 I/O(讀取並發佈)。阻塞時間應在 1 秒上下,讓迴圈能及時反應停止。

        裝置讀寫失敗請 raise DeviceError。
        """

    def commands(self) -> list[Command]:
        """子類別額外提供的命令,格式同 describe。"""
        return []

    # -------------------------------------------------------------- 共通命令

    def _build_commands(self) -> list[Command]:
        common = [
            Command("describe", "命令清單", self._cmd_describe),
            Command(
                "set_channel", "命名 channel", self._cmd_set_channel,
                args=(Arg("channel"), Arg("name")),
            ),
            Command("reconnect", "重新連線", self._cmd_reconnect),
        ]
        return self.commands() + common

    def _cmd_describe(self) -> str:
        return to_json(list(self._commands.values()))

    def _cmd_set_channel(self, channel: str, name: str) -> str:
        with self._lock:
            self._names[channel] = name
        return f"{channel} = {name}"

    def _cmd_reconnect(self) -> str:
        """重連逾時(status 為 error)後手動重試。"""
        self._teardown_device()
        self._retry_deadline = time.monotonic() + RECONNECT_TIMEOUT
        self.set_status("reconnecting")
        return "reconnecting"

    # ------------------------------------------------------------------ 發佈

    def channel_name(self, channel: str) -> str:
        """channel 的顯示名稱;未用 set_channel 命名過時 = channel 名。"""
        with self._lock:
            return self._names.get(channel, channel)

    def _publisher(self, channel: str) -> ecal.Publisher:
        with self._lock:
            pub = self._pubs.get(channel)
            if pub is None:
                pub = ecal.Publisher(f"{self.prefix}/{channel}", _JSON)
                self._pubs[channel] = pub
            return pub

    def publish(self, channel: str, value: float, unit: str,
                when: datetime | None = None) -> None:
        """發佈一筆量測值到 ``<device>/<port>/<channel>``,時間戳蓋在量測當下。"""
        when = when or datetime.now()
        name = self.channel_name(channel)
        self._publisher(channel).send(measurement(value, unit, name, when))
        self.activity("PUB", f"{self.prefix}/{channel} {value} {unit}", when)

    def activity(self, kind: str, text: str, when: datetime | None = None) -> None:
        """記一筆事件到 activity topic(供 UI 的 DUT console 下半部顯示)。"""
        if self._activity_pub is None:
            return
        line = json.dumps(
            {"t": stamp(when), "kind": kind, "text": text}, ensure_ascii=False
        )
        self._activity_pub.send(line.encode("utf-8"))

    def set_status(self, status: str) -> None:
        self._status = status
        if self._status_pub is not None:
            self._status_pub.send(status.encode("utf-8"))

    @property
    def status(self) -> str:
        return self._status

    # --------------------------------------------------------------- service

    def handle_line(self, line: str) -> str:
        """把一行純文字命令派送到 handler,回傳 ``OK ...`` / ``ERR ...``。"""
        line = line.strip()
        if not line:
            return "ERR empty command"
        method, *args = line.split()
        command = self._commands.get(method)
        if command is None:
            known = " ".join(sorted(self._commands))
            return f"ERR unknown method '{method}'; known: {known}"
        args = split_args(command, args)
        problem = check_args(command, args)
        if problem:
            return f"ERR {problem}"
        try:
            result = command.handler(*args)
        except DeviceError as exc:
            self._enter_reconnecting(str(exc))
            return f"ERR device error: {exc}"
        except Exception as exc:  # handler 的錯誤不該讓 node 死掉
            return f"ERR {exc}"
        return f"OK {result}" if result else "OK"

    def _on_call(self, _method_info, request: bytes) -> tuple[int, bytes]:
        """eCAL 6.1 的 method callback。

        **必須回 ``(ret_state, bytes)``。** 回 ``bytes`` 或 ``str`` 會在 eCAL 內部被吞成
        ``Error invoking method callback: bad cast``,而且 client 端**仍然**收到
        ``CallState.EXECUTED``,只是 response 是空的 —— 不拋例外,很難查。
        """
        line = request.decode("utf-8", errors="replace")
        reply = self.handle_line(line)
        self.activity("SRV", f"{line.strip()} -> {reply[:80]}")
        return 0, reply.encode("utf-8")

    # ------------------------------------------------------------------- run

    def run(self) -> None:
        """啟動 node,阻塞到 Ctrl+C 或 stop()。"""
        ecal.initialize(self.prefix.replace("/", "_"), self.components)
        self._status_pub = ecal.Publisher(f"{self.prefix}/status", _TEXT)
        self._activity_pub = ecal.Publisher(f"{self.prefix}/activity", _JSON)
        self._server = ecal.ServiceServer(self.prefix)
        self._server.set_method_callback(
            ecal.ServiceMethodInformation(
                method_name=CMD_METHOD, request_type=_TEXT, response_type=_TEXT
            ),
            self._on_call,
        )

        heartbeat = threading.Thread(target=self._heartbeat, daemon=True)
        heartbeat.start()
        self._retry_deadline = time.monotonic() + RECONNECT_TIMEOUT
        try:
            self._loop()
        except KeyboardInterrupt:
            pass
        finally:
            self._stop.set()
            self._teardown_device()
            ecal.finalize()

    def _loop(self) -> None:
        while not self._stop.is_set() and ecal.ok():
            if not self._connected:
                self._try_open()
                continue
            try:
                self.poll()
            except DeviceError as exc:
                self._enter_reconnecting(str(exc))

    def _try_open(self) -> None:
        # status 已是 error 表示重連逾時,停止自動重試,等 `reconnect` 命令。
        if self._status.startswith("error"):
            self._stop.wait(RECONNECT_INTERVAL)
            return
        try:
            self.open_device()
        except DeviceError as exc:
            if self._retry_deadline is not None and time.monotonic() >= self._retry_deadline:
                self.set_status(f"error: {exc}")
                self.activity("ERR", f"gave up after {RECONNECT_TIMEOUT:.0f}s: {exc}")
            else:
                self._stop.wait(RECONNECT_INTERVAL)
            return
        self._connected = True
        self._retry_deadline = None
        self.set_status("ok")
        self.activity("SYS", f"connected to {self.port}")

    def _enter_reconnecting(self, reason: str) -> None:
        self._teardown_device()
        self.set_status("reconnecting")
        self.activity("ERR", f"device error: {reason}")
        # 重開「同一個 COM」,不重新掃描。
        self._retry_deadline = time.monotonic() + RECONNECT_TIMEOUT
        self._stop.wait(RECONNECT_INTERVAL)

    def _teardown_device(self) -> None:
        if self._connected:
            try:
                self.close_device()
            except Exception:
                pass
        self._connected = False

    def _heartbeat(self) -> None:
        while not self._stop.wait(HEARTBEAT_INTERVAL):
            if self._status_pub is not None:
                self._status_pub.send(self._status.encode("utf-8"))

    def stop(self) -> None:
        self._stop.set()
