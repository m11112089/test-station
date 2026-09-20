"""`st launch` —— 依序啟動整站(README 3.1)。

類似 ros launch,但**不是等固定秒數,而是等 node 的 `status` 變成 `ok`**。
這件事很重要:node 未指定 `--port` 時會掃描所有 COM,掃描期間會暫時佔住別人的 port,
同時啟動會讓彼此誤判成「使用中」而跳過。所以下一個 node 必須等前一個掃完、port 佔住了才起。

這也是 README 3.1 說明不使用 eCAL Sys 的原因:它只能等固定秒數,不理解 `status` 語意。
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import ecal.nanobind_core as ecal
import yaml

DEFAULT_READY_TIMEOUT = 30.0
POLL = 0.1

RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"


class Launched:
    """一個已啟動的 node:process、輸出前綴、以及它註冊出來的 service 名稱。"""

    def __init__(self, run: str, process: subprocess.Popen) -> None:
        self.run = run
        self.process = process
        self.label = run
        self.service: str | None = None
        self.reader = threading.Thread(target=self._pump, daemon=True)
        self.reader.start()

    def _pump(self) -> None:
        """把子行程的輸出加上前綴合併顯示(README 3.1)。"""
        assert self.process.stdout is not None
        for line in self.process.stdout:
            print(f"{DIM}[{self.label}]{RESET} {line.rstrip()}", flush=True)


def _server_names() -> set[str]:
    try:
        return {s.service_name for s in ecal.monitoring.get_monitoring().servers}
    except RuntimeError:
        return set()


def _wait_ready(before: set[str], timeout: float) -> tuple[str | None, str]:
    """等一個新的 service 出現、而且它的 status 變成 `ok`。

    回傳 (service 名稱, 最後看到的 status)。逾時則 service 可能已經有了但不是 ok。
    """
    deadline = time.monotonic() + timeout
    service: str | None = None
    status = "no registration"
    holder = {"status": status}
    sub = None
    try:
        while time.monotonic() < deadline:
            if service is None:
                new = _server_names() - before
                if new:
                    service = sorted(new)[0]
                    status = "no heartbeat"
                    holder["status"] = status
                    sub = ecal.Subscriber(f"{service}/status")
                    sub.set_receive_callback(
                        lambda _t, _dt, data, *_: holder.__setitem__(
                            "status", data.buffer.decode("utf-8", "replace"))
                    )
            else:
                status = holder["status"]
                if status == "ok":
                    return service, status
            time.sleep(POLL)
    finally:
        if sub is not None:
            sub.remove_receive_callback()
    return service, status


def _spawn(spec: dict) -> subprocess.Popen:
    run = spec["run"]
    executable = shutil.which(run)
    if executable:
        command = [executable]
    else:
        # 沒裝成 console script 時退回用模組路徑跑,錯誤訊息才看得懂。
        command = [sys.executable, "-m", f"teststation.nodes.{run}.node"]
    args = spec.get("args", "")
    if isinstance(args, str):
        command += args.split()
    else:
        command += [str(a) for a in args]
    return subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", bufsize=1,
        # Windows 的 Ctrl+C 會送給整個 console process group,子 node 會跟 launcher
        # 同時收到,那就沒有「反向順序、Log 最後關」可言了。把子行程放到自己的
        # process group,關閉順序才真的由 launcher 決定。
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )


def _stop_on_signals() -> None:
    """除了 Ctrl+C,也接 SIGTERM / SIGBREAK,確保都走得到關閉流程。

    launcher 死了而子 node 還活著,會佔著 COM 不放,下次啟動就掃不到裝置
    (README 3:使用中的 port 直接跳過)。

    **Windows 上仍有救不了的情況**:`taskkill /F` 與工作管理員的「結束工作」是
    TerminateProcess,不給 Python 執行 handler 的機會,子 node 會變孤兒。
    發生時用 `st node list` 看得到,再手動砍掉。
    """
    def handler(_signum, _frame):
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGBREAK", "SIGINT"):
        received = getattr(signal, name, None)
        if received is None:
            continue
        try:
            signal.signal(received, handler)
        except (ValueError, OSError):
            pass  # 不在主執行緒或平台不支援


def run_launch(path: str) -> int:
    _stop_on_signals()
    config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    specs = config.get("nodes") or []
    if not specs:
        print(f"ERR {path} 裡沒有 nodes", file=sys.stderr)
        return 1
    ready_timeout = float(config.get("ready_timeout", DEFAULT_READY_TIMEOUT))

    started: list[Launched] = []
    failed = 0
    try:
        for spec in specs:
            before = _server_names()
            print(f"{DIM}--- starting {spec['run']} {spec.get('args', '')}{RESET}",
                  flush=True)
            try:
                node = Launched(spec["run"], _spawn(spec))
            except (OSError, FileNotFoundError) as exc:
                print(f"{RED}[{spec['run']}] 啟動失敗:{exc}{RESET}", file=sys.stderr)
                failed += 1
                continue
            started.append(node)

            service, status = _wait_ready(before, ready_timeout)
            if service:
                node.service = service
                node.label = service
            if status == "ok":
                print(f"{DIM}[{node.label}] ready{RESET}", flush=True)
            else:
                # README 3.1:紅字警示,但**繼續啟動下一個**,不整批中止。
                print(f"{RED}[{node.label}] 超過 {ready_timeout:.0f}s 仍未 ok "
                      f"(status: {status}) —— 繼續啟動下一個{RESET}", file=sys.stderr,
                      flush=True)
                failed += 1

        print(f"{DIM}--- {len(started)} node(s) up, {failed} 未就緒。Ctrl+C 關閉整站"
              f"{RESET}", flush=True)
        while started and any(n.process.poll() is None for n in started):
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown(started)
    return 1 if failed else 0


def _shutdown(started: list[Launched]) -> None:
    """反向順序關閉,Log 最後關 —— 確保它收得到其他 node 的最後資料(README 3.1)。"""
    for node in reversed(started):
        if node.process.poll() is not None:
            continue
        print(f"{DIM}--- stopping {node.label}{RESET}", flush=True)
        node.process.terminate()
        try:
            node.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            print(f"{RED}[{node.label}] 沒有在 10s 內結束,強制砍掉{RESET}",
                  file=sys.stderr)
            node.process.kill()
