"""把 `data.csv` 讀回來,分組成 series。

**不對齊、不重取樣、不內插。** 每條 series 保留自己的取樣時刻,所以報告上的每個點
都是真的量到的值 —— 不會出現任何被算出來的數字。各裝置的取樣節奏本來就不同
(BTM-4208SD 一個 channel 約 0.57 Hz、PFR 約 1 Hz),硬湊成同一個網格只會製造假象。

時間戳的處理跟 `chart_template.html` 一致:log 裡的時間是**不帶時區的牆上時間**,
這裡把它當成 UTC 解析成毫秒。模板那邊再用 UTC getter 印回去,一來一回剛好抵消,
所以不管讀報告的人在哪個時區,看到的都是當初記錄的那個鐘面時間。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ...core.payload import TIME_FORMAT

CSV_HEADER = ["t", "topic", "name", "value", "unit"]


def to_ms(text: str) -> int:
    """`YYYY-MM-DD HH:MM:SS.mmm` → epoch 毫秒,當成 UTC 解析(見模組說明)。"""
    moment = datetime.strptime(text.strip(), TIME_FORMAT).replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


@dataclass
class Series:
    topic: str
    unit: str = ""
    name: str = ""
    t: list[int] = field(default_factory=list)
    v: list[float] = field(default_factory=list)

    @property
    def device(self) -> str:
        """topic 的第一段,用來分組:`BTM-4208SD/COM11/ch1` → `BTM-4208SD`。"""
        return self.topic.split("/", 1)[0]

    @property
    def channel(self) -> str:
        return self.topic.rsplit("/", 1)[-1]


def load(path: Path) -> list[Series]:
    """讀 long format 的 data.csv,依 topic 分組。

    壞掉的行直接跳過 —— Log 被砍掉時最後一行可能只寫到一半(README 6)。
    """
    series: dict[str, Series] = {}

    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                moment = to_ms(row["t"])
            except (ValueError, KeyError, TypeError, AttributeError):
                continue

            topic = (row.get("topic") or "").strip()
            try:
                value = float(row["value"])
            except (ValueError, KeyError, TypeError):
                continue

            current = series.get(topic)
            if current is None:
                current = series[topic] = Series(topic)
            # name 與 unit 取最後一筆:set_channel 可能在中途改名。
            current.name = (row.get("name") or "").strip() or current.name
            current.unit = (row.get("unit") or "").strip() or current.unit
            current.t.append(moment)
            current.v.append(value)

    return sorted(series.values(), key=lambda s: s.topic)


def span(series: list[Series]) -> tuple[int, int]:
    """整份記錄的時間範圍。沒有任何資料時回 (0, 0)。"""
    moments = [t for s in series for t in (s.t[:1] + s.t[-1:])]
    return (min(moments), max(moments)) if moments else (0, 0)
