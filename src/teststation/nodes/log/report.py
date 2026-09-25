"""把 series 與 marks 灌進 `chart_template.html`,產出 `report.html`。

模板是既有資產(手工報告時代就存在),這裡只負責準備它要的 `DATA` 物件:

* `t0` + 每條 series 的 `t`(相對 t0 的整數毫秒)—— 存相對值省下大量位元組
* `cols[]` 每條一組 `t` / `v`,**長度不必相同**
* `groups` / `order` 由 topic 的第一段(裝置名)自動分組,不認識任何特定裝置
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .sync import Series, load, span

TEMPLATE = Path(__file__).with_name("chart_template.html")
PLACEHOLDER = "/*DATA*/null"
PLOTLY_MARKER = "<!--PLOTLY-->"

PLOTLY_JS = Path(__file__).with_name("plotly.min.js")
"""內嵌用的 plotly.js(從既有那份手工報告抽出來的同一版)。

**報告必須離線可看。** 機台常常沒有對外網路,而報告還會被複製、寄出、封存 ——
指向 CDN 的話,那些情境下開起來就是一片空白。代價是每份報告多約 4.6 MB。
"""

PLOTLY_CDN = "https://cdn.plot.ly/plotly-3.0.1.min.js"
"""萬一 plotly.min.js 不見了才用,而且會在報告裡留下註解說明。"""


def _labels(series: list[Series]) -> list[str]:
    """每條 series 的顯示名稱,重複時用 topic 補足以免圖例分不出來。"""
    wanted = [s.name or s.channel for s in series]
    labels = []
    for label, item in zip(wanted, series):
        labels.append(label if wanted.count(label) == 1 else f"{label} ({item.topic})")
    return labels


def build_data(series: list[Series], *, title: str, note: str = "") -> dict:
    start, end = span(series)
    devices = sorted({s.device for s in series})
    return {
        "title": title,
        "built": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "t0": start,
        "window": {"start": start, "end": end},
        "groups": {device: device for device in devices},
        "order": devices,
        "cols": [
            {
                "name": label,
                "group": item.device,
                "unit": item.unit,
                "ref": False,
                "vis": True,
                "t": [moment - start for moment in item.t],
                "v": item.v,
            }
            for label, item in zip(_labels(series), series)
        ],
        "bands": [],
        "limits": [],
        "note": note,
    }


def _plotly_tag() -> str:
    if PLOTLY_JS.is_file():
        return "<script>" + PLOTLY_JS.read_text(encoding="utf-8") + "</script>"
    return (f"<!-- {PLOTLY_JS.name} 不在,改用 CDN:這份報告離線打不開 -->\n"
            f'<script src="{PLOTLY_CDN}"></script>')


def render(data: dict) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise ValueError(f"{TEMPLATE} 裡找不到 {PLACEHOLDER} 佔位符")
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    template = template.replace(PLACEHOLDER, body, 1)
    return template.replace(PLOTLY_MARKER, _plotly_tag(), 1)


def write(run_dir: Path, *, title: str = "", note: str = "") -> Path:
    """讀 run 資料夾裡的 data.csv,寫出 report.html,回傳它的路徑。"""
    series = load(run_dir / "data.csv")
    data = build_data(series, title=title or run_dir.name, note=note)
    target = run_dir / "report.html"
    target.write_text(render(data), encoding="utf-8")
    return target
