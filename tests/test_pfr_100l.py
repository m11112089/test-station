"""PFR-100L node 的測試:SCPI 解析、100 W 鎖、RS232 / USB-CDC 兩種接法。

儀器用 ``FakePfr`` 代替 —— 它是一個假的 ``serial.Serial``,依手冊回應 SCPI。
RS232 與 USB-CDC 對 node 而言都是 COM port,差別只在 port 的 USB 身分與鮑率,
所以兩種接法跑的是同一組測試,只換 ``list_ports`` 回報的裝置資訊。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from teststation.nodes.pfr_100l import node as pfr_node
from teststation.nodes.pfr_100l.node import GW_INSTEK_VID, Pfr100lNode, probe, transport
from teststation.core.scpi import NoResponse, Scpi, ScpiError, parse_error, parse_idn
from teststation.nodes.pfr_100l.scpi import is_pfr_100l, parse_pair, power_problem

IDN = "GW-INSTEK,PFR-100L,TW1234567,01.01.12345678"


class FakePfr:
    """一台假的 PFR-100L。只實作 node 用得到的命令,其他一律推 -113 進錯誤佇列。"""

    def __init__(self, port="COM20", baudrate=115200, idn=IDN, **_):
        self.port = port
        self.baudrate = baudrate
        self.idn = idn
        self.volts, self.amps = 5.0, 1.0
        self.output = False
        self.errors: list[str] = []
        self.written: list[str] = []
        self._out = bytearray()
        self.unplugged = False
        self.silent = False
        self.closed = False

    # serial.Serial 的介面 ------------------------------------------------
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
            return b""  # timeout
        line = bytes(self._out[: end + 1])
        del self._out[: end + 1]
        return line

    def reset_input_buffer(self) -> None:
        self._out.clear()

    def close(self) -> None:
        self.closed = True

    # SCPI ---------------------------------------------------------------
    def _handle(self, line: str) -> str | None:
        head, _, arg = line.partition(" ")
        head = head.upper()
        if head == "*IDN?":
            return self.idn
        if head == "*CLS":
            self.errors.clear()
            return None
        if head == ":SYST:ERR?":
            return self.errors.pop(0) if self.errors else '0, "No error"'
        if head == ":MEAS:ALL?":
            amps = self.amps if self.output else 0.0
            volts = self.volts if self.output else 0.0
            return f"{volts:+.4f},{amps * 0.5:+.4f}"
        if head == ":MEAS:POW?":
            return f"{(self.volts * self.amps * 0.5) if self.output else 0.0:+.4f}"
        if head == ":VOLT?":
            return f"{self.volts:.3f}"
        if head == ":CURR?":
            return f"{self.amps:.3f}"
        if head == ":OUTP?":
            return "1" if self.output else "0"
        if head == ":VOLT:PROT?":
            return "55.000"
        if head == ":CURR:PROT?":
            return "11.000"
        if head == ":VOLT":
            self.volts = float(arg)
            return None
        if head == ":CURR":
            self.amps = float(arg)
            return None
        if head == ":OUTP":
            self.output = arg.upper() in ("ON", "1")
            return None
        if head == ":OUTP:PROT:CLE":
            return None
        self.errors.append('-113, "Undefined header"')
        return None


def _ports(*entries):
    return lambda: [SimpleNamespace(device=d, vid=v) for d, v in entries]


@pytest.fixture
def usb(monkeypatch):
    """COM20 是 PFR 自己的 USB-CDC(後面板 Type B)。"""
    monkeypatch.setattr(pfr_node.list_ports, "comports", _ports(("COM20", GW_INSTEK_VID)))


@pytest.fixture
def rs232(monkeypatch):
    """COM20 是 USB 轉 RS232 線(FTDI),後面接 Remote-IN。"""
    monkeypatch.setattr(pfr_node.list_ports, "comports", _ports(("COM20", 0x0403)))


@pytest.fixture(params=["usb", "rs232"])
def connected(request, monkeypatch):
    """一個已經連上假 PFR 的 node,兩種接法各跑一次。不起 eCAL,publish 收進 list。"""
    request.getfixturevalue(request.param)
    device = FakePfr()
    monkeypatch.setattr(pfr_node.serial, "Serial", lambda *a, **kw: device)
    node = Pfr100lNode("COM20", interval=0)
    published: list[tuple[str, float, str]] = []
    events: list[tuple[str, str]] = []
    node.publish = lambda channel, value, unit, when=None: published.append((channel, value, unit))
    node.activity = lambda kind, text, when=None: events.append((kind, text))
    node.open_device()
    return SimpleNamespace(node=node, device=device, published=published,
                           events=events, kind=request.param)


# --------------------------------------------------------------- SCPI 解析

def test_parse_idn():
    idn = parse_idn(IDN)
    assert idn.model == "PFR-100L"
    assert idn.serial == "TW1234567"
    assert parse_idn("garbage") is None


def test_identify_only_pfr_100l():
    assert is_pfr_100l(IDN + "\n")
    assert not is_pfr_100l("GW-INSTEK,PFR-100M,TW1,01.00")
    assert not is_pfr_100l("")


def test_parse_measure_all():
    assert parse_pair("+12.0000,+1.5000") == (12.0, 1.5)


def test_parse_error():
    assert parse_error('0, "No error"') == (0, "No error")
    assert parse_error('-100, "Command error"') == (-100, "Command error")


def test_power_limit():
    assert power_problem(50, 2) is None
    assert power_problem(10, 10) is None
    assert "100W" in power_problem(12, 10)


def test_scpi_send_raises_when_instrument_rejects():
    scpi = Scpi(FakePfr())
    with pytest.raises(ScpiError, match="-113"):
        scpi.send(":BOGUS 1")


def test_scpi_query_timeout_is_an_os_error():
    device = FakePfr()
    device.silent = True
    with pytest.raises(NoResponse):
        Scpi(device).query("*IDN?")
    assert issubclass(NoResponse, OSError)


# ------------------------------------------------------------- 接法辨識

def test_transport_usb(usb):
    assert transport("COM20") == "USB-CDC"


def test_transport_rs232_through_usb_adapter(rs232):
    """USB 轉 RS232 線也有 VID,不能被當成 PFR 的 USB-CDC。"""
    assert transport("COM20") == "RS232"


def test_probe_matches_pfr_only():
    assert probe(FakePfr())
    assert not probe(FakePfr(idn="GW-INSTEK,GPP-4323,X,1"))
    silent = FakePfr()
    silent.silent = True
    assert not probe(silent)


# ------------------------------------------------- node(USB / RS232 各一次)

def test_connect_reads_state_without_changing_it(connected):
    device = connected.device
    assert connected.node._volts == 5.0 and connected.node._amps == 1.0
    assert not any(cmd.startswith((":VOLT ", ":CURR ", ":OUTP ")) for cmd in device.written)
    expected = "USB-CDC" if connected.kind == "usb" else "RS232"
    assert any(f"via {expected}" in text for _, text in connected.events)
    assert any("OVP 55 V" in text for _, text in connected.events)


def test_poll_publishes_measurements(connected):
    node, device = connected.node, connected.device
    assert node.handle_line("set_voltage 12") == "OK 12.000"
    assert node.handle_line("set_current 2") == "OK 2.000"
    assert node.handle_line("output_on") == "OK output on"
    assert device.output
    node.poll()
    assert connected.published == [("voltage", 12.0, "V"), ("current", 1.0, "A"),
                                   ("power", 12.0, "W")]


def test_power_lock_blocks_and_sends_nothing(connected):
    node, device = connected.node, connected.device
    assert node.handle_line("set_current 10").startswith("OK")
    before = list(device.written)
    reply = node.handle_line("set_voltage 12")
    assert reply.startswith("ERR power limit 100W exceeded")
    assert device.written == before
    assert device.volts == 5.0


def test_describe_range_is_enforced(connected):
    assert connected.node.handle_line("set_voltage 60").startswith("ERR v above max")


def test_output_off_and_clear_protection(connected):
    node = connected.node
    node.handle_line("output_on")
    assert node.handle_line("output_off") == "OK output off"
    assert not connected.device.output
    assert node.handle_line("clear_protection") == "OK protection cleared"


def test_settings_are_marked_and_actions_are_not(connected):
    commands = {c.name: c for c in connected.node.commands()}
    assert commands["set_voltage"].setting and commands["set_current"].setting
    assert not commands["output_on"].setting


def test_unplugged_cable_is_a_device_error(connected, monkeypatch):
    """USB 被拔 → DeviceError → core 進重連。"""
    node = connected.node
    entered: list[str] = []
    monkeypatch.setattr(node, "_enter_reconnecting", entered.append)
    connected.device.unplugged = True
    assert node.handle_line("output_off").startswith("ERR device error")
    assert entered
    with pytest.raises(pfr_node.DeviceError):
        node.poll()


def test_wrong_device_on_port_is_rejected(monkeypatch, usb):
    monkeypatch.setattr(pfr_node.serial, "Serial",
                        lambda *a, **kw: FakePfr(idn="GW-INSTEK,PFR-100M,X,1"))
    node = Pfr100lNode("COM20")
    node.activity = lambda *a, **kw: None
    with pytest.raises(pfr_node.DeviceError, match="not a PFR-100L"):
        node.open_device()


def test_silent_port_hints_at_f29(monkeypatch, usb):
    """F-29 沒切到對的介面時,儀器不回話;錯誤訊息要告訴人去看 F-29。"""
    device = FakePfr()
    device.silent = True
    monkeypatch.setattr(pfr_node.serial, "Serial", lambda *a, **kw: device)
    node = Pfr100lNode("COM20")
    with pytest.raises(pfr_node.DeviceError, match="F-29 = 3"):
        node.open_device()
    assert device.closed


def test_commands_before_connect_are_errors(usb):
    node = Pfr100lNode("COM20")
    assert node.handle_line("output_on") == "ERR not connected"
    assert node.handle_line("set_voltage 12") == "ERR not connected"
