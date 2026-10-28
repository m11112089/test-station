"""plans/pfr_pel_cycle_4h.py 的命令順序與安全收尾。不碰 eCAL、不真的等。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PLAN = Path(__file__).resolve().parents[1] / "plans" / "pfr_pel_cycle_4h.py"
spec = importlib.util.spec_from_file_location("pfr_pel_cycle_4h", PLAN)
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)

PSU, LOAD = plan.PSU, plan.LOAD


class Bench:
    """記下每道命令與每次 sleep;`reject` 裡的命令回 ERR,`interrupt_at` 那道命令丟 Ctrl+C。"""

    def __init__(self, reject=(), interrupt_at=None, trip_after_checks=None):
        self.trip_after_checks = trip_after_checks
        self.checks = 0
        self.sent: list[tuple[str, str]] = []
        self.slept: list[float] = []
        self.reject = set(reject)
        self.interrupt_at = interrupt_at

    def cmd(self, service, line):
        self.sent.append((service, line))
        if (service, line) == self.interrupt_at:
            raise KeyboardInterrupt
        if (service, line) in self.reject:
            raise plan.PlanError(f"{service} {line}: ERR")
        if line == "check":
            self.checks += 1
            if self.trip_after_checks is not None and self.checks > self.trip_after_checks:
                return "OK load OFF, mode CV; load turned off by the instrument at 17:52:00"
            return "OK load ON, mode CV"
        return "OK"

    def sleep(self, seconds):
        self.slept.append(seconds)


SAFE_OFF = [(LOAD, "load_off"), (PSU, "output_off")]


def test_full_sequence():
    bench = Bench()
    plan.run(bench.cmd, bench.sleep, duration=2 * 60, half_cycle=30)
    assert bench.sent == [
        (PSU, "set_current 0.5"),
        (PSU, "set_voltage 30.0"),
        (LOAD, "set_voltage 17.0"),
        (LOAD, "set_mode CV"),
        (PSU, "output_on"),
        (LOAD, "load_on"),
        (LOAD, "check"),
        (PSU, "set_current 1.0"), (LOAD, "check"), (PSU, "set_current 0.5"), (LOAD, "check"),
        (PSU, "set_current 1.0"), (LOAD, "check"), (PSU, "set_current 0.5"), (LOAD, "check"),
        *SAFE_OFF,
    ]
    assert bench.slept == [5, 30, 30, 30, 30, 30]


def test_four_hours_is_240_cycles():
    bench = Bench()
    plan.run(bench.cmd, bench.sleep)
    assert bench.sent.count((PSU, "set_current 1.0")) == 240
    assert sum(bench.slept) == 5 + 30 + 4 * 3600


def test_rejected_load_setting_stops_before_any_power():
    """PEL 在 Low voltage range 會拒收 CV 17 V —— 這時還沒送電,收尾照樣跑。"""
    bench = Bench(reject=[(LOAD, "set_voltage 17.0")])
    with pytest.raises(plan.PlanError):
        plan.run(bench.cmd, bench.sleep, duration=120)
    assert (PSU, "output_on") not in bench.sent
    assert bench.sent[-2:] == SAFE_OFF


def test_ctrl_c_mid_cycle_turns_load_off_then_supply_off():
    bench = Bench(interrupt_at=(PSU, "set_current 1.0"))
    with pytest.raises(KeyboardInterrupt):
        plan.run(bench.cmd, bench.sleep, duration=600)
    assert bench.sent[-2:] == SAFE_OFF


def test_load_tripped_by_protection_stops_the_plan():
    """實機:ROCP 讓 PEL 自己關了 load,load_on 卻回 OK。計畫要在下一次 check 停下並收尾。"""
    bench = Bench(trip_after_checks=2)
    with pytest.raises(plan.PlanError, match="not loading"):
        plan.run(bench.cmd, bench.sleep, duration=600)
    assert bench.checks == 3
    assert bench.sent[-2:] == SAFE_OFF


def test_plan_never_touches_the_log():
    """記錄由 launch 檔負責;計畫若下 start / stop,會換掉或關掉那份 data.csv。"""
    bench = Bench()
    plan.run(bench.cmd, bench.sleep, duration=120)
    assert {service for service, _ in bench.sent} == {PSU, LOAD}


def test_shutdown_continues_when_one_step_fails():
    """負載關不掉(例如 USB 掉了)也還是要去關電源。"""
    bench = Bench(reject=[(LOAD, "load_off")])
    plan.run(bench.cmd, bench.sleep, duration=60)
    assert bench.sent[-2:] == SAFE_OFF
