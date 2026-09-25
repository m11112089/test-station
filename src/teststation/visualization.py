"""`uv run visualization <data.csv>` —— 事後產生互動式報告。

**記錄與視覺化是分開的。** Log 只負責把選定的 topic 寫成 CSV;要看圖就拿那個 CSV
跑這個指令。好處是想換畫法、想重畫半年前的 run,都不必重跑測試 ——
CSV 才是資料,報告只是它的一種樣子。

```
uv run visualization logs/2026-09-21_101548_burn_in_45C/data.csv
uv run visualization data.csv -o /tmp/看一下.html --open
```
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from .nodes.log import report, sync


def build(csv_path: Path, output: Path | None = None, title: str = "") -> Path:
    """讀 CSV,寫出報告,回傳它的路徑。"""
    series = sync.load(csv_path)
    if not series:
        raise ValueError(f"{csv_path} 裡沒有可畫的資料")
    data = report.build_data(
        series, title=title or csv_path.parent.name or csv_path.stem
    )
    target = output or csv_path.with_name("report.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report.render(data), encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="visualization", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", type=Path, help="Log 產出的 data.csv")
    parser.add_argument("-o", "--output", type=Path,
                        help="輸出的 HTML(預設:CSV 旁邊的 report.html)")
    parser.add_argument("-t", "--title", default="", help="報告標題(預設:run 資料夾名)")
    parser.add_argument("--open", action="store_true", help="產生後用瀏覽器打開")
    args = parser.parse_args(argv)

    if not args.csv.is_file():
        print(f"ERR 找不到 {args.csv}", file=sys.stderr)
        return 1
    try:
        target = build(args.csv, args.output, args.title)
    except (ValueError, OSError) as exc:
        print(f"ERR {exc}", file=sys.stderr)
        return 1

    size = target.stat().st_size
    print(f"{target}  ({size / 1024 / 1024:.1f} MB,plotly 已內嵌,離線可看)")
    if args.open:
        webbrowser.open(target.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
