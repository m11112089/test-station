"""Log node 的純邏輯測試:CSV 讀回、報告組裝。

不碰 eCAL、不碰硬體 —— 端到端的部分靠實機驗。
重點是守住那個決定:**不對齊、不重取樣、不內插。**
"""

from __future__ import annotations

import json

import pytest

from teststation.nodes.log import report, sync
from teststation.nodes.log.sync import Series

CSV = """t,topic,name,value,unit
2026-09-20 20:12:38.824,BTM-4208SD/COM11/ch3,ch3,37.6,°C
2026-09-20 20:12:40.075,BTM-4208SD/COM11/ch1,ch1,31.5,°C
2026-09-20 20:12:40.574,BTM-4208SD/COM11/ch3,ch3,37.7,°C
2026-09-20 20:12:42.838,Log/mark,DUT power on,,
2026-09-20 20:12:43.573,BTM-4208SD/COM11/ch1,SoC,31.6,°C
2026-09-20 20:12:44.000,PFR-100L/COM20/voltage,voltage,12.0,V
"""


@pytest.fixture
def run_dir(tmp_path):
    (tmp_path / "data.csv").write_text(CSV, encoding="utf-8")
    return tmp_path


# ------------------------------------------------------------- 時間戳

def test_timestamps_are_parsed_as_wall_clock():
    """log 裡是不帶時區的牆上時間,當成 UTC 解析,跟 chart_template 的 wall() 對稱。"""
    assert sync.to_ms("2026-09-20 20:12:38.824") == 1789935158824


def test_millisecond_precision_survives():
    a = sync.to_ms("2026-09-20 20:12:38.824")
    b = sync.to_ms("2026-09-20 20:12:38.825")
    assert b - a == 1


# --------------------------------------------------------------- 讀回

def test_series_keep_their_own_sample_times(run_dir):
    """核心決定:每條 series 各自的時間戳,長度不必相同。"""
    series, _marks = sync.load(run_dir / "data.csv")
    by_topic = {s.topic: s for s in series}
    assert len(by_topic["BTM-4208SD/COM11/ch3"].t) == 2
    assert len(by_topic["BTM-4208SD/COM11/ch1"].t) == 2
    assert len(by_topic["PFR-100L/COM20/voltage"].t) == 1
    for item in series:
        assert len(item.t) == len(item.v)


def test_marks_are_pulled_out_of_the_same_file(run_dir):
    _series, marks = sync.load(run_dir / "data.csv")
    assert [m.text for m in marks] == ["DUT power on"]
    assert marks[0].t == sync.to_ms("2026-09-20 20:12:42.838")


def test_marks_are_not_a_series(run_dir):
    series, _marks = sync.load(run_dir / "data.csv")
    assert sync.MARK_TOPIC not in {s.topic for s in series}


def test_renaming_mid_run_takes_the_latest_name(run_dir):
    """測試中途下 set_channel,報告上要用新名字。"""
    series, _ = sync.load(run_dir / "data.csv")
    ch1 = next(s for s in series if s.topic.endswith("ch1"))
    assert ch1.name == "SoC"


def test_device_and_channel_come_from_the_topic(run_dir):
    series, _ = sync.load(run_dir / "data.csv")
    ch1 = next(s for s in series if s.topic.endswith("ch1"))
    assert (ch1.device, ch1.channel) == ("BTM-4208SD", "ch1")


def test_a_truncated_last_line_is_skipped(tmp_path):
    """Log 被砍掉時最後一行可能只寫到一半(README 6)。"""
    (tmp_path / "data.csv").write_text(CSV + "2026-09-20 20:12:45.0", encoding="utf-8")
    series, _ = sync.load(tmp_path / "data.csv")
    assert sum(len(s.v) for s in series) == 5


def test_non_numeric_values_are_skipped(tmp_path):
    (tmp_path / "data.csv").write_text(
        CSV + "2026-09-20 20:12:46.000,X/COM1/ch1,ch1,not-a-number,V\n", encoding="utf-8")
    series, _ = sync.load(tmp_path / "data.csv")
    assert "X/COM1/ch1" not in {s.topic for s in series}


