"""`st` 的各個子命令(README 5.1)。

每個函式都回傳 exit code,讓 REPL 與一次性呼叫共用同一份實作。
"""

from __future__ import annotations

import json
import statistics
import sys
import threading
import time
from collections import deque

import ecal.nanobind_core as ecal

from ..core.payload import stamp
from . import common
from .common import ServiceUnavailable, columns, describe, format_payload


# ------------------------------------------------------------------- node

def node_list(settle: float = common.SETTLE) -> int:
    nodes = common.list_nodes(settle=settle)
    if not nodes:
        print("no nodes found — 站台還沒啟動?試 `st launch launch/<station>.yaml`")
        return 1
    rows = [[n.name, n.status, n.host, str(n.pid)] for n in nodes]
    print(columns(rows, ["NODE", "STATUS", "HOST", "PID"]))
    return 0


# ---------------------------------------------------------------- service

def service_list(settle: float = common.SETTLE) -> int:
    servers = common.list_services(settle=settle)
    if not servers:
        print("no services found")
        return 1
    rows = [[s.service_name, s.host_name, str(s.process_id)] for s in servers]
    print(columns(rows, ["SERVICE", "HOST", "PID"]))
    return 0


def service_call(service: str, line: str) -> int:
    try:
        reply = common.call(service, line)
    except ServiceUnavailable as exc:
        print(f"ERR {exc}", file=sys.stderr)
        return 1
    print(reply)
    return 0 if reply.startswith("OK") else 1


def service_describe(service: str) -> int:
    try:
        commands = describe(service)
    except ServiceUnavailable as exc:
        print(f"ERR {exc}", file=sys.stderr)
        return 1
    rows = []
    for command in commands:
        args = " ".join(_format_arg(a) for a in command.get("args", []))
        rows.append([command["name"], args, command.get("label", ""),
                     command.get("style", "")])
    print(columns(rows, ["METHOD", "ARGS", "LABEL", "STYLE"]))
    return 0


def _format_arg(arg: dict) -> str:
    text = f"<{arg['name']}"
    if "unit" in arg:
        text += f":{arg['unit']}"
    if "min" in arg or "max" in arg:
        text += f" {arg.get('min', '')}..{arg.get('max', '')}"
    return text + ">"


# ------------------------------------------------------------------ topic

def topic_list(settle: float = common.SETTLE) -> int:
    topics = common.list_topics(settle=settle)
    if not topics:
        print("no topics found")
        return 1
    rows = [[t.topic_name, f"{t.data_frequency / 1000:.2f}", t.unit_name, t.host_name]
            for t in topics]
    print(columns(rows, ["TOPIC", "HZ", "NODE", "HOST"]))
    return 0


def topic_echo(topic: str, count: int | None = None) -> int:
    """印出 topic 收到的每一筆,直到 Ctrl+C(或收滿 `count` 筆)。"""
    received = threading.Event()
    seen = [0]

    def on_message(_topic, _datatype, data, *_):
        print(format_payload(data.buffer), flush=True)
        seen[0] += 1
        if count is not None and seen[0] >= count:
            received.set()

    sub = ecal.Subscriber(topic)
    sub.set_receive_callback(on_message)
    print(f"echoing {topic} — Ctrl+C to stop", file=sys.stderr)
    try:
        while not received.is_set() and ecal.ok():
            received.wait(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        sub.remove_receive_callback()
    if seen[0] == 0:
        print(f"(沒收到任何東西 —— topic 名稱對嗎?試 `st topic list`)", file=sys.stderr)
        return 1
    return 0


def topic_hz(topic: str, window: float = 1.0, samples: int = 50) -> int:
    """量測發佈頻率,每 `window` 秒印一次(類似 ros2 topic hz)。

    統計是跑在**滾動視窗**上(最近 `samples` 筆),不是每次印完就清空 ——
    BTM-4208SD 這種一輪 12 個 channel 約 2 秒的來源,單一 channel 只有 0.5 Hz,
    清空式的視窗會一直落在「不足兩筆」而印不出東西。
    """
    times: deque[float] = deque(maxlen=samples)
    lock = threading.Lock()

    def on_message(*_):
        with lock:
            times.append(time.monotonic())

    sub = ecal.Subscriber(topic)
    sub.set_receive_callback(on_message)
    print(f"measuring {topic} — Ctrl+C to stop", file=sys.stderr)
    try:
        while ecal.ok():
            time.sleep(window)
            with lock:
                collected = list(times)
            if len(collected) < 2:
                print("no new messages", flush=True)
                continue
            gaps = [b - a for a, b in zip(collected, collected[1:])]
            spread = statistics.stdev(gaps) if len(gaps) > 1 else 0.0
            print(f"average rate: {len(gaps) / sum(gaps):7.3f}  "
                  f"min: {min(gaps):.3f}s  max: {max(gaps):.3f}s  "
                  f"std dev: {spread:.5f}  window: {len(collected)}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        sub.remove_receive_callback()
    return 0


def topic_pub(topic: str, payload: str, rate: float | None = None,
              count: int | None = None) -> int:
    """發佈到任意 topic,用來在沒有實機時餵假資料給 Log / UI。

    payload 少了 `t` 會自動補上現在時間,這樣 `st topic pub x '{"v": 1.0}'`
    就直接符合 README 2.2 的 schema。
    """
    try:
        message = json.loads(payload)
    except json.JSONDecodeError as exc:
        print(f"ERR payload is not valid JSON: {exc}", file=sys.stderr)
        return 1
    if isinstance(message, dict) and "t" not in message:
        message = {"t": stamp(), **message}

    pub = ecal.Publisher(topic, common.JSON)
    # 新建的 publisher 要先跟訂閱端配對才送得到,否則第一筆直接掉進虛空。
    # eCAL 的註冊大約 1 Hz,所以這裡等的是「真的有人在聽」,不是等固定秒數。
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and pub.get_subscriber_count() == 0:
        time.sleep(0.05)
    if pub.get_subscriber_count() == 0:
        print(f"(沒有人訂閱 {topic},還是送出去了)", file=sys.stderr)

    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    if rate is None:
        pub.send(body)
        # 送完立刻 finalize 會把還在路上的訊息砍掉,留一點時間讓它出去。
        time.sleep(0.3)
        print(f"published 1 message to {topic}", flush=True)
        return 0

    interval = 1.0 / rate
    sent = 0
    print(f"publishing to {topic} at {rate} Hz — Ctrl+C to stop", file=sys.stderr)
    try:
        while ecal.ok() and (count is None or sent < count):
            if isinstance(message, dict) and "t" in message:
                message["t"] = stamp()
                body = json.dumps(message, ensure_ascii=False).encode("utf-8")
            pub.send(body)
            sent += 1
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    print(f"published {sent} messages to {topic}", flush=True)
    return 0
