"""「存成 launch 檔」要把設定配回**正確的 node**。

曾經的 bug:launcher 用 `_processes` 的位置去對 `specs` 的位置,但 `run: log` 被跳過、
不起行程,從那一筆之後就錯開一格 —— PEL 的 `set_current 0.5` / `set_mode CV`
被存到 `pfr_100l` 底下,開檔就把 PFR 的限流改成 0.5 A。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from teststation.core.topics import LOG_SERVICE
from teststation.ui import launch as launch_module
from teststation.ui.bridge import Station
from teststation.ui.launch import Launcher, launch_document

PFR, PEL = "PFR-100L/COM7", "PEL-3031E/COM3"

SPECS = [
    {"run": "log", "setting": ["record a b"]},
    {"run": "pfr_100l", "args": "--port COM7"},
    {"run": "pel_3031e", "args": "--port COM3"},
]

SETTINGS = {
    LOG_SERVICE: {"record": "record a b"},
    PFR: {"set_channel voltage": "set_channel voltage V_out"},
    PEL: {"set_current": "set_current 0.5", "set_mode": "set_mode CV"},
}


# ------------------------------------------------------- launch_document

def test_settings_follow_spec_position_past_the_skipped_log_entry():
    document = launch_document(SPECS, {1: PFR, 2: PEL}, SETTINGS, 30.0)
    assert document["nodes"] == [
        {"run": "log", "setting": ["record a b"]},
        {"run": "pfr_100l", "args": "--port COM7", "setting": ["set_channel voltage V_out"]},
        {"run": "pel_3031e", "args": "--port COM3",
         "setting": ["set_current 0.5", "set_mode CV"]},
    ]
    assert document["ready_timeout"] == 30.0


def test_settings_are_saved_in_the_order_they_were_last_made(qt_station):
    """PEL 設 CP 值會順便切到 CP,所以重放順序決定最後的模式,必須照「最後一次設定」的先後。

    這裡最後一步是 `set_power 3`,儀器當下是 CP。照字母排序存會變成
    current → mode → power,碰巧也是 CP;但只要最後一步是 `set_mode`,排序就會讓
    `set_power` / `set_voltage` 排到它後面,把模式蓋掉。
    """
    qt_station._describes[PEL] = [
        {"name": n, "setting": True, "args": [{"name": "x"}]}
        for n in ("set_mode", "set_power", "set_current")]
    for line in ("set_power 2", "set_current 0.5", "set_mode CV", "set_power 3"):
        qt_station.remember_setting(PEL, line)
    nodes = launch_document([{"run": "pel_3031e"}], {0: PEL}, qt_station.settings, 30.0)["nodes"]
    # set_power 重設過,移到最後:重放後儀器是 CP(跟實機當下一樣),不是 CV。
    assert nodes[0]["setting"] == ["set_current 0.5", "set_mode CV", "set_power 3"]


def test_two_nodes_of_the_same_kind_keep_their_own_settings():
    specs = [{"run": "pfr_100l", "args": "--port COM7"}, {"run": "pfr_100l", "args": "--port COM8"}]
    settings = {"PFR-100L/COM7": {"set_voltage": "set_voltage 12"},
                "PFR-100L/COM8": {"set_voltage": "set_voltage 5"}}
    nodes = launch_document(specs, {0: "PFR-100L/COM7", 1: "PFR-100L/COM8"}, settings, 30.0)["nodes"]
    assert [n["setting"] for n in nodes] == [["set_voltage 12"], ["set_voltage 5"]]


def test_node_that_never_registered_is_saved_without_settings():
    nodes = launch_document(SPECS, {1: PFR}, SETTINGS, 30.0)["nodes"]
    assert nodes[2] == {"run": "pel_3031e", "args": "--port COM3"}


@pytest.fixture
def qt_station():
    QApplication.instance() or QApplication([])
    return Station()


# ------------------------------------------------------------- Launcher

class FakeProcess:
    """不真的起行程;launcher 只需要它能接 signal、能被關掉。"""

    readyReadStandardOutput = SimpleNamespace(connect=lambda _slot: None)

    def state(self):
        return QProcess.NotRunning


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    station = Station()
    runner = Launcher(station, tmp_path)
    monkeypatch.setattr(runner, "_spawn_process", lambda run, args: FakeProcess())
    monkeypatch.setattr(launch_module.client, "call", lambda *a, **kw: "OK")
    config = tmp_path / "station.yaml"
    config.write_text(
        "nodes:\n"
        "  - run: log\n    setting: [record a b]\n"
        "  - run: pfr_100l\n    args: --port COM7\n"
        "  - run: pel_3031e\n    args: --port COM3\n",
        encoding="utf-8")
    return SimpleNamespace(runner=runner, station=station, config=config)


def _register(station: Station, service: str) -> None:
    """模擬 discovery 發現一個新 service,接著它回報 ok。"""
    station._nodes = [*station._nodes, service]
    station.nodes_changed.emit(station.nodes)
    station.node_status.emit(service, "ok")


def test_launcher_maps_each_spec_to_the_service_it_started(launcher):
    launcher.runner.start(launcher.config)
    _register(launcher.station, PFR)
    _register(launcher.station, PEL)
    assert launcher.runner.services == {1: PFR, 2: PEL}


def test_restarting_forgets_the_previous_mapping(launcher):
    launcher.runner.start(launcher.config)
    _register(launcher.station, PFR)
    launcher.runner.start(launcher.config)
    assert launcher.runner.services == {}
