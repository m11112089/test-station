"""PEL-3031E node —— 直流電子負載(GW Instek),0–150 V / 0–60 A / 300 W。

遠端介面只有 **USB-CDC(後面板 USB Type B)**,GPIB 是選配、本站不支援;
這台**沒有 RS232**。面板 ``Shift + Help(Utility)> Interface[F3]`` 設成 ``USB``。
USB 不看鮑率,所以沒有 ``--baud``。

命令依 PEL-3000E Programming Manual V1.11(SCPI,結尾 LF)。
一個 process 對應一台機器、一個 COM。node 連上時**只查詢**機器現況,不改動任何設定 ——
保護值(OCP / OPP / OVP / UVP)該設多少由現場決定,程式不發表意見。

Topic:``PEL-3031E/<port>/voltage``、``current``、``power``(量測值,不是設定值)。
"""

from __future__ import annotations

import argparse
import sys
import time

import serial

from ...core.describe import Arg, Command
from ...core.node import DeviceError, Node
from ...core.scan import find_port
from ...core.scpi import NoResponse, Scpi, parse_idn, parse_number, probe_idn

MODEL = "PEL-3031E"

MAX_VOLTAGE = 150.0
MAX_CURRENT = 60.0
MAX_POWER = 300.0
"""PEL-3031E 的額定(High range)。儀器實際可設到 102%(61.2 A / 153 V / 306 W),
這裡只放到額定;Low range(6 A / 15 V / 30 W)的上限由儀器自己擋,會回 ``ERR ... rejected``。
"""

MODES = ("CC", "CV", "CP")
"""開放的操作模式。

CR 刻意不開:CR 的單位出廠是 mS(電導),``:RESistance`` 只在單位設成 OHM 時有效
(手冊 p.81),兩者混用很容易設錯值。要用再加。
"""

BAUDRATE = 115200
"""USB-CDC 不看鮑率;pyserial 一定要給一個值,給什麼都可以。"""

READ_TIMEOUT = 1.0
"""一道查詢最多等多久。逾時 = 裝置讀寫失敗,進重連。"""

DEFAULT_INTERVAL = 1.0
"""量測輪詢間隔(秒)。"""

INTERFACE_HINT = ("check the panel: Shift + Help (Utility) > Interface [F3] = USB, "
                  "and that the rear USB Type B cable is in")


def is_pel_3031e(text: str) -> bool:
    """``*IDN?`` 的回應含 ``PEL-3031E``(手冊 p.7:``GW,PEL-303XE,<序號>,<韌體>``)。"""
    return MODEL in text.upper()


