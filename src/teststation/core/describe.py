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

    setting: bool = False
    """這個命令設的是**一個會留下來的值**,不是一次性的動作。

    設定會被存進 launch 檔的 `setting` 欄位,下次啟動時重放 ——
    所以 `set_voltage` 是設定,`output_on` 不是。搞錯的話開檔就會送電。

    「同一個設定」以命令加上**最後一個參數以外**的所有詞為準:
    `set_channel ch3 X` 與 `set_channel ch1 Y` 是兩個設定,重設 ch3 則覆蓋前一次。
    """

    variadic: bool = False
    """最後一個參數吃掉整行剩下的文字。

    `mark DUT power on` 是一個參數不是三個 —— 沒有這個,凡是帶空白的自由文字
    都得逼使用者加引號,但命令的契約是「一行純文字」,沒有引號這回事。
    """

    def as_dict(self) -> dict:
        d: dict = {"name": self.name, "label": self.label}
        if self.style:
            d["style"] = self.style
        if self.setting:
            d["setting"] = True
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


def setting_key(line: str, arity: int | None = None) -> str:
    """一行設定命令的「身分」——重設同一個 key 會覆蓋前一次。

    key = 命令 + **宣告的最後一個參數以外**的所有參數:

    ======================================  ======  ====================
    命令                                    參數數  key
    ======================================  ======  ====================
    ``set_voltage 12``                      1       ``set_voltage``
    ``set_channel ch3 SoC``                 2       ``set_channel ch3``
    ``record a b c``(variadic,算 1 個)    1       ``record``
    ======================================  ======  ====================

    `arity` 來自 `describe` 裡宣告的參數個數;沒給就當成這一行的詞數減一
    (對非 variadic 的命令等價)。**variadic 命令一定要給**,不然最後一個參數裡的
    空白會被當成分隔,整份清單變成好幾個 key。
    """
    words = line.split()
    if arity is None:
        arity = max(len(words) - 1, 0)
    # 留下命令本身與「最後一個宣告參數」之前的所有參數。
    return " ".join(words[:arity]) if arity else line
