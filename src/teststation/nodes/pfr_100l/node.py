"""PFR-100L node —— 可程式化電源(GW Instek),0–50 V / 0–10 A / 100 W。

兩種接法都是 COM port,走同一份程式碼(``scpi.py``):

* **RS232**:後面板 Remote-IN(RJ-45)+ PSU-232 線,面板 ``F-29 = 1``。
  鮑率要跟 ``F-71`` 一致(出廠 115200 8N1),用 ``--baud`` 指定。
* **USB-CDC**:後面板 USB Type B,面板 ``F-29 = 3``。Windows 會多出一個虛擬 COM,
  ``--baud`` 對它沒有意義(USB 不看鮑率),給什麼都可以。

一個 process 對應一台機器、一個 COM,不做 multi-drop。
node 連上時**只查詢**機器現況,不改動任何設定(README 4)。

Topic:``PFR-100L/<port>/voltage``、``current``、``power``(量測值,不是設定值)。
"""

from __future__ import annotations

import argparse
import sys
import time

import serial
from serial.tools import list_ports

from ...core.describe import Arg, Command
from ...core.node import DeviceError, Node
from ...core.scan import find_port
from ...core.scpi import Scpi, parse_bool, parse_idn, parse_number, probe_idn
from .scpi import MAX_CURRENT, MAX_VOLTAGE, MODEL, is_pfr_100l, parse_pair, power_problem

DEFAULT_BAUDRATE = 115200
"""``F-71`` 的出廠值(手冊 p.146:7 = 115200),8N1。USB-CDC 不看這個值。"""

READ_TIMEOUT = 1.0
"""一道查詢最多等多久。逾時 = 裝置讀寫失敗,進重連。"""

DEFAULT_INTERVAL = 1.0
"""量測輪詢間隔(秒)。README 4:取樣上限約 10–20 Hz,平常 1 Hz 就夠。"""

GW_INSTEK_VID = 0x2184
"""GW Instek 的 USB vendor ID。只用來在 log 上標示接法,**不拿來辨識裝置** ——
辨識一律看 ``*IDN?``,USB 轉 RS232 的線也有 VID,不能只看「是不是 USB」。
"""

INTERFACE_HINT = ("check the panel: F-29 = 3 for USB (rear Type B), "
                  "or F-29 = 1 for RS232 with F-71 matching --baud")


def transport(port: str) -> str:
    """這個 COM 是 PFR 自己的 USB-CDC,還是 RS232(含 USB 轉 RS232 線)。"""
    for info in list_ports.comports():
        if info.device == port:
            return "USB-CDC" if info.vid == GW_INSTEK_VID else "RS232"
    return "RS232"


