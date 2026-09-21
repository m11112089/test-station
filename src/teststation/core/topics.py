"""Topic 與 payload 的共通契約(README 2.2)。

這裡是**唯一**定義「什麼是共通 topic」「什麼算量測值」的地方。
node、`st`、UI 都 import 它 —— 這三個曾經各寫一份,而且寫得不一樣。

注意有**兩個**判斷式,因為 node 與觀察端問的不是同一個問題:

* Log 問「我要不要訂這個 topic」 —— `raw` 要訂(它要寫進 console.log)
* UI 問「這是量測值嗎」 —— `raw` 不是

把它們合成一個會弄壞其中一邊。
"""

from __future__ import annotations

LOG_SERVICE = "Log"
"""Log node 的固定 service 名稱(README 2.1)。UI 與 launcher 都要用到。"""

STATUS = "status"
ACTIVITY = "activity"
STATE = "state"
RAW = "raw"

COMMON_CHANNELS = frozenset({STATUS, ACTIVITY, STATE})
"""node 自己的狀態,跟量測無關(README 2.2)。

`state` 是給 node 放自己那份比 `status` 更細的狀態用的(例如 Log 的錄製進度)。
不列進來的話它會被當成可記錄的資料 topic 出現在清單上。
"""


def channel_of(topic: str) -> str:
    """topic 的最後一段:`BTM-4208SD/COM11/ch1` → `ch1`。"""
    return topic.rsplit("/", 1)[-1]


def is_common(topic: str) -> bool:
    """`status` / `activity` —— node 自己的狀態,任何人都不該把它當資料。"""
    return channel_of(topic) in COMMON_CHANNELS


def is_raw(topic: str) -> bool:
    """DUT node 專屬:DUT 原始輸出的每一行。"""
    return channel_of(topic) == RAW


def is_measurement_topic(topic: str) -> bool:
    """可能帶量測值的 topic。**光看名字不夠**,還要看 payload,見下。"""
    return not is_common(topic) and not is_raw(topic)


def is_measurement(payload: object) -> bool:
    """payload 帶 `v` 才是量測值(README 2.2)。

    這條規則讓 node 自有的狀態 topic(例如 `Log/state`)自然被擋在資料之外,
    不必維護一份「哪些 topic 要排除」的名單 —— 那種名單一定會漏。
    """
    return isinstance(payload, dict) and "v" in payload
