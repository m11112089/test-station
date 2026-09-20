"""COM port 掃描(README 3)。

未指定 ``--port`` 時 node 自己掃描所有 COM,以該裝置專屬的方式辨識。
**使用中的 port 直接跳過** —— 開啟失敗即視為使用中,不再嘗試。
因為掃描期間會暫時佔住別人的 port,node 必須依序啟動(實務上用 ``st launch``)。
"""

from __future__ import annotations

from typing import Callable, Iterator

import serial
from serial.tools import list_ports

Probe = Callable[[serial.Serial], bool]
"""拿到一個已開啟的 port,判斷它是不是這台裝置。探測動作必須盡量無害。"""


def available_ports() -> list[str]:
    """回傳系統上所有 COM,依名稱排序,讓掃描順序可預期。"""
    return sorted(p.device for p in list_ports.comports())


def scan(probe: Probe, *, baudrate: int, timeout: float,
         ports: list[str] | None = None) -> Iterator[tuple[str, bool, str]]:
    """依序試開每個 port 並探測,產出 ``(port, matched, note)``。

    呼叫端負責決定拿第一個 match 還是全部列出;note 用於印出掃描過程。
    """
    for port in ports if ports is not None else available_ports():
        try:
            handle = serial.Serial(port, baudrate=baudrate, timeout=timeout)
        except (serial.SerialException, OSError) as exc:
            yield port, False, f"skipped (in use or unavailable): {exc}"
            continue
        try:
            matched = probe(handle)
            yield port, matched, "match" if matched else "no match"
        except (serial.SerialException, OSError) as exc:
            yield port, False, f"probe failed: {exc}"
        finally:
            handle.close()


def find_port(probe: Probe, *, baudrate: int, timeout: float,
              log: Callable[[str], None] | None = None) -> str | None:
    """回傳第一個通過探測的 port;全部都不是則 None。"""
    for port, matched, note in scan(probe, baudrate=baudrate, timeout=timeout):
        if log:
            log(f"{port}: {note}")
        if matched:
            return port
    return None