def test_span_covers_data_and_marks(run_dir):
    series, marks = sync.load(run_dir / "data.csv")
    start, end = sync.span(series, marks)
    assert start == sync.to_ms("2026-09-20 20:12:38.824")
    assert end == sync.to_ms("2026-09-20 20:12:44.000")


def test_span_of_nothing_is_zero():
    assert sync.span([], []) == (0, 0)


# --------------------------------------------------------------- 報告

def test_build_data_keeps_per_series_times(run_dir):
    series, marks = sync.load(run_dir / "data.csv")
    data = report.build_data(series, marks, title="t")
    lengths = {c["name"]: len(c["t"]) for c in data["cols"]}
    assert sorted(lengths.values()) == [1, 2, 2]
    for col in data["cols"]:
        assert len(col["t"]) == len(col["v"])


def test_times_are_offsets_from_t0(run_dir):
    """存相對值省空間;模板那邊再加回 t0。"""
    series, marks = sync.load(run_dir / "data.csv")
    data = report.build_data(series, marks, title="t")
    assert data["t0"] == sync.to_ms("2026-09-20 20:12:38.824")
    assert min(t for c in data["cols"] for t in c["t"]) == 0
    assert all(t >= 0 for c in data["cols"] for t in c["t"])


def test_no_grid_fields_are_emitted(run_dir):
    """t0/dt/n 的等間隔網格已經拿掉了,別讓它悄悄回來。"""
    series, marks = sync.load(run_dir / "data.csv")
    data = report.build_data(series, marks, title="t")
    assert "dt" not in data and "n" not in data


def test_groups_come_from_the_device(run_dir):
    series, marks = sync.load(run_dir / "data.csv")
    data = report.build_data(series, marks, title="t")
    assert data["order"] == ["BTM-4208SD", "PFR-100L"]
    assert {c["group"] for c in data["cols"]} == {"BTM-4208SD", "PFR-100L"}


def test_marks_reach_the_report(run_dir):
    series, marks = sync.load(run_dir / "data.csv")
    data = report.build_data(series, marks, title="t")
    assert data["marks"] == [
        {"t": sync.to_ms("2026-09-20 20:12:42.838"), "text": "DUT power on"}]


def test_duplicate_labels_are_disambiguated():
    """兩台同型裝置都有 ch1 時,圖例不能有兩條一樣的名字。"""
    a = Series("BTM-4208SD/COM7/ch1", "°C", "ch1", [0], [1.0])
    b = Series("BTM-4208SD/COM8/ch1", "°C", "ch1", [0], [2.0])
    data = report.build_data([a, b], [], title="t")
    names = [c["name"] for c in data["cols"]]
    assert len(set(names)) == 2
    assert all("ch1" in n for n in names)


# --------------------------------------------------------------- 產出

def test_report_is_self_contained(run_dir):
    """報告會被複製、寄出、在離線機台上打開 —— plotly 必須內嵌。"""
    target = report.write(run_dir, title="smoke")
    html = target.read_text(encoding="utf-8")
    assert report.PLACEHOLDER not in html
    # plotly.js 內文自己就會提到 cdn.plot.ly,所以要看的是有沒有「外連的 script 標籤」
    assert "<script src=" not in html
    assert "plotly.js v" in html
    assert len(html) > 1_000_000


def test_report_falls_back_to_the_cdn_and_says_so(run_dir, monkeypatch, tmp_path):
    monkeypatch.setattr(report, "PLOTLY_JS", tmp_path / "missing.js")
    html = report.render(report.build_data([], [], title="t"))
    assert '<script src="' in html and "cdn.plot.ly" in html
    assert "離線打不開" in html


def test_report_data_is_valid_json(run_dir):
    import re
    target = report.write(run_dir, title="smoke")
    html = target.read_text(encoding="utf-8")
    payload = re.search(r"const DATA = (\{.*?\});", html, re.S).group(1)
    data = json.loads(payload)
    assert data["title"] == "smoke"
    assert len(data["cols"]) == 3


def test_missing_placeholder_is_an_error(run_dir, monkeypatch, tmp_path):
    broken = tmp_path / "broken.html"
    broken.write_text("<html>no placeholder</html>", encoding="utf-8")
    monkeypatch.setattr(report, "TEMPLATE", broken)
    with pytest.raises(ValueError, match="佔位符"):
        report.render({})
