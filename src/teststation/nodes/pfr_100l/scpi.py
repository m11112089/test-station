"""PFR-100L 專屬的 SCPI 部分(Programming Manual p.70–124)。

連線、錯誤確認、數值解析這些各機型共通的東西在 ``core/scpi.py``;
這裡只放 PFR-100L 的型號、範圍、100 W 鎖與 ``:MEASure:ALL?`` 的格式。
"""

from __future__ import annotations

from ...core.scpi import parse_number

MODEL = "PFR-100L"

MAX_VOLTAGE = 50.0
MAX_CURRENT = 10.0
RATED_POWER = 100.0
"""PFR-100L 是 multi-range:0–50 V / 0–10 A,但兩者乘積受限於額定 100 W(手冊 p.9)。"""


def is_pfr_100l(text: str) -> bool:
    """掃描辨識依據(README 3):``*IDN?`` 的回應含 ``PFR-100L``。

    回應格式 ``GW-INSTEK,PFR-100L,TW1234567,01.01.12345678``(手冊 p.44)。
    """
    return MODEL in text.upper()


def parse_pair(text: str) -> tuple[float, float]:
    """``:MEASure:ALL?`` 的回應 ``+12.0000,+1.5000`` → (V, A)(手冊 p.84)。"""
    volts, amps = text.strip().split(",")
    return parse_number(volts), parse_number(amps)


def power_problem(volts: float, amps: float) -> str | None:
    """V 設定 × I 限流超過額定 100 W 就擋下(README 4)。

    **這道鎖是防手滑,不是安全機制** —— 安全靠儀器自己的 OVP/OCP。
    """
    watts = volts * amps
    if watts > RATED_POWER + 1e-9:
        return (f"power limit {RATED_POWER:.0f}W exceeded: "
                f"{volts:g}V x {amps:g}A = {watts:.1f}W")
    return None
