"""`st console` —— DUT 雙窗格 TUI(README 5.2)。

```
┌─ DUT console ─────────────────────────────────┐
│ [RX] soc: 71.2C  ddr: 58.0C                    │  raw topic
│ [TX] > temp                                    │  經 send method
├─ node activity ───────────────────────────────┤
│ 15:30:01.123 PUB Serial/COM5/soc_temp 71.2 °C  │  activity topic
│ 15:30:02.500 SRV set_channel soc_temp → OK     │
└────────────────────────────────────────────────┘
```

上半是 DUT 原始收發,下半是 node 解析後 publish 的值與收到的命令。
調整 profile 的 `parse()` 時用來對照:上面有這行、下面沒有對應的 PUB,就是 parse 沒接到。
"""

from __future__ import annotations

import ecal.nanobind_core as ecal
from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input, RichLog

from .common import ServiceUnavailable, call, format_payload


class ConsoleApp(App):
    """target 是 service 名稱,例如 `Serial/COM5`。"""

    CSS = """
    #dut      { height: 1fr; border: round $accent; }
    #activity { height: 1fr; border: round $primary; }
    #entry    { dock: bottom; }
    """
    BINDINGS = [("ctrl+c", "quit", "離開")]

    def __init__(self, target: str) -> None:
        super().__init__()
        self.target = target
        self._subs: list[ecal.Subscriber] = []

    def compose(self) -> ComposeResult:
        yield Header()
        dut = RichLog(id="dut", highlight=False, markup=True, wrap=True)
        dut.border_title = f"DUT console — {self.target}"
        yield dut
        activity = RichLog(id="activity", highlight=False, markup=True, wrap=True)
        activity.border_title = "node activity"
        yield activity
        yield Input(placeholder="輸入後 Enter 送給 DUT", id="entry")
        yield Footer()

    def on_mount(self) -> None:
        self._subscribe(f"{self.target}/raw", self._on_raw)
        self._subscribe(f"{self.target}/activity", self._on_activity)
        self.query_one("#entry", Input).focus()
        self.query_one("#dut", RichLog).write(
            f"[dim]listening on {self.target}/raw — 輸入文字後 Enter 會送出 "
            f"`send <text>`[/dim]"
        )

    def _subscribe(self, topic: str, handler) -> None:
        sub = ecal.Subscriber(topic)
        # eCAL 的 callback 在別的執行緒,要 marshal 回 textual 的執行緒才能畫。
        sub.set_receive_callback(
            lambda _t, _dt, data, *_: self.call_from_thread(handler, data.buffer)
        )
        self._subs.append(sub)

    def _on_raw(self, raw: bytes) -> None:
        text = raw.decode("utf-8", "replace").rstrip()
        self.query_one("#dut", RichLog).write(f"[green][RX][/green] {text}")

    def _on_activity(self, raw: bytes) -> None:
        self.query_one("#activity", RichLog).write(format_payload(raw))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        self.query_one("#dut", RichLog).write(f"[cyan][TX][/cyan] > {text}")
        # service 呼叫會阻塞,丟到 worker,不要卡住畫面。
        self.run_worker(lambda: self._send(text), thread=True)

    def _send(self, text: str) -> None:
        try:
            reply = call(self.target, f"send {text}")
        except ServiceUnavailable as exc:
            reply = f"ERR {exc}"
        if not reply.startswith("OK"):
            self.call_from_thread(
                self.query_one("#dut", RichLog).write, f"[red]{reply}[/red]"
            )

    def on_unmount(self) -> None:
        for sub in self._subs:
            sub.remove_receive_callback()


def run_console(target: str) -> int:
    ConsoleApp(target).run()
    return 0
