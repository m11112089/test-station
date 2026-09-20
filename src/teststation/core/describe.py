"""``describe`` 命令清單的資料結構(README 2.3)。

同一份清單是三件事的來源:UI 自動產生的按鈕、``st`` 的 Tab 補完、``INTERFACE.md`` 的命令表。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Arg:
    name: str
    unit: str | None = None
    min: float | None = None
    max: float | None = None

    def as_dict(self) -> dict:
        d: dict = {"name": self.name}
        for key in ("unit", "min", "max"):
            value = getattr(self, key)
            if value is not None:
                d[key] = value
        return d


@dataclass(frozen=True)
class Command:
    name: str
    label: str
    handler: Callable[..., str]
    style: str | None = None
    args: tuple[Arg, ...] = field(default_factory=tuple)

    variadic: bool = False
    """最後一個參數吃掉整行剩下的文字。

    `mark DUT power on` 是一個參數不是三個 —— 沒有這個,凡是帶空白的自由文字
    都得逼使用者加引號,但命令的契約是「一行純文字」,沒有引號這回事。
    """

    def as_dict(self) -> dict:
        d: dict = {"name": self.name, "label": self.label}
        if self.style:
            d["style"] = self.style
        if self.args:
            d["args"] = [a.as_dict() for a in self.args]
        return d


def to_json(commands: list[Command]) -> str:
    return json.dumps([c.as_dict() for c in commands], ensure_ascii=False)


def split_args(command: Command, args: list[str]) -> list[str]:
    """variadic 命令的最後一個參數吃掉剩下的所有詞。"""
    if not command.variadic or not command.args:
        return args
    keep = len(command.args) - 1
    if len(args) <= keep:
        return args
    return args[:keep] + [" ".join(args[keep:])]


def check_args(command: Command, args: list[str]) -> str | None:
    """依 describe 宣告檢查參數個數與 min/max,回傳錯誤訊息或 None。"""
    if len(args) != len(command.args):
        expected = " ".join(a.name for a in command.args) or "(no args)"
        return f"{command.name} expects: {expected}"
    for spec, raw in zip(command.args, args):
        if spec.min is None and spec.max is None:
            continue
        try:
            value = float(raw)
        except ValueError:
            return f"{spec.name} must be a number, got {raw!r}"
        if spec.min is not None and value < spec.min:
            return f"{spec.name} below min {spec.min}"
        if spec.max is not None and value > spec.max:
            return f"{spec.name} above max {spec.max}"
    return None
