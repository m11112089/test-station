"""PFR-100L 供電、PEL-3031E CV 17 V 拉載,電源限流在 1 A / 0.5 A 之間每 30 秒切換,共 4 小時。

    power supply 30 V / 0.5 A,開輸出
    sleep 5 s
    eLoad CV 17 V,開始拉載
    sleep 30 s
    重複 4 小時(每輪 60 s,共 240 輪):
        power supply 限流 1 A,   sleep 30 s
        power supply 限流 0.5 A, sleep 30 s
    eLoad off
    power supply off

物理上:負載 CV 17 V 會把電壓往下拉,電源因此進入限流(CC),
兩台之間流的電流就是電源的限流值 —— 0.5 A(8.5 W)與 1 A(17 W)交替。

流程:
  1. UI 開 `launch/pfr_pel_log.yaml`:起兩台 node,Log 開始記錄。
  2. 手動執行這支:`uv run python plans/pfr_pel_cycle_4h.py`
     **它只送控制命令,不碰 Log** —— 記錄從 launch 開始、一直錄到 UI 關閉,
     計畫前後的待機數據也都在同一份 data.csv 裡。
  3. Ctrl+C 可隨時中止。不論怎麼結束,最後一律先關負載、再關電源。

執行前:PEL 的 voltage range 要是 **High**。Low range 只到 15 V,CV 17 V 會被拒收
(計畫會在送電前就停下來)。

Main-Control(UI「測試計畫」分頁)還沒實作,所以這支是獨立腳本,直接用 `teststation.client`
送命令 —— 字串跟 UI 按鈕送的完全一樣。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from typing import Callable

from teststation import client

PSU = "PFR-100L/COM7"
LOAD = "PEL-3031E/COM3"

VOLTAGE = 30.0
LOW_LIMIT = 0.5
HIGH_LIMIT = 1.0
LOAD_CV = 17.0
DURATION = 4 * 3600
HALF_CYCLE = 30.0


class PlanError(Exception):
    """某道命令回了 ERR。ERR 表示什麼都沒發生,所以停下來,不帶著錯的狀態繼續跑 4 小時。"""


def log(text: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}", flush=True)


def cmd(service: str, line: str) -> str:
    reply = client.call(service, line)
    log(f"{service:16} {line:22} -> {reply}")
    if not reply.startswith("OK"):
        raise PlanError(f"{service} {line}: {reply}")
    return reply


def require_load_on(cmd: Callable[[str, str], str]) -> None:
    """負載還在拉嗎?`load_on` 回 OK 不代表一直在拉 —— 保護(例如 ROCP)會讓儀器自己關掉,
    而那不會出現在任何命令的回覆裡。沒在拉就停下來,不要空跑 4 小時。"""
    reply = cmd(LOAD, "check")
    if "load ON" not in reply:
        raise PlanError(f"{LOAD} is not loading: {reply}")


def run(cmd: Callable[[str, str], str] = cmd, sleep: Callable[[float], None] = time.sleep,
        duration: float = DURATION, half_cycle: float = HALF_CYCLE) -> None:
    """計畫本體。`cmd` / `sleep` 可替換,讓測試不用真的等 4 小時。"""
    cycles = int(duration // (2 * half_cycle))
    try:
        # 先限流再電壓:PFR 的 100 W 鎖看的是「新值 × 另一個目前的值」。
        cmd(PSU, f"set_current {LOW_LIMIT}")
        cmd(PSU, f"set_voltage {VOLTAGE}")
        # 負載先設好(不拉載),模式放在設值之後;值被拒收(例如 Low range)就在送電前停下。
        # cmd(LOAD, f"set_voltage {LOAD_CV}")
        cmd(LOAD, f"set_voltage 17.5")
        cmd(LOAD, "set_mode CV")

        cmd(PSU, "output_on")
        sleep(5)
        cmd(LOAD, "load_on")
        sleep(30)
        require_load_on(cmd)

        while True:
            # log(f"--- cycle {cycle}/{cycles}")
            cmd(PSU, f"set_current {HIGH_LIMIT}")
            sleep(300)
            # sleep(half_cycle)
            require_load_on(cmd)
            cmd(PSU, f"set_current {LOW_LIMIT}")
            # sleep(half_cycle)
            sleep(300)
            require_load_on(cmd)
        log("done")
    finally:
        # 不論成功、ERR、Ctrl+C 或例外:先關負載、再關電源。兩步都要試。
        for service, line in ((LOAD, "load_off"), (PSU, "output_off")):
            try:
                cmd(service, line)
            except Exception as exc:
                log(f"!! {service} {line} failed: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hours", type=float, default=DURATION / 3600,
                        help="循環總時間(小時),預設 4")
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(errors="replace")
    with client.session("plan-pfr-pel-cycle"):
        try:
            run(duration=args.hours * 3600)
        except KeyboardInterrupt:
            log("aborted by user (outputs turned off)")
            return 130
        except (PlanError, client.ServiceUnavailable) as exc:
            log(f"stopped: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
