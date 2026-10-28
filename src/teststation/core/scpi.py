"""SCPI over COM 的共通部分(GW Instek 的 PFR-100L、PEL-3031E 都用這套)。

本模組**不開 port**:``Scpi`` 拿一個已開啟、有 ``write`` / ``readline`` 的 handle
(``serial.Serial`` 或測試用的假裝置)。RS232 與 USB-CDC 對它而言沒有差別 ——
USB-CDC 在 Windows 上就是一個虛擬 COM。

訊息結尾是 LF。各機型自己的命令、範圍與型號辨識放在各 node 的模組裡。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

TERMINATOR = b"\n"

_NUMBER = re.compile(r"^\s*>?\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*[A-Za-z%]*\s*$")


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
    """``*IDN?`` 的回應:``<廠商>,<型號>,<序號>,<韌體>``。"""
    parts = [p.strip() for p in text.strip().split(",")]
    if len(parts) != 4:
        return None
    return Identity(*parts)


def parse_number(text: str) -> float:
    """``+12.0000`` / ``12.000`` / ``1.2e+1`` / ``1.0A`` → float。

    有些機型(PEL-3000E)查詢設定值時會帶單位(手冊範例 ``>1.0A``),一併吃掉。
    """
    match = _NUMBER.match(text)
    if match is None:
        raise ValueError(f"not a number: {text!r}")
    return float(match.group(1))


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

    錯誤碼 0(含 ``+0``)表示沒有錯誤。

    PEL-3031E 實機(FW V1.03.002)會在訊息後面附上出錯的命令:
    ``-222, "Data out of range";':CURR:VA'`` → (-222, "Data out of range (:CURR:VA)")。
    """
    code, _, rest = text.strip().partition(",")
    message, _, context = rest.strip().partition(";")
    message = message.strip().strip('"')
    context = context.strip().strip("'")
    return int(code), f"{message} ({context})" if context else message


def probe_idn(handle, model: str) -> bool:
    """掃描用的探測:送 ``*IDN?``,回應含 ``model`` 就算數(README 3)。

    ``*IDN?`` 是 IEEE 488.2 的標準查詢,打進別台儀器頂多回一行身分,
    打進 DUT console 也只是一行看不懂的字。
    """
    handle.reset_input_buffer()
    handle.write(b"*IDN?" + TERMINATOR)
    reply = handle.readline().decode("ascii", errors="replace")
    return model.upper() in reply.upper()


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
