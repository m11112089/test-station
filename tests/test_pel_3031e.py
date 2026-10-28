"""PEL-3031E node 的測試:模式切換、設定值、量測輪詢、USB 拔線進重連。

儀器用 ``FakePel`` 代替 —— 一個假的 ``serial.Serial``,依 PEL-3000E Programming Manual
V1.11 回應 SCPI。查詢設定值會帶單位(手冊範例 ``>1.0A``),OVP / UVP 出廠回 ``OFF``,
這兩件事都照手冊模擬,確保 node 不會因此連不上。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from teststation.core.scpi import parse_error, parse_number
from teststation.nodes.pel_3031e import node as pel_node
from teststation.nodes.pel_3031e.node import Pel3031eNode, is_pel_3031e, probe

IDN = "GW,PEL-3031E,GEP123456789,V1.10.0.0"


class FakePel:
    """一台假的 PEL-3031E。只實作 node 用得到的命令,其他一律推 -113 進錯誤佇列。"""

    LOW_RANGE_MAX_A = 6.12

    def __init__(self, idn=IDN, **_):
        self.idn = idn
        self.mode = "CC"
        self.load = False
        self.crange = "High"
        self.amps, self.volts, self.watts = 0.0, 153.0, 0.0
        self.source_volts = 12.0  # 接在負載上的電源
        self.errors: list[str] = []
        self.written: list[str] = []
        self._out = bytearray()
        self.unplugged = False
        self.silent = False
        self.closed = False

    def write(self, data: bytes) -> int:
        if self.unplugged:
            raise OSError("device disconnected")
        for line in data.decode("ascii").splitlines():
            self.written.append(line)
            reply = self._handle(line.strip())
            if reply is not None and not self.silent:
                self._out += reply.encode("ascii") + b"\n"
        return len(data)

    def readline(self) -> bytes:
        if self.unplugged:
            raise OSError("device disconnected")
        end = self._out.find(b"\n")
        if end < 0:
            return b""
        line = bytes(self._out[: end + 1])
        del self._out[: end + 1]
        return line

    def reset_input_buffer(self) -> None:
        self._out.clear()

    def close(self) -> None:
        self.closed = True

    def _drawn(self) -> float:
        if not self.load:
            return 0.0
        if self.mode == "CC":
            return self.amps
        if self.mode == "CP":
            return self.watts / self.source_volts
        return 1.0

    def _handle(self, line: str) -> str | None:
        head, _, arg = line.partition(" ")
        head = head.upper()
        queries = {
            "*IDN?": lambda: self.idn,
            ":SYST:ERR?": lambda: self.errors.pop(0) if self.errors else '+0, "No error."',
            ":MEAS:VOLT?": lambda: f"{self.source_volts:.5f}",
            ":MEAS:CURR?": lambda: f"{self._drawn():.5f}",
            ":MEAS:POW?": lambda: f"{self._drawn() * self.source_volts:.5f}",
            ":MODE?": lambda: self.mode,
            ":INP?": lambda: "1" if self.load else "0",
            ":CRAN?": lambda: self.crange,
            ":CURR:VA?": lambda: f"{self.amps}A",
            ":VOLT:VA?": lambda: f"{self.volts}V",
            ":POW:VA?": lambda: f"{self.watts}W",
            ":OCP?": lambda: "LIMIT, 63.000",
            ":OPP?": lambda: "LIMIT, 315.00",
            ":OVP?": lambda: "OFF",
            ":UVP?": lambda: "OFF",
        }
        if head in queries:
            return queries[head]()
        if head == ":VRAN?":
            # 實機(FW V1.03.002)在 CR 模式下不回話,只留一筆錯誤。
            if self.mode == "CR":
                self.errors.append("-221, \"Settings conflict\";':VRAN'")
                return None
            return "High"
        if head == "*CLS":
            self.errors.clear()
        elif head == ":INP":
            self.load = arg.upper() in ("ON", "1")
        elif head == ":MODE" and arg in ("CC", "CR", "CV", "CP", "CCCV", "CRCV", "CPCV"):
            if arg != self.mode:
                self.load = False  # 實機:真的換模式會把 load 關掉
            self.mode = arg
        elif head == ":CURR:VA":
            limit = self.LOW_RANGE_MAX_A if self.crange == "Low" else 61.2
            if float(arg) > limit:
                self.errors.append("-222, \"Data out of range\";':CURR:VA'")
            else:
                self.amps = float(arg)
                self.mode = "CC"  # 實機:設 CC 值會順便切到 CC
        elif head == ":VOLT:VA":
            self.volts = float(arg)
        elif head == ":POW:VA":
            self.watts = float(arg)
            self.mode = "CP"  # 實機:設 CP 值會順便切到 CP;設 CV 值則不會
        else:
            self.errors.append('-113, "Undefined header"')
        return None


@pytest.fixture
def connected(monkeypatch):
    """一個已經連上假 PEL 的 node。不起 eCAL,publish 收進 list。"""
    device = FakePel()
    monkeypatch.setattr(pel_node.serial, "Serial", lambda *a, **kw: device)
    node = Pel3031eNode("COM21", interval=0)
    published: list[tuple[str, float, str]] = []
    events: list[tuple[str, str]] = []
    node.publish = lambda channel, value, unit, when=None: published.append((channel, value, unit))
    node.activity = lambda kind, text, when=None: events.append((kind, text))
    node.open_device()
    return SimpleNamespace(node=node, device=device, published=published, events=events)


# ------------------------------------------------------------------ 辨識

def test_identify_only_pel_3031e():
    assert is_pel_3031e(IDN)
    assert not is_pel_3031e("GW,PEL-3032E,X,V1")
    assert not is_pel_3031e("GW-INSTEK,PFR-100L,TW1,01.00")


def test_probe():
    assert probe(FakePel())
    assert not probe(FakePel(idn="GW-INSTEK,PFR-100L,TW1,01.00"))
    silent = FakePel()
    silent.silent = True
    assert not probe(silent)


def test_numbers_with_unit_suffix():
    """手冊的查詢範例帶單位(``>1.0A``、``>15.0000``)。"""
    assert parse_number("1.0A") == 1.0
    assert parse_number(">15.0000") == 15.0
    assert parse_number("306.00W") == 306.0
    assert parse_number("+0.79860") == 0.7986
    with pytest.raises(ValueError):
        parse_number("OFF")


# ----------------------------------------------------------------- node

def test_connect_reads_state_without_changing_it(connected):
    written = connected.device.written
    assert not any(cmd.startswith((":INP ", ":MODE ", ":CURR:", ":VOLT:", ":POW:"))
                   and not cmd.endswith("?") for cmd in written)
    texts = [text for _, text in connected.events]
    assert any("PEL-3031E" in t and "via USB-CDC" in t for t in texts)
    state = next(t for t in texts if t.startswith("mode "))
    assert "mode CC, load OFF" in state
    assert "OVP OFF" in state and "OCP LIMIT, 63.000" in state


def test_connects_in_cr_mode(monkeypatch):
    """實機在 CR 模式下對 :VRAN? 不回話;node 不能因此連不上,也不能把那筆錯誤留給下一道命令。"""
    device = FakePel()
    device.mode = "CR"
    monkeypatch.setattr(pel_node.serial, "Serial", lambda *a, **kw: device)
    node = Pel3031eNode("COM3")
    events: list[str] = []
    node.activity = lambda kind, text, when=None: events.append(text)
    node.open_device()
    assert any(t.startswith("mode CR, load OFF") for t in events)
    assert ":VRAN?" not in device.written
    assert device.errors == []
    assert node.handle_line("set_current 0.5") == "OK 0.5000"


def test_cc_load_and_measurements(connected):
    node, device = connected.node, connected.device
    assert node.handle_line("set_mode cc") == "OK CC"
    assert node.handle_line("set_current 2.5") == "OK 2.5000"
    assert node.handle_line("load_on") == "OK load on"
    assert device.load
    node.poll()
    assert connected.published == [("voltage", 12.0, "V"), ("current", 2.5, "A"),
                                   ("power", 30.0, "W")]
    assert node.handle_line("load_off") == "OK load off"
    assert not device.load


def test_cp_mode(connected):
    node, device = connected.node, connected.device
    assert node.handle_line("set_mode CP") == "OK CP"
    assert node.handle_line("set_power 24") == "OK 24.00"
    assert node.handle_line("set_voltage 10") == "OK 10.0000"
    assert device.mode == "CP" and device.watts == 24.0 and device.volts == 10.0


def test_setting_a_value_switches_mode_like_the_real_unit(connected):
    """實機(FW V1.03.002)量到的行為:CC / CP 的值一設下去就切到該模式,CV 不會。"""
    node, device = connected.node, connected.device
    device.mode = "CR"
    node.handle_line("set_current 0.5")
    assert device.mode == "CC"
    node.handle_line("set_voltage 16")
    assert device.mode == "CC"
    node.handle_line("set_power 1.5")
    assert device.mode == "CP"


def test_mode_change_under_load_says_the_load_went_off(connected):
    """實機:拉載中真的換模式,儀器會自己關 load;設值順便換模式則不會。"""
    node, device = connected.node, connected.device
    node.handle_line("set_current 0.5")
    node.handle_line("load_on")
    assert node.handle_line("set_power 2") == "OK 2.00"
    assert device.mode == "CP" and device.load
    assert node.handle_line("set_mode CP") == "OK CP"
    assert device.load
    reply = node.handle_line("set_mode CV")
    assert reply == "OK CV (load turned off by the mode change; load_on to resume)"
    assert not device.load


def test_error_message_includes_the_failing_command():
    """實機的 :SYST:ERR? 在訊息後面附上出錯的命令。"""
    assert parse_error("-222, \"Data out of range\";':CURR:VA'") == (
        -222, "Data out of range (:CURR:VA)")
    assert parse_error('+0, "No error."') == (0, "No error.")


def test_unsupported_mode_sends_nothing(connected):
    before = list(connected.device.written)
    reply = connected.node.handle_line("set_mode CR")
    assert reply.startswith("ERR mode must be one of CC/CV/CP")
    assert connected.device.written == before


def test_instrument_rejection_is_err_and_nothing_changes(connected):
    """Low range 最多 6.12 A:儀器拒收時回 ERR,設定值不變。"""
    device = connected.device
    device.crange = "Low"
    reply = connected.node.handle_line("set_current 10")
    assert reply == "ERR ':CURR:VA 10.0000' rejected: -222 Data out of range (:CURR:VA)"
    assert device.amps == 0.0


def test_describe_range_is_enforced(connected):
    assert connected.node.handle_line("set_current 70").startswith("ERR a above max")
    assert connected.node.handle_line("set_power 301").startswith("ERR w above max")


def test_settings_are_marked_and_actions_are_not(connected):
    commands = {c.name: c for c in connected.node.commands()}
    assert all(commands[n].setting for n in ("set_mode", "set_current", "set_voltage", "set_power"))
    assert not commands["load_on"].setting
    assert commands["load_off"].style == "danger"


def test_unplugged_usb_is_a_device_error(connected, monkeypatch):
    node = connected.node
    entered: list[str] = []
    monkeypatch.setattr(node, "_enter_reconnecting", entered.append)
    connected.device.unplugged = True
    assert node.handle_line("load_off").startswith("ERR device error")
    assert entered
    with pytest.raises(pel_node.DeviceError):
        node.poll()


def test_garbled_measurement_is_a_device_error(connected):
    connected.device._handle = lambda line: "garbage" if line.endswith("?") else None
    with pytest.raises(pel_node.DeviceError, match="unexpected reply"):
        connected.node.poll()


def test_wrong_device_is_rejected(monkeypatch):
    monkeypatch.setattr(pel_node.serial, "Serial",
                        lambda *a, **kw: FakePel(idn="GW,PEL-3032E,X,V1"))
    node = Pel3031eNode("COM21")
    with pytest.raises(pel_node.DeviceError, match="not a PEL-3031E"):
        node.open_device()


def test_silent_port_hints_at_interface_setting(monkeypatch):
    device = FakePel()
    device.silent = True
    monkeypatch.setattr(pel_node.serial, "Serial", lambda *a, **kw: device)
    node = Pel3031eNode("COM21")
    with pytest.raises(pel_node.DeviceError, match=r"Interface \[F3\] = USB"):
        node.open_device()
    assert device.closed


def test_commands_before_connect_are_errors():
    node = Pel3031eNode("COM21")
    assert node.handle_line("load_on") == "ERR not connected"
