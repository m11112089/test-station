"""PFR-100L 的 SCPI 收發(Programming Manual p.70–124)。

本模組**不開 port**:``Scpi`` 拿一個已開啟、有 ``write`` / ``readline`` 的 handle
(``serial.Serial`` 或測試用的假裝置),所以 RS232 與 USB-CDC 走的是同一份程式碼 ——
USB-CDC 在 Windows 上就是一個虛擬 COM,差別只在 port 名稱,協定完全一樣。

訊息結尾是 LF(手冊 p.72 Message Terminator)。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

TERMINATOR = b"\n"

MODEL = "PFR-100L"

MAX_VOLTAGE = 50.0
MAX_CURRENT = 10.0
RATED_POWER = 100.0
"""PFR-100L 是 multi-range:0–50 V / 0–10 A,但兩者乘積受限於額定 100 W(手冊 p.9)。"""


class NoResponse(OSError):
    """查詢在 timeout 內沒有收到回應。

    繼承 ``OSError`` 是刻意的:對 node 而言它跟 USB 被拔一樣,都是「裝置讀寫失敗」,
    要進重連狀態機,而不是當成一個普通的命令錯誤。
    """


class ScpiError(Exception):
    """儀器收到命令但回報錯誤(``:SYSTem:ERRor?`` 不是 0)。命令沒有生效。"""


@dataclass(frozen=True)
class Identity:
    manufacturer: str
    model: str
    serial: str
    firmware: str

    def __str__(self) -> str:
        return f"{self.manufacturer} {self.model} SN {self.serial} FW {self.firmware}"


def parse_idn(text: str) -> Identity | None:
    """``*IDN?`` 的回應:``GW-INSTEK,PFR-100L,TW1234567,01.01.12345678``(手冊 p.44)。"""
    parts = [p.strip() for p in text.strip().split(",")]
    if len(parts) != 4:
        return None
    return Identity(*parts)


def is_pfr_100l(text: str) -> bool:
    """掃描辨識依據(README 3):``*IDN?`` 的回應含 ``PFR-100L``。"""
    return MODEL in text.upper()


def parse_number(text: str) -> float:
    """``+12.0000`` / ``12.000`` / ``1.2e+1`` → float。"""
    return float(text.strip())


def parse_pair(text: str) -> tuple[float, float]:
    """``:MEASure:ALL?`` 的回應 ``+12.0000,+1.5000`` → (V, A)(手冊 p.84)。"""
    volts, amps = text.strip().split(",")
    return parse_number(volts), parse_number(amps)


def parse_bool(text: str) -> bool:
    """``<bool>`` 回應:``0`` / ``1``,有些韌體回 ``OFF`` / ``ON``。"""
    value = text.strip().upper()
    if value in ("1", "ON"):
        return True
    if value in ("0", "OFF"):
        return False
    raise ValueError(f"not a boolean: {text!r}")


def parse_error(text: str) -> tuple[int, str]:
    """``:SYSTem:ERRor?`` 的回應 ``-100, "Command error"`` → (-100, "Command error")。

    錯誤碼 0 表示沒有錯誤。
    """
    code, _, message = text.strip().partition(",")
    return int(code), message.strip().strip('"')


def power_problem(volts: float, amps: float) -> str | None:
    """V 設定 × I 限流超過額定 100 W 就擋下(README 4)。

    **這道鎖是防手滑,不是安全機制** —— 安全靠儀器自己的 OVP/OCP。
    """
    watts = volts * amps
    if watts > RATED_POWER + 1e-9:
        return (f"power limit {RATED_POWER:.0f}W exceeded: "
                f"{volts:g}V x {amps:g}A = {watts:.1f}W")
    return None


class Scpi:
    """一條 SCPI 連線。所有收發都過同一把鎖(README 4:命令與輪詢必須序列化)。

    ``write`` / ``readline`` 丟出的 ``OSError``(含 ``serial.SerialException``)原樣往上拋,
    由 node 轉成 ``DeviceError``。
    """

    def __init__(self, handle) -> None:
        self._handle = handle
        self._lock = threading.Lock()

    def _write(self, command: str) -> None:
        self._handle.write(command.encode("ascii") + TERMINATOR)

    def _read(self, command: str) -> str:
        line = self._handle.readline()
        if not line.endswith(TERMINATOR):
            raise NoResponse(f"no response to {command!r}")
        return line.decode("ascii", errors="replace").strip()

    def query(self, command: str) -> str:
        """送一個查詢(結尾是 ``?``),回傳去掉結尾的回應。"""
        with self._lock:
            self._write(command)
            return self._read(command)

    def send(self, command: str) -> None:
        """送一個設定命令,並以 ``:SYSTem:ERRor?`` 確認它真的被接受。

        設定命令本身沒有回應,不查錯誤佇列就無從得知它有沒有生效;
        而 README 2.1 規定 ``ERR`` 必須表示什麼都沒發生,所以每一道都要確認。
        """
        with self._lock:
            self._write(command)
            self._write(":SYST:ERR?")
            code, message = parse_error(self._read(":SYST:ERR?"))
        if code != 0:
            raise ScpiError(f"{command!r} rejected: {code} {message}")

    def clear(self) -> None:
        """``*CLS``:清掉錯誤佇列,免得開機前留下的錯誤被算到第一道命令頭上。"""
        with self._lock:
            self._write("*CLS")
