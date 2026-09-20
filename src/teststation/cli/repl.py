"""`st` 的互動模式(README 5.1)。

Tab 補完的來源:
  * service / topic 名稱 —— eCAL 原生的 monitoring 註冊資訊
  * method 名稱與參數 —— 對該 service 呼叫 `describe`

所以補完永遠跟站上實際跑著的東西一致,不是寫死的清單。
"""

from __future__ import annotations

import argparse
import shlex
import sys
import time
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory

from . import common
from .common import ServiceUnavailable
from .main import dispatch

GROUPS: dict[str, list[str]] = {
    "node": ["list"],
    "service": ["list", "call", "describe"],
    "topic": ["list", "echo", "pub", "hz"],
    "launch": [],
    "ui": [],
}
BUILTINS = ["help", "exit", "quit"]
TAKES_SERVICE = {("service", "call"), ("service", "describe")}
TAKES_TOPIC = {("topic", "echo"), ("topic", "pub"), ("topic", "hz")}

HISTORY_FILE = Path.home() / ".st_history"
CACHE_TTL = 3.0
"""補完用的探索結果快取多久。太短會每按一次 Tab 就打擾註冊層。"""


class StCompleter(Completer):
    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, list[str]]] = {}

    def _cached(self, key: str, produce) -> list[str]:
        hit = self._cache.get(key)
        if hit and time.monotonic() < hit[0]:
            return hit[1]
        try:
            values = produce()
        except Exception:
            values = []  # 補完失敗不該讓 REPL 爆掉
        self._cache[key] = (time.monotonic() + CACHE_TTL, values)
        return values

    def _services(self) -> list[str]:
        # settle=0:REPL 的 eCAL session 一直開著,註冊資訊早就熱了。
        return self._cached(
            "services", lambda: [s.service_name for s in common.list_services(settle=0)]
        )

    def _topics(self) -> list[str]:
        return self._cached(
            "topics", lambda: [t.topic_name for t in common.list_topics(settle=0)]
        )

    def _methods(self, service: str) -> list[str]:
        return self._cached(
            f"methods:{service}",
            lambda: [c["name"] for c in common.describe(service)],
        )

    def _candidates(self, words: list[str], position: int) -> list[str]:
        if position == 0:
            return list(GROUPS) + BUILTINS
        group = words[0]
        if position == 1:
            return GROUPS.get(group, [])
        action = words[1] if len(words) > 1 else ""
        if position == 2:
            if (group, action) in TAKES_SERVICE:
                return self._services()
            if (group, action) in TAKES_TOPIC:
                return self._topics()
        if position == 3 and (group, action) == ("service", "call"):
            return self._methods(words[2])
        return []

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        words = text.split()
        starting_new_word = not text or text.endswith(" ")
        position = len(words) if starting_new_word else len(words) - 1
        prefix = "" if starting_new_word else words[-1]

        for candidate in self._candidates(words, position):
            if candidate.startswith(prefix):
                yield Completion(candidate, start_position=-len(prefix))


BANNER = """st — test-station 互動模式
  Tab 補完(service/topic 名稱來自 eCAL 註冊,method 來自 describe)
  help 看命令、exit 離開
"""


def execute(line: str, parser: argparse.ArgumentParser) -> bool:
    """跑一行輸入。回傳 False 代表該結束 REPL。"""
    line = line.strip()
    if not line:
        return True
    if line in ("exit", "quit"):
        return False
    if line in ("help", "?"):
        parser.print_help()
        return True
    try:
        args = parser.parse_args(shlex.split(line))
    except SystemExit:
        return True  # argparse 的錯誤訊息已經印過了
    except ValueError as exc:
        print(f"ERR {exc}")
        return True
    try:
        dispatch(args, parser)
    except ServiceUnavailable as exc:
        print(f"ERR {exc}")
    except Exception as exc:
        print(f"ERR {exc}")
    return True


def run_repl(parser: argparse.ArgumentParser) -> int:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        # 被管線或腳本餵輸入:prompt_toolkit 需要真的終端機,這裡直接讀 stdin。
        for line in sys.stdin:
            if not execute(line, parser):
                break
        return 0

    print(BANNER)
    prompt = PromptSession(
        history=FileHistory(str(HISTORY_FILE)), completer=StCompleter()
    )
    while True:
        try:
            line = prompt.prompt("st> ")
        except KeyboardInterrupt:
            continue  # Ctrl+C 清掉這一行,不離開
        except EOFError:
            break
        if not execute(line, parser):
            break
    return 0
