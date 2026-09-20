"""UI 的純邏輯測試(README 5.3)。

這裡不碰 eCAL:直接餵 `Station` 的 signal,檢查畫面長出什麼。
測的重點是「**通用 UI**」這件事 —— 面板只認 `describe` 與 topic 名稱,不認裝置。
真的跟 node 通訊的部分在 test_node_contract.py。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QLineEdit, QPushButton

from teststation.ui.bridge import Station, is_data_topic
from teststation.ui.panels import (
    ERROR_COLOR, CommandPanel, LogPanel, NodeList, PlanPanel, ValueTable,
    status_color,
)


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def station(qt_app):
    """Station 建構時不碰 eCAL,所以可以單獨拿來當 signal 來源。"""
    return Station()


# ------------------------------------------------------------- topic 分類

@pytest.mark.parametrize("topic,expected", [
    ("BTM-4208SD/COM11/ch1", True),
    ("PFR-100L/COM20/voltage", True),
    ("SSH/192_168_1_50/soc_temp", True),
    ("BTM-4208SD/COM11/status", False),
    ("BTM-4208SD/COM11/activity", False),
    ("Serial/COM5/raw", False),
])
def test_only_measurement_topics_reach_the_value_table(topic, expected):
    assert is_data_topic(topic) is expected


# ---------------------------------------------------------------- 燈號

def test_status_colours():
    assert status_color("ok") != status_color("reconnecting")
    assert status_color("error: could not open port").name() == ERROR_COLOR
    assert status_color("").name() == status_color("whatever").name()


# ------------------------------------------------------------ Node 清單

def test_node_list_follows_discovery(station):
    panel = NodeList(station)
    station.nodes_changed.emit(["BTM-4208SD/COM11", "Log"])
    assert [panel._list.item(i).text() for i in range(2)] == ["BTM-4208SD/COM11", "Log"]
    assert panel.current() == "BTM-4208SD/COM11"


def test_node_list_keeps_the_selection_across_refreshes(station):
    panel = NodeList(station)
    station.nodes_changed.emit(["A/COM1", "B/COM2"])
    panel._list.setCurrentRow(1)
    station.nodes_changed.emit(["A/COM1", "B/COM2", "C/COM3"])
    assert panel.current() == "B/COM2"


def test_node_list_shows_the_status_as_a_tooltip(station):
    panel = NodeList(station)
    station.nodes_changed.emit(["A/COM1"])
    station.node_status.emit("A/COM1", "error: port gone")
    assert panel._list.item(0).toolTip() == "error: port gone"
    assert panel._list.item(0).foreground().color().name() == ERROR_COLOR


# ----------------------------------------------------- 命令面板由 describe 長出

DESCRIBE = [
    {"name": "output_on", "label": "開始送電"},
    {"name": "output_off", "label": "停止送電", "style": "danger"},
    {"name": "set_voltage", "label": "設定電壓",
     "args": [{"name": "v", "unit": "V", "min": 0, "max": 50}]},
    {"name": "set_channel", "label": "命名 channel",
     "args": [{"name": "channel"}, {"name": "name"}]},
]


def _panel_for(station, commands=DESCRIBE, node="PFR-100L/COM20"):
    panel = CommandPanel(station)
    panel._node = node
    station.describe_ready.emit(node, commands)
    return panel


def test_buttons_come_from_describe(station):
    panel = _panel_for(station)
    buttons = panel._generated.findChildren(QPushButton)
    assert [b.toolTip() for b in buttons] == [
        "output_on", "output_off", "set_voltage", "set_channel"]
    assert [b.text() for b in buttons][0] == "開始送電"


def test_danger_style_is_applied(station):
    panel = _panel_for(station)
    danger = [b for b in panel._generated.findChildren(QPushButton)
              if b.toolTip() == "output_off"][0]
    assert ERROR_COLOR in danger.styleSheet()


def test_bounded_argument_becomes_a_spin_box_with_the_limits(station):
    """README 2.3:有 min/max 就依限制產生輸入框。"""
    panel = _panel_for(station)
    button = [b for b in panel._generated.findChildren(QPushButton)
              if b.toolTip() == "set_voltage"][0]
    spin = button.parent().findChildren(QDoubleSpinBox)[0]
    assert (spin.minimum(), spin.maximum()) == (0.0, 50.0)


def test_unbounded_arguments_become_plain_fields(station):
    panel = _panel_for(station)
    button = [b for b in panel._generated.findChildren(QPushButton)
              if b.toolTip() == "set_channel"][0]
    fields = button.parent().findChildren(QLineEdit)
    assert [f.placeholderText() for f in fields] == ["channel", "name"]


def test_clicking_a_button_sends_the_right_command_line(station, monkeypatch):
    sent = []
    monkeypatch.setattr(station, "call", lambda service, line: sent.append((service, line)))
    panel = _panel_for(station)
    button = [b for b in panel._generated.findChildren(QPushButton)
              if b.toolTip() == "set_channel"][0]
    channel, name = button.parent().findChildren(QLineEdit)
    channel.setText("ch3")
    name.setText("SoC_heatsink")
    button.click()
    assert sent == [("PFR-100L/COM20", "set_channel ch3 SoC_heatsink")]


def test_free_command_line_sends_verbatim(station, monkeypatch):
    """README 1.2:UI 送的字串跟 st 手打的完全一樣。"""
    sent = []
    monkeypatch.setattr(station, "call", lambda service, line: sent.append((service, line)))
    panel = _panel_for(station)
    panel._free.setText("set_voltage 12")
    panel._send_free()
    assert sent == [("PFR-100L/COM20", "set_voltage 12")]


def test_describe_for_another_node_is_ignored(station):
    """使用者換選別的 node 時,慢回來的 describe 不該蓋掉畫面。"""
    panel = CommandPanel(station)
    panel._node = "A/COM1"
    station.describe_ready.emit("B/COM2", DESCRIBE)
    assert panel._generated.findChildren(QPushButton) == []


# ----------------------------------------------------------- 即時數值表

def test_value_table_adds_then_updates_in_place(station):
    table = ValueTable(station)
    station.measurement.emit("BTM-4208SD/COM11/ch1",
                             {"t": "2026-09-20 00:00:00.000", "v": 25.0,
                              "unit": "°C", "name": "ch1"})
    station.measurement.emit("BTM-4208SD/COM11/ch1",
                             {"t": "2026-09-20 00:00:01.000", "v": 26.5,
                              "unit": "°C", "name": "SoC"})
    assert table._table.rowCount() == 1        # 同一個 topic 不會多開一列
    assert table._table.item(0, 1).text() == "SoC"
    assert table._table.item(0, 2).text() == "26.5"


def test_value_table_keeps_one_row_per_topic(station):
    table = ValueTable(station)
    for channel in ("ch1", "ch2", "ch1"):
        station.measurement.emit(f"X/COM1/{channel}", {"v": 1, "unit": "V", "name": channel})
    assert table._table.rowCount() == 2


# --------------------------------------------------- 計畫 / Log 面板的命令

def test_plan_panel_lists_plans_and_sends_run(station, monkeypatch, tmp_path):
    (tmp_path / "burn_in_45C.py").write_text("", encoding="utf-8")
    sent = []
    monkeypatch.setattr(station, "call", lambda service, line: sent.append((service, line)))
    panel = PlanPanel(station, tmp_path)
    panel._run()
    assert sent == [("Main-Control", "run burn_in_45C.py")]


def test_plan_panel_survives_an_empty_plans_dir(station, tmp_path):
    panel = PlanPanel(station, tmp_path / "nope")
    assert panel._plans.currentText().startswith("(")


def test_log_panel_sends_start_mark_stop(station, monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(station, "call", lambda service, line: sent.append((service, line)))
    panel = LogPanel(station, tmp_path)
    panel._run_name.setText("burn_in_45C")
    panel._start()
    panel._mark_text.setText("DUT power on")
    panel._mark()
    assert sent == [("Log", "start burn_in_45C"), ("Log", "mark DUT power on")]


def test_log_panel_ignores_an_empty_run_name(station, monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(station, "call", lambda service, line: sent.append((service, line)))
    panel = LogPanel(station, tmp_path)
    panel._start()
    assert sent == []


def test_log_panel_reports_when_there_is_no_report(station, tmp_path):
    panel = LogPanel(station, tmp_path)
    panel._open_report()
    assert "report.html" in panel._state.text()
