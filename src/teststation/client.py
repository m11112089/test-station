"""站台的 client 層:eCAL session、探索、service 呼叫、payload 處理。

**不依賴 Qt。** 這一點是刻意的:UI 用它,測試計畫的 `control` runner 也要用它,
而那是一個無頭的 Python 腳本(README 4)。把這些搬進 `ui/` 會逼 runner 去 import
PySide6,或是再寫一份一模一樣的 ServiceClient 程式碼。

命令來源是平權的(README 1.2):UI 按鈕與測試計畫送出的字串完全一樣,
走的也是同一個 `<device>/<port>` 的 `cmd` method —— 這裡沒有任何捷徑。
"""

from __future__ import annotations

import json
import time
import statistics
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass

import ecal.nanobind_core as ecal

from .core.node import CMD_METHOD
from .core.payload import stamp

SETTLE = 1.0
"""eCAL 註冊資訊要一點時間才會傳開;列表類命令先等這麼久再問。"""

CALL_TIMEOUT_MS = 5000
TEXT = ecal.DataTypeInformation(name="text", encoding="utf-8")
JSON = ecal.DataTypeInformation(name="json", encoding="utf-8")


@contextmanager
def session(unit_name: str):
    """包住 eCAL 的 initialize/finalize,確保離開時一定收乾淨。

    **必須帶 `init.ALL`。** 預設的 `init.DEFAULT`(55)**不含 MONITORING**(8),
    少了它 `get_monitoring()` 會直接丟 `RuntimeError: Failed to get monitoring struct.`。
    node 不需要 monitoring,所以只有 UI 這種查詢端要開。
    """
    ecal.initialize(unit_name, ecal.init.ALL)
    try:
        yield
    finally:
        ecal.finalize()


# ---------------------------------------------------------------------- 探索

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


# ------------------------------------------------------------------ 頻率

@dataclass(frozen=True)
class Rate:
    """一串取樣時刻算出來的頻率統計。"""

    samples: int
    hz: float
    min_gap: float
    max_gap: float
    std: float


def rate_of(times: Sequence[float]) -> Rate | None:
    """純計算,不碰 eCAL。不足兩筆回 None。

    `st topic hz` 與 UI 的頻率面板都用這個 —— 兩邊只差在誰去收時刻、算完怎麼顯示。
    """
    if len(times) < 2:
        return None
    gaps = [b - a for a, b in zip(times, times[1:])]
    total = sum(gaps)
    return Rate(
        samples=len(times),
        hz=len(gaps) / total if total else 0.0,
        min_gap=min(gaps),
        max_gap=max(gaps),
        std=statistics.stdev(gaps) if len(gaps) > 1 else 0.0,
    )


# ------------------------------------------------------------------ 發佈

def prepare_payload(text: str) -> dict:
    """JSON 字串 → payload,少了 `t` 就補上現在時間。

    所以 `{"v": 1.0}` 直接就符合 README 2.2 的 schema。不是合法 JSON 會丟 ValueError。
    """
    message = json.loads(text)
    if not isinstance(message, dict):
        raise ValueError("payload 必須是 JSON 物件")
    return message if "t" in message else {"t": stamp(), **message}


def wait_for_subscriber(publisher: ecal.Publisher, timeout: float = 3.0) -> bool:
    """等到真的有人在聽才回 True。

    新建的 publisher 要先跟訂閱端配對才送得到,否則第一筆直接掉進虛空。
    eCAL 的註冊大約 1 Hz,所以等的是「有沒有訂閱者」而不是固定秒數。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and publisher.get_subscriber_count() == 0:
        time.sleep(0.05)
    return publisher.get_subscriber_count() > 0


def encode(message: dict) -> bytes:
    return json.dumps(message, ensure_ascii=False).encode("utf-8")
