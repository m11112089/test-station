"""`st` 共用的東西:eCAL session、探索、service 呼叫、payload 格式化。

命令來源是平權的(README 1.2):`st` 送出的字串跟 UI 按鈕、測試計畫送的完全一樣,
所以這裡沒有任何「CLI 專用」的捷徑 —— 一律走 `<device>/<port>` 的 `cmd` method。
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass

import ecal.nanobind_core as ecal

from ..core.node import CMD_METHOD

SETTLE = 1.0
"""eCAL 註冊資訊要一點時間才會傳開;列表類命令先等這麼久再問。"""

CALL_TIMEOUT_MS = 5000
TEXT = ecal.DataTypeInformation(name="text", encoding="utf-8")
JSON = ecal.DataTypeInformation(name="json", encoding="utf-8")


@contextmanager
def session(unit_name: str = "st"):
    """包住 eCAL 的 initialize/finalize,確保離開時一定收乾淨。

    **必須帶 `init.ALL`。** 預設的 `init.DEFAULT`(55)**不含 MONITORING**(8),
    少了它 `get_monitoring()` 會直接丟 `RuntimeError: Failed to get monitoring struct.`。
    node 不需要 monitoring,所以只有 st 這種查詢端要開。
    """
    ecal.initialize(unit_name, ecal.init.ALL)
    try:
        yield
    finally:
        ecal.finalize()


# ---------------------------------------------------------------------- 探索

@dataclass(frozen=True)
class NodeInfo:
    name: str
    """service 名稱,也就是 node 名稱:`BTM-4208SD/COM11`、`Log`。"""
    host: str
    pid: int
    unit: str
    status: str = "?"


def list_nodes(settle: float = SETTLE, status_wait: float = 3.0) -> list[NodeInfo]:
    """列出站上的 node,並收一次 status 心跳當燈號。

    心跳是 1 Hz,但新建的 subscriber 跟 publisher 配對還要約 2 秒,所以不是等固定
    秒數 —— **收齊所有 node 就立刻回**,收不齊才等到 `status_wait`。
    """
    time.sleep(settle)
    servers = {}
    for server in ecal.monitoring.get_monitoring().servers:
        servers[server.service_name] = NodeInfo(
            name=server.service_name,
            host=server.host_name,
            pid=server.process_id,
            unit=server.unit_name,
        )
    if not servers:
        return []

    statuses: dict[str, str] = {}
    subs = []
    for name in servers:
        sub = ecal.Subscriber(f"{name}/status")
        sub.set_receive_callback(
            (lambda n: lambda _t, _dt, data, *_: statuses.__setitem__(
                n, data.buffer.decode("utf-8", "replace")))(name)
        )
        subs.append(sub)
    deadline = time.monotonic() + status_wait
    while time.monotonic() < deadline and len(statuses) < len(servers):
        time.sleep(0.05)
    for sub in subs:
        sub.remove_receive_callback()

    return sorted(
        (NodeInfo(n.name, n.host, n.pid, n.unit, statuses.get(n.name, "no heartbeat"))
         for n in servers.values()),
        key=lambda n: n.name,
    )


def list_topics(settle: float = SETTLE) -> list[ecal.monitoring.Topic]:
    """列出所有被發佈的 topic。"""
    time.sleep(settle)
    seen: dict[str, ecal.monitoring.Topic] = {}
    for topic in ecal.monitoring.get_monitoring().publishers:
        seen.setdefault(topic.topic_name, topic)
    return sorted(seen.values(), key=lambda t: t.topic_name)


def list_services(settle: float = SETTLE) -> list[ecal.monitoring.Server]:
    time.sleep(settle)
    servers = ecal.monitoring.get_monitoring().servers
    return sorted(servers, key=lambda s: s.service_name)


# ----------------------------------------------------------------- service

class ServiceUnavailable(Exception):
    pass


def call(service: str, line: str, *, connect_timeout: float = 3.0) -> str:
    """送一行純文字命令,回傳一行 `OK ...` / `ERR ...`。

    跟 UI 按鈕、測試計畫的 `cmd()` 走的是同一條路。
    """
    method = ecal.ServiceMethodInformation(
        method_name=CMD_METHOD, request_type=TEXT, response_type=TEXT
    )
    client = ecal.ServiceClient(service, [method])
    deadline = time.monotonic() + connect_timeout
    while time.monotonic() < deadline:
        instances = client.get_client_instances()
        if instances:
            break
        time.sleep(0.05)
    else:
        raise ServiceUnavailable(
            f"no node is serving '{service}' — try `st service list`"
        )

    ok, response = instances[0].call_with_response(
        CMD_METHOD, line.encode("utf-8"), CALL_TIMEOUT_MS
    )
    if not ok:
        raise ServiceUnavailable(f"call to '{service}' failed: {response.error_msg}")
    return response.response.decode("utf-8", "replace")


def describe(service: str) -> list[dict]:
    """取得命令清單。Tab 補完、`service describe`、UI 按鈕都用這個。"""
    reply = call(service, "describe")
    if not reply.startswith("OK "):
        raise ServiceUnavailable(f"describe failed: {reply}")
    return json.loads(reply[3:])


# --------------------------------------------------------------- 格式化

def format_payload(raw: bytes) -> str:
    """把 topic 收到的 bytes 變成一行人看的字。

    資料 topic 是量測 JSON、activity 是事件 JSON、status 是純文字,
    所以這裡不假設格式,解不出來就原樣印。
    """
    text = raw.decode("utf-8", "replace")
    try:
        message = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text
    if not isinstance(message, dict):
        return text

    stamp = message.get("t", "")
    if "v" in message:  # 量測值
        name = message.get("name", "")
        unit = message.get("unit", "")
        label = f"  ({name})" if name else ""
        return f"{stamp}  {message['v']} {unit}{label}".rstrip()
    if "kind" in message:  # activity
        return f"{stamp}  {message['kind']} {message.get('text', '')}"
    return text


def columns(rows: list[list[str]], headers: list[str]) -> str:
    """對齊的表格,不拉外部套件。"""
    if not rows:
        return ""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    lines.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return "\n".join(lines)