class Pel3031eNode(Node):
    device = MODEL

    def __init__(self, port: str, *, interval: float = DEFAULT_INTERVAL) -> None:
        super().__init__(port)
        self._interval = interval
        self._serial: serial.Serial | None = None
        self._scpi: Scpi | None = None
        self._next_sample = 0.0

    # ------------------------------------------------------------------ 命令

    def commands(self) -> list[Command]:
        return [
            Command("load_on", "開始拉載", self._cmd_load_on),
            Command("load_off", "停止拉載", self._cmd_load_off, style="danger"),
            Command("set_mode", "操作模式 CC/CV/CP", self._cmd_set_mode, setting=True,
                    args=(Arg("mode"),)),
            Command("set_current", "CC 電流", self._cmd_set_current, setting=True,
                    args=(Arg("a", unit="A", min=0, max=MAX_CURRENT),)),
            Command("set_voltage", "CV 電壓", self._cmd_set_voltage, setting=True,
                    args=(Arg("v", unit="V", min=0, max=MAX_VOLTAGE),)),
            Command("set_power", "CP 功率", self._cmd_set_power, setting=True,
                    args=(Arg("w", unit="W", min=0, max=MAX_POWER),)),
        ]

    def _io(self, action):
        """跑一次收發;序列埠錯誤(含沒回應)轉成 DeviceError,讓 core 進重連。"""
        if self._scpi is None:
            raise RuntimeError("not connected")
        try:
            return action(self._scpi)
        except OSError as exc:
            raise DeviceError(str(exc)) from exc

    def _cmd_load_on(self) -> str:
        self._io(lambda s: s.send(":INP ON"))
        return "load on"

    def _cmd_load_off(self) -> str:
        self._io(lambda s: s.send(":INP OFF"))
        return "load off"

    def _cmd_set_mode(self, mode: str) -> str:
        mode = mode.upper()
        if mode not in MODES:
            raise ValueError(f"mode must be one of {'/'.join(MODES)}, got {mode!r}")
        # 實機(FW V1.03.002):真的換模式時儀器會自己把 load 關掉(設值順便換模式則不會)。
        # 回覆要講出來,不然看起來像是還在拉載。
        was_on = self._io(lambda s: s.query(":INP?")) in ("1", "ON")
        self._io(lambda s: s.send(f":MODE {mode}"))
        if was_on and self._io(lambda s: s.query(":INP?")) not in ("1", "ON"):
            return f"{mode} (load turned off by the mode change; load_on to resume)"
        return mode

    # [:VA] 只有在 static 模式才能省略(手冊 p.69、p.93),所以一律寫出來。
    # 實機(FW V1.03.002):設 CC 值會順便切到 CC、設 CP 值會切到 CP,設 CV 值不切。手冊沒寫。
    def _cmd_set_current(self, raw: str) -> str:
        amps = float(raw)
        self._io(lambda s: s.send(f":CURR:VA {amps:.4f}"))
        return f"{amps:.4f}"

    def _cmd_set_voltage(self, raw: str) -> str:
        volts = float(raw)
        self._io(lambda s: s.send(f":VOLT:VA {volts:.4f}"))
        return f"{volts:.4f}"

    def _cmd_set_power(self, raw: str) -> str:
        watts = float(raw)
        self._io(lambda s: s.send(f":POW:VA {watts:.2f}"))
        return f"{watts:.2f}"

    # ------------------------------------------------------------------- I/O

    def open_device(self) -> None:
        try:
            self._serial = serial.Serial(self.port, baudrate=BAUDRATE, timeout=READ_TIMEOUT)
            self._serial.reset_input_buffer()
            scpi = Scpi(self._serial)
            idn = scpi.query("*IDN?")
            if not is_pel_3031e(idn):
                raise DeviceError(f"{self.port} is not a {self.device}: {idn!r}")
            state = self._read_state(scpi)
            # 查完再清:狀態查詢在某些模式下會留下錯誤(見 _read_state),
            # 不清掉的話會被算到第一道設定命令頭上。
            scpi.clear()
        except DeviceError:
            self.close_device()
            raise
        except OSError as exc:
            self.close_device()
            raise DeviceError(f"{exc}; {INTERFACE_HINT}") from exc
        self._scpi = scpi
        self._next_sample = 0.0
        self.activity("SYS", f"{parse_idn(idn) or idn} via USB-CDC")
        self.activity("SYS", state)

    @staticmethod
    def _read_state(scpi: Scpi) -> str:
        """連上時查詢現況,只讀不寫。

        回應原樣記下來,不解析:OVP / UVP 出廠是 ``OFF``,OCP / OPP 是 ``LIMIT, 63.000``
        這種字串,硬要轉成數字反而會在格式稍有不同時讓連線失敗。

        **不查 ``:VRANge?``**:實機(FW V1.03.002)在 CR 模式下對它完全不回話,
        只在錯誤佇列留下 ``-221, "Settings conflict";':VRAN'``。其他項目沒回應時記成
        ``n/a`` —— 這只是記錄,不值得為它讓整個連線失敗。``*IDN?`` 已經證明線是通的。
        """
        def ask(command: str) -> str:
            try:
                return scpi.query(command)
            except NoResponse:
                return "n/a"

        return (
            f"mode {ask(':MODE?')}, load {'ON' if ask(':INP?') in ('1', 'ON') else 'OFF'}, "
            f"range I {ask(':CRAN?')}, "
            f"set CC {ask(':CURR:VA?')} / CV {ask(':VOLT:VA?')} / CP {ask(':POW:VA?')}, "
            f"OCP {ask(':OCP?')}, OPP {ask(':OPP?')}, OVP {ask(':OVP?')}, UVP {ask(':UVP?')}"
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
        try:
            volts = self._io(lambda s: parse_number(s.query(":MEAS:VOLT?")))
            amps = self._io(lambda s: parse_number(s.query(":MEAS:CURR?")))
            watts = self._io(lambda s: parse_number(s.query(":MEAS:POW?")))
        except ValueError as exc:
            # 回應格式不對多半是收發錯位,重開連線把緩衝區清乾淨最保險。
            raise DeviceError(f"unexpected reply: {exc}") from exc
        self.publish("voltage", volts, "V")
        self.publish("current", amps, "A")
        self.publish("power", watts, "W")


def probe(handle: serial.Serial) -> bool:
    """掃描用的探測:送 ``*IDN?``,回應含 ``PEL-3031E`` 就算數。"""
    return probe_idn(handle, MODEL)


def resolve_port(port: str | None) -> str:
    if port:
        return port
    print(f"scanning for {MODEL} (*IDN?)...")
    found = find_port(probe, baudrate=BAUDRATE, timeout=READ_TIMEOUT,
                      log=lambda line: print(f"  {line}"))
    if found is None:
        raise SystemExit(f"no {MODEL} found; {INTERFACE_HINT}, or try --port")
    print(f"found on {found}")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pel_3031e", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", help="指定 COM;不給則自動掃描")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help="量測輪詢間隔(秒)")
    args = parser.parse_args(argv)

    try:
        port = resolve_port(args.port)
    except (serial.SerialException, OSError) as exc:
        print(f"serial error: {exc}", file=sys.stderr)
        return 1

    node = Pel3031eNode(port, interval=args.interval)
    print(f"{node.prefix} starting")
    try:
        node.run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
