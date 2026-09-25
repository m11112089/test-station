"""量測 payload 的時間戳與 JSON 格式(README 2.2)。

時間戳由 node 在量測當下蓋上,本地時間、不帶時區,格式 ``YYYY-MM-DD HH:MM:SS.mmm``。
跨 PC 時所有機台必須 NTP 對時且設為同一時區。
"""

from __future__ import annotations

import json
from datetime import datetime

TIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def stamp(when: datetime | None = None) -> str:
    """回傳人眼可讀的本地時間戳,毫秒精度。"""
    when = when or datetime.now()
    # strftime 的 %f 是微秒,截成毫秒。
    return when.strftime(TIME_FORMAT)[:-3]


def measurement(value: float, unit: str, name: str, when: datetime | None = None) -> bytes:
    """組出資料 topic 的 payload。"""
    return json.dumps(
        {"t": stamp(when), "v": value, "unit": unit, "name": name},
        ensure_ascii=False,
    ).encode("utf-8")
