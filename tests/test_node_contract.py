"""core.Node 的通訊契約測試(README 2.1、2.2、2.3)。

真的跑一個 eCAL node 起來,用 eCAL client 呼叫它、訂閱它的 topic,
確認「一行純文字命令 → OK/ERR」與 payload 格式真的成立。
裝置部分用 stub(測的是 core 的契約,不是某台儀器)。
"""

from __future__ import annotations

import json
import re
import threading
import time

import ecal.nanobind_core as ecal
import pytest

from teststation.core.node import CMD_METHOD, Node

TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$")


class StubNode(Node):
    """不碰硬體的 node,只為了驗 core 的 service/topic 行為。"""

    device = "TEST-NODE"

    def open_device(self) -> None:
        pass

    def close_device(self) -> None:
        pass

    def poll(self) -> None:
        self.publish("ch1", 25.0, "°C")
        time.sleep(0.1)


@pytest.fixture(scope="module")
def node():
    instance = StubNode("COM99")
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    for _ in range(100):
        if instance.status == "ok":
            break
        time.sleep(0.05)
    yield instance
    instance.stop()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def call(node):
    method = ecal.ServiceMethodInformation(method_name=CMD_METHOD)
    client = ecal.ServiceClient(node.prefix, [method])
    for _ in range(100):
        if client.get_client_instances():
            break
        time.sleep(0.05)
    instances = client.get_client_instances()
    assert instances, "service server never showed up"

    def send(line: str) -> str:
        ok, response = instances[0].call_with_response(CMD_METHOD, line.encode(), 3000)
        assert ok, "service call failed"
        return response.response.decode()

    return send


def test_status_reaches_ok(node):
    assert node.status == "ok"


def test_describe_lists_the_common_methods(call):
    reply = call("describe")
    assert reply.startswith("OK ")
    commands = json.loads(reply[3:])
    by_name = {c["name"]: c for c in commands}
    assert {"describe", "set_channel", "reconnect"} <= set(by_name)
    assert [a["name"] for a in by_name["set_channel"]["args"]] == ["channel", "name"]
    assert "args" not in by_name["describe"]


def test_unknown_method_is_an_error(call):
    assert call("set_voltage 12").startswith("ERR unknown method")


def test_wrong_argument_count_is_an_error(call):
    assert call("set_channel ch1").startswith("ERR set_channel expects")


def test_empty_command_is_an_error(call):
    assert call("   ").startswith("ERR")


def test_set_channel_renames_the_payload(node, call):
    received: list[dict] = []
    sub = ecal.Subscriber(f"{node.prefix}/ch1")
    sub.set_receive_callback(
        lambda _topic, _dt, data, *_: received.append(json.loads(data.buffer.decode()))
    )
    time.sleep(0.5)

    assert call("set_channel ch1 SoC_heatsink") == "OK ch1 = SoC_heatsink"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if any(m["name"] == "SoC_heatsink" for m in received):
            break
        time.sleep(0.05)

    named = [m for m in received if m["name"] == "SoC_heatsink"]
    assert named, f"no renamed payload arrived; got {received[-3:]}"
    sample = named[-1]
    assert sample["v"] == 25.0
    assert sample["unit"] == "°C"
    assert TIMESTAMP.match(sample["t"]), sample["t"]
    sub.remove_receive_callback()
