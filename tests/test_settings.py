"""設定值的契約:哪些命令算設定、怎麼判斷「同一個設定」、UI 怎麼記住它們。

這一層存在的理由是**下次開 launch 檔不能意外送電**:只有 node 自己標成
`setting` 的命令才會被存下來重放,`output_on` 那種動作不會。
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from teststation.core.describe import Arg, Command, setting_key, to_json
from teststation.ui.bridge import Station

import json


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def station(qt_app):
    return Station()


# --------------------------------------------------- 哪些命令是設定

def test_describe_carries_the_setting_flag():
    commands = [
        Command("set_voltage", "設定電壓", lambda v: "", args=(Arg("v"),), setting=True),
        Command("output_on", "開始送電", lambda: ""),
    ]
    by_name = {c["name"]: c for c in json.loads(to_json(commands))}
    assert by_name["set_voltage"]["setting"] is True
    assert "setting" not in by_name["output_on"]   # 動作不標,存檔時才不會被撈走


def test_set_channel_is_a_setting():
    """共通 method 裡只有 set_channel 是設定 —— 它設的是會留下來的名字。"""
    from teststation.core.node import Node

    class Stub(Node):
        device = "X"
        def open_device(self): ...
        def close_device(self): ...
        def poll(self): ...

    commands = {c["name"]: c for c in json.loads(Stub("COM1")._cmd_describe())}
    assert commands["set_channel"].get("setting") is True
    assert "setting" not in commands["reconnect"]
    assert "setting" not in commands["describe"]


# ------------------------------------------------ 什麼算「同一個設定」

@pytest.mark.parametrize("line,arity,key", [
    ("set_voltage 12", 1, "set_voltage"),
    ("set_voltage 20", 1, "set_voltage"),
    ("set_channel ch3 SoC", 2, "set_channel ch3"),
    ("set_channel ch1 DDR", 2, "set_channel ch1"),
    ("record a/b/c x/y/z", 1, "record"),          # variadic:整份清單是一個設定
    ("output_on", 0, "output_on"),
])
def test_setting_key(line, arity, key):
    assert setting_key(line, arity) == key


def test_channels_are_separate_settings_but_a_rename_overwrites():
    keys = {setting_key(line, 2) for line in
            ["set_channel ch3 SoC", "set_channel ch1 DDR", "set_channel ch3 CPU"]}
    assert keys == {"set_channel ch3", "set_channel ch1"}


# ------------------------------------------------- Station 記住設定值

PFR = [
    {"name": "set_voltage", "label": "設定電壓", "setting": True,
     "args": [{"name": "v"}]},
    {"name": "output_on", "label": "開始送電"},
]
LOG = [{"name": "record", "label": "選 topic", "setting": True,
        "args": [{"name": "topics"}]}]


def _teach(station, node, commands):
    """讓 Station 知道這個 node 有哪些命令(平常由 describe 回來填)。"""
    station._describes[node] = commands


def test_only_settings_are_remembered(station):
    _teach(station, "PFR-100L/COM20", PFR)
    station.remember_setting("PFR-100L/COM20", "set_voltage 12")
    station.remember_setting("PFR-100L/COM20", "output_on")
    assert station.settings == {"PFR-100L/COM20": {"set_voltage": "set_voltage 12"}}


def test_the_last_value_wins(station):
    _teach(station, "PFR-100L/COM20", PFR)
    for value in ("set_voltage 12", "set_voltage 20", "set_voltage 5"):
        station.remember_setting("PFR-100L/COM20", value)
    assert station.settings["PFR-100L/COM20"] == {"set_voltage": "set_voltage 5"}


def test_a_whole_topic_list_is_one_setting(station):
    _teach(station, "Log", LOG)
    station.remember_setting("Log", "record a/b/c x/y/z")
    station.remember_setting("Log", "record a/b/c")
    assert station.settings["Log"] == {"record": "record a/b/c"}


def test_unknown_nodes_are_ignored(station):
    """還沒拿到 describe 就不知道那是不是設定 —— 寧可不存,也不要亂存。"""
    station.remember_setting("Mystery/COM9", "set_voltage 12")
    assert station.settings == {}


def test_settings_are_per_node(station):
    _teach(station, "A/COM1", PFR)
    _teach(station, "B/COM2", PFR)
    station.remember_setting("A/COM1", "set_voltage 12")
    station.remember_setting("B/COM2", "set_voltage 24")
    assert station.settings["A/COM1"] != station.settings["B/COM2"]


def test_a_change_is_announced(station, qt_app):
    _teach(station, "A/COM1", PFR)
    seen = []
    station.settings_changed.connect(lambda: seen.append(True))
    station.remember_setting("A/COM1", "set_voltage 12")
    station.remember_setting("A/COM1", "output_on")     # 不是設定,不該通知
    assert len(seen) == 1