class Pfr100lNode(Node):
    device = "PFR-100L"

    def __init__(self, port: str, *, baudrate: int = DEFAULT_BAUDRATE,
                 interval: float = DEFAULT_INTERVAL) -> None:
        super().__init__(port)
        self._baudrate = baudrate
        self._interval = interval
        self._serial: serial.Serial | None = None
        self._scpi: Scpi | None = None
        # 機器上目前的設定值,連上時從機器讀回來;100 W 鎖要用。
        self._volts: float | None = None
        self._amps: float | None = None
        self._next_sample = 0.0

    # ------------------------------------------------------------------ 命令

    def commands(self) -> list[Command]:
        return [
            Command("output_on", "開始送電", self._cmd_output_on),
            Command("output_off", "停止送電", self._cmd_output_off, style="danger"),
            Command("set_voltage", "設定電壓", self._cmd_set_voltage, setting=True,
                    args=(Arg("v", unit="V", min=0, max=MAX_VOLTAGE),)),
            Command("set_current", "設定限流", self._cmd_set_current, setting=True,
                    args=(Arg("a", unit="A", min=0, max=MAX_CURRENT),)),
            Command("clear_protection", "解除保護", self._cmd_clear_protection),
        ]

    def _link(self) -> Scpi:
        if self._scpi is None:
            raise RuntimeError("not connected")
        return self._scpi

    def _io(self, action):
        """跑一次收發;序列埠錯誤(含沒回應)轉成 DeviceError,讓 core 進重連。"""
        try:
            return action(self._link())
        except OSError as exc:
            raise DeviceError(str(exc)) from exc

    def _cmd_output_on(self) -> str:
        self._io(lambda s: s.send(":OUTP ON"))
        return "output on"

    def _cmd_output_off(self) -> str:
        self._io(lambda s: s.send(":OUTP OFF"))
        return "output off"

    def _cmd_set_voltage(self, raw: str) -> str:
        volts = float(raw)
        self._check_power(volts, self._amps)
        self._io(lambda s: s.send(f":VOLT {volts:.3f}"))
        self._volts = volts
        return f"{volts:.3f}"

    def _cmd_set_current(self, raw: str) -> str:
        amps = float(raw)
        self._check_power(self._volts, amps)
        self._io(lambda s: s.send(f":CURR {amps:.3f}"))
        self._amps = amps
        return f"{amps:.3f}"

    def _cmd_clear_protection(self) -> str:
        self._io(lambda s: s.send(":OUTP:PROT:CLE"))
        return "protection cleared"

    def _check_power(self, volts: float | None, amps: float | None) -> None:
        if volts is None or amps is None:
            raise RuntimeError("not connected")
        problem = power_problem(volts, amps)
        if problem:
            raise ValueError(problem)

    # ------------------------------------------------------------------- I/O

    def open_device(self) -> None:
        try:
            self._serial = serial.Serial(
                self.port, baudrate=self._baudrate, bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                timeout=READ_TIMEOUT,
            )
            self._serial.reset_input_buffer()
            scpi = Scpi(self._serial)
            idn = scpi.query("*IDN?")
            if not is_pfr_100l(idn):
                raise DeviceError(f"{self.port} is not a {self.device}: {idn!r}")
            scpi.clear()
            self._read_state(scpi)
        except DeviceError:
            self.close_device()
            raise
        except (OSError, ValueError) as exc:
            self.close_device()
            raise DeviceError(f"{exc}; {INTERFACE_HINT}") from exc
        self._scpi = scpi
        self._next_sample = 0.0
        self.activity("SYS", f"{parse_idn(idn) or idn} via {transport(self.port)}")

    def _read_state(self, scpi: Scpi) -> None:
        """連上時查詢現況並記到 activity。只讀不寫(README 4)。"""
        self._volts = parse_number(scpi.query(":VOLT?"))
        self._amps = parse_number(scpi.query(":CURR?"))
        output = parse_bool(scpi.query(":OUTP?"))
        ovp = parse_number(scpi.query(":VOLT:PROT?"))
        ocp = parse_number(scpi.query(":CURR:PROT?"))
        self.activity(
            "SYS",
            f"set {self._volts:g} V / {self._amps:g} A, output {'ON' if output else 'OFF'}, "
            f"OVP {ovp:g} V, OCP {ocp:g} A",
        )

    def close_device(self) -> None:
        self._scpi = None
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def poll(self) -> None:
        wait = self._next_sample - time.monotonic()
        if wait > 0:
            # 分段睡,讓主迴圈能及時反應停止;命令走 service 執行緒,不受影響。
            time.sleep(min(wait, 0.5))
            return
        self._next_sample = time.monotonic() + self._interval
        volts, amps = self._io(lambda s: parse_pair(s.query(":MEAS:ALL?")))
        watts = self._io(lambda s: parse_number(s.query(":MEAS:POW?")))
        self.publish("voltage", volts, "V")
        self.publish("current", amps, "A")
        self.publish("power", watts, "W")


def probe(handle: serial.Serial) -> bool:
    """掃描用的探測:送 ``*IDN?``,回應含 ``PFR-100L`` 就算數。"""
    return probe_idn(handle, MODEL)


def resolve_port(port: str | None, baudrate: int) -> str:
    if port:
        return port
    print(f"scanning for {Pfr100lNode.device} (*IDN? at {baudrate} baud)...")
    found = find_port(probe, baudrate=baudrate, timeout=READ_TIMEOUT,
                      log=lambda line: print(f"  {line}"))
    if found is None:
        raise SystemExit(f"no {Pfr100lNode.device} found; {INTERFACE_HINT}, or try --port")
    print(f"found on {found} ({transport(found)})")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pfr_100l", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", help="指定 COM;不給則自動掃描")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUDRATE,
                        help="RS232 鮑率,要跟面板 F-71 一致;USB-CDC 不看這個值")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help="量測輪詢間隔(秒)")
    args = parser.parse_args(argv)

    try:
        port = resolve_port(args.port, args.baud)
    except (serial.SerialException, OSError) as exc:
        print(f"serial error: {exc}", file=sys.stderr)
        return 1

    node = Pfr100lNode(port, baudrate=args.baud, interval=args.interval)
    print(f"{node.prefix} starting")
    try:
        node.run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
