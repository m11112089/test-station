"""eCAL 與 Qt 之間的橋(README 5.3)。

UI 不直接碰 eCAL,一律透過這裡,理由有兩個:

1. **執行緒。** eCAL 的 callback 在它自己的執行緒上跑,直接動 widget 會讓 Qt 當掉。
   這裡把每個事件轉成 Qt signal;signal 送到住在主執行緒的接收者時會自動排隊,
   widget 因此永遠只在主執行緒被碰到。
2. **`st` 與 UI 走同一條路。** service 呼叫直接用 `cli.common.call()`,
   跟 `st service call` 是同一份程式碼,不是另外寫一套(README 1.2:GUI 沒有特權)。

探索用輪詢(eCAL 原生的 monitoring API),訂閱則是常駐的 —— node 出現就掛上,
消失就拆掉,所以燈號與數值是推過來的,不是輪詢來的。
"""

from __future__ import annotations

import json

import ecal.nanobind_core as ecal
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from ..cli import common

DISCOVERY_INTERVAL_MS = 2000
"""多久重新問一次站上有哪些 node 與 topic。"""

LOG_SERVICE = "Log"
"""Log node 的固定 service 名稱(README 2.1)。"""

NON_DATA_CHANNELS = {"status", "activity", "raw"}
"""這三個是共通 topic,不是量測值,不進即時數值表(README 2.2)。"""


def is_data_topic(topic: str) -> bool:
    return topic.rsplit("/", 1)[-1] not in NON_DATA_CHANNELS


class _Call(QRunnable):
    """service 呼叫會阻塞,丟到執行緒池,不要卡住畫面。"""

    def __init__(self, station: "Station", service: str, line: str) -> None:
        super().__init__()
        self._station = station
        self._service = service
        self._line = line

    def run(self) -> None:
        try:
            reply = common.call(self._service, self._line)
        except Exception as exc:
            reply = f"ERR {exc}"
        self._station.reply.emit(self._service, self._line, reply)


