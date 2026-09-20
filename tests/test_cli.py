"""`st` 的純邏輯測試:參數解析、payload 格式化、Tab 補完。

這裡不碰 eCAL —— 跟實機通訊的部分在 test_node_contract.py。
"""

from __future__ import annotations

import pytest

from teststation.cli import common, repl
from teststation.cli.common import columns, format_payload
from teststation.cli.main import build_parser


# -------------------------------------------------- README 5.1 的命令都要能解析

@pytest.mark.parametrize("argv,expected", [
    (["node", "list"], ("node", "list")),
    (["service", "list"], ("service", "list")),
    (["service", "call", "PFR-100L/COM20", "set_voltage 12"], ("service", "call")),
    (["service", "describe", "PFR-100L/COM20"], ("service", "describe")),
    (["topic", "list"], ("topic", "list")),
    (["topic", "echo", "BTM-4208SD/COM7/ch3"], ("topic", "echo")),
    (["topic", "pub", "test/fake/ch1", '{"v": 1.0}'], ("topic", "pub")),
    (["topic", "hz", "BTM-4208SD/COM7/ch3"], ("topic", "hz")),
])
def test_readme_examples_parse(argv, expected):
    args = build_parser().parse_args(argv)
    assert (args.group, args.action) == expected


def test_launch_takes_a_config():
    args = build_parser().parse_args(["launch", "launch/burn_in_station.yaml"])
    assert args.group == "launch" and args.config == "launch/burn_in_station.yaml"


def test_console_takes_a_target():
    args = build_parser().parse_args(["console", "Serial/COM5"])
    assert args.group == "console" and args.target == "Serial/COM5"


def test_service_call_joins_unquoted_words():
    """REPL 裡不加引號直接打 `service call X set_channel ch1 name` 也要能用。"""
    args = build_parser().parse_args(
        ["service", "call", "X/COM1", "set_channel", "ch1", "SoC"]
    )
    assert " ".join(args.command) == "set_channel ch1 SoC"


# ------------------------------------------------------------ payload 格式化

def test_format_measurement():
    raw = b'{"t": "2026-09-20 00:41:30.524", "v": 31.0, "unit": "\xc2\xb0C", "name": "SoC"}'
    assert format_payload(raw) == "2026-09-20 00:41:30.524  31.0 °C  (SoC)"


def test_format_activity():
    raw = '{"t": "2026-09-20 00:41:30.524", "kind": "PUB", "text": "x/y 1 °C"}'.encode()
    assert format_payload(raw) == "2026-09-20 00:41:30.524  PUB x/y 1 °C"


def test_format_status_is_plain_text():
    """status topic 不是 JSON,要原樣印。"""
    assert format_payload(b"ok") == "ok"
    assert format_payload(b"error: could not open port") == "error: could not open port"


def test_format_tolerates_garbage():
    assert format_payload(b"\xff\xfe not json") != ""


def test_format_json_that_is_not_an_object():
    assert format_payload(b"[1, 2, 3]") == "[1, 2, 3]"


# ------------------------------------------------------------------- 表格

def test_columns_aligns_and_has_a_rule():
    text = columns([["a", "1"], ["bbb", "22"]], ["NAME", "N"])
    lines = text.splitlines()
    assert lines[0].startswith("NAME")
    assert set(lines[1]) <= {"-", " "}
    assert lines[2].startswith("a  ")


def test_columns_of_nothing_is_empty():
    assert columns([], ["A"]) == ""


# ---------------------------------------------------------------- Tab 補完

@pytest.fixture
def completer(monkeypatch):
    monkeypatch.setattr(repl.StCompleter, "_services",
                        lambda self: ["BTM-4208SD/COM11", "Log"])
    monkeypatch.setattr(repl.StCompleter, "_topics",
                        lambda self: ["BTM-4208SD/COM11/ch1", "BTM-4208SD/COM11/status"])
    monkeypatch.setattr(repl.StCompleter, "_methods",
                        lambda self, service: ["describe", "set_channel", "reconnect"])
    return repl.StCompleter()


def test_completes_top_level_groups(completer):
    assert set(completer._candidates([], 0)) >= {
        "node", "service", "topic", "console", "launch"}


def test_completes_actions_of_a_group(completer):
    assert completer._candidates(["topic"], 1) == ["list", "echo", "pub", "hz"]


def test_completes_service_names_for_call(completer):
    assert "Log" in completer._candidates(["service", "call"], 2)


def test_completes_topic_names_for_echo(completer):
    assert "BTM-4208SD/COM11/ch1" in completer._candidates(["topic", "echo"], 2)


def test_completes_methods_from_describe(completer):
    """README 5.1:Tab 補完來自 describe。"""
    candidates = completer._candidates(["service", "call", "BTM-4208SD/COM11"], 3)
    assert candidates == ["describe", "set_channel", "reconnect"]


def test_console_completes_service_names(completer):
    assert "Log" in completer._candidates(["console"], 1)


def test_completion_failure_does_not_raise(monkeypatch):
    """站台沒起來時按 Tab 不該把 REPL 弄掛。"""
    monkeypatch.setattr(common, "list_services",
                        lambda settle=0: (_ for _ in ()).throw(RuntimeError("no ecal")))
    assert repl.StCompleter()._services() == []