class Station(QObject):
    """站台目前的樣子,以 Qt signal 對外。"""

    nodes_changed = Signal(list)
    """list[str]:service 名稱,例如 ['BTM-4208SD/COM11', 'Log']。"""

    node_status = Signal(str, str)
    """(service, status):`ok` / `reconnecting` / `error: ...`。"""

    topics_changed = Signal(list)
    """list[str]:所有被發佈的 topic。"""

    measurement = Signal(str, dict)
    """(topic, payload):資料 topic 的一筆量測值。"""

    activity = Signal(str, dict)
    """(service, payload):node 的 activity 事件。"""

    raw_line = Signal(str, str)
    """(service, text):DUT 的一行原始輸出。"""

    reply = Signal(str, str, str)
    """(service, 送出的命令, 回覆)。"""

    describe_ready = Signal(str, list)
    """(service, 命令清單):命令面板用來長出按鈕。"""

    log_state = Signal(dict)
    """Log node 的錄製狀態(`Log/state`)。"""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        self._status_subs: dict[str, ecal.Subscriber] = {}
        self._data_subs: dict[str, ecal.Subscriber] = {}
        self._console_subs: dict[str, ecal.Subscriber] = {}
        self._log_sub: ecal.Subscriber | None = None
        self._nodes: list[str] = []
        self._topics: list[str] = []

        self._timer = QTimer(self)
        self._timer.setInterval(DISCOVERY_INTERVAL_MS)
        self._timer.timeout.connect(self._discover)

    # ------------------------------------------------------------- 生命週期

    def start(self) -> None:
        self.watch_log()
        self._discover()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        if self._log_sub is not None:
            self._log_sub.remove_receive_callback()
            self._log_sub = None
        for subs in (self._status_subs, self._data_subs, self._console_subs):
            for sub in subs.values():
                sub.remove_receive_callback()
            subs.clear()

    # --------------------------------------------------------------- 探索

    def _discover(self) -> None:
        # settle=0:UI 的 eCAL session 一直開著,註冊資訊早就熱了。
        try:
            nodes = sorted(s.service_name for s in common.list_services(settle=0))
            topics = sorted(t.topic_name for t in common.list_topics(settle=0))
        except RuntimeError:
            return  # monitoring 還沒好,下一輪再說

        if nodes != self._nodes:
            self._nodes = nodes
            self._sync_status_subs(nodes)
            self.nodes_changed.emit(nodes)
        if topics != self._topics:
            self._topics = topics
            self._sync_data_subs([t for t in topics if is_data_topic(t)])
            self.topics_changed.emit(topics)

    def _sync_status_subs(self, nodes: list[str]) -> None:
        for gone in set(self._status_subs) - set(nodes):
            self._status_subs.pop(gone).remove_receive_callback()
        for name in set(nodes) - set(self._status_subs):
            self._status_subs[name] = self._subscribe(
                f"{name}/status",
                lambda raw, n=name: self.node_status.emit(
                    n, raw.decode("utf-8", "replace")),
            )

    def _sync_data_subs(self, topics: list[str]) -> None:
        for gone in set(self._data_subs) - set(topics):
            self._data_subs.pop(gone).remove_receive_callback()
        for topic in set(topics) - set(self._data_subs):
            self._data_subs[topic] = self._subscribe(
                topic,
                lambda raw, t=topic: self._emit_measurement(t, raw),
            )

    def _subscribe(self, topic: str, handler) -> ecal.Subscriber:
        sub = ecal.Subscriber(topic)
        sub.set_receive_callback(lambda _t, _dt, data, *_: handler(data.buffer))
        return sub

    def watch_log(self) -> None:
        """訂閱 Log node 的狀態。它沒有 `v`,所以不會混進即時數值表。"""
        if self._log_sub is not None:
            return
        self._log_sub = self._subscribe(
            f"{LOG_SERVICE}/state", lambda raw: self._emit_log_state(raw)
        )

    def _emit_log_state(self, raw: bytes) -> None:
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except (json.JSONDecodeError, ValueError):
            return
        if isinstance(payload, dict):
            self.log_state.emit(payload)

    def _emit_json(self, signal: Signal, key: str, raw: bytes) -> None:
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except (json.JSONDecodeError, ValueError):
            return
        if isinstance(payload, dict):
            signal.emit(key, payload)

    def _emit_measurement(self, topic: str, raw: bytes) -> None:
        """只有帶 `v` 的 payload 才是量測值(README 2.2)。

        這條規則讓 `Log/state` 之類的 node 自有狀態 topic 自然被擋在數值表外面,
        不必維護一份「哪些 topic 要排除」的名單。
        """
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except (json.JSONDecodeError, ValueError):
            return
        if isinstance(payload, dict) and "v" in payload:
            self.measurement.emit(topic, payload)

    # ------------------------------------------------- DUT console 的訂閱

    def watch_console(self, service: str | None) -> None:
        """只訂閱目前看著的那個 node 的 raw / activity,換 node 就換訂閱。"""
        for sub in self._console_subs.values():
            sub.remove_receive_callback()
        self._console_subs.clear()
        if not service:
            return
        self._console_subs[f"{service}/raw"] = self._subscribe(
            f"{service}/raw",
            lambda raw, s=service: self.raw_line.emit(
                s, raw.decode("utf-8", "replace").rstrip()),
        )
        self._console_subs[f"{service}/activity"] = self._subscribe(
            f"{service}/activity",
            lambda raw, s=service: self._emit_json(self.activity, s, raw),
        )

    # --------------------------------------------------------------- 命令

    def call(self, service: str, line: str) -> None:
        """非同步送出一行命令;結果由 `reply` signal 帶回來。"""
        self._pool.start(_Call(self, service, line))

    def describe(self, service: str) -> None:
        """非同步取命令清單;結果由 `describe_ready` signal 帶回來。"""

        class _Describe(QRunnable):
            def run(_self) -> None:
                try:
                    commands = common.describe(service)
                except Exception:
                    commands = []
                self.describe_ready.emit(service, commands)

        self._pool.start(_Describe())
