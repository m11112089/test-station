"""RSI3000E node —— 一個 process 同時橋接裝置的兩個介面。

* **serial console**:service 收到的命令排進佇列,一次寫一條到 COM(補 CRLF),
  寫完要等 console 提示符 ``>`` 回來(300 ms 保底)才送下一條 —— 給裝置反應時間。
  console 的所有輸出一行一則發到 ``RSI3000/<port>/raw``。
* **TCP 影像**:一個連線 = 一張 packed 12-bit RGGB,拆成四通道發到
  ``RSI3000/<port>/image_{R,Gr,Gb,B}``(格式見 `image.py`)。

``status`` 跟著 console 走(core 的重連狀態機);影像執行緒獨立,console 斷線時影像照收。

這是 DUT,不是測試機台:畫面在 ``gui.py``,不跟站台 UI 共用。
"""

from __future__ import annotations

import argparse
import errno
import json
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass

import ecal.nanobind_core as ecal
import serial

from ...core.describe import Arg, Command
from ...core.node import DeviceError, Node
from ...core.topics import IMAGE_PREFIX, RAW
from . import image, protocol

DEFAULT_BAUDRATE = 115200
DEFAULT_TCP_HOST = "0.0.0.0"
DEFAULT_TCP_PORT = 5001

READ_TIMEOUT = 0.05
PROMPT_TIMEOUT = 0.3
"""沒等到提示符時,最多隔這麼久就送下一條。"""

ACCEPT_TIMEOUT = 1.0
"""accept 的輪詢週期,讓影像執行緒能及時反應停止。"""

RECEIVE_TIMEOUT = 30.0
"""一張影像傳到一半就沒下文時,多久放棄這個連線。"""

MODULE = "teststation.nodes.rsi3000.node"
"""用來認出「這是另一個 RSI3000 node」:它的命令列一定帶著這個模組路徑。"""

TAKEOVER_TIMEOUT = 5.0
"""砍掉舊的 node 之後,最多等這麼久讓 port 放出來。"""

_TEXT = ecal.DataTypeInformation(name="text", encoding="utf-8")
_IMAGE = ecal.DataTypeInformation(name="rsi3000_image", encoding="raw")


class Rsi3000Node(Node):
    device = protocol.DEVICE

    def __init__(self, port: str, listener: socket.socket, *,
                 baudrate: int = DEFAULT_BAUDRATE) -> None:
        super().__init__(port)
        self._baudrate = baudrate
        self._listener = listener
        self._serial: serial.Serial | None = None
        self._lines = protocol.LineSplitter()
        self._queue: deque[str] = deque()
        self._queue_lock = threading.Lock()
        self._prompt_seen = True
        self._last_write = 0.0
        self._captures = protocol.CaptureTracker()
        self._raw_pub: ecal.Publisher | None = None
        self._image_thread: threading.Thread | None = None

    # -------------------------------------------------------------- 命令

    def commands(self) -> list[Command]:
        return [
            Command("send", "送一行給 console", self._cmd_send,
                    args=(Arg("text"),), variadic=True),
            Command("sensor_on", "開啟 sensor (csp 1)",
                    lambda: self._enqueue(protocol.SENSOR_ON)),
            Command("sensor_off", "關閉 sensor (csp 0)",
                    lambda: self._enqueue(protocol.SENSOR_OFF)),
            Command("set_frames", "張數 (scnf)", self._cmd_set_frames,
                    args=(Arg("count", min=1),), setting=True),
            Command("set_exposure", "曝光 (sce)", self._cmd_set_exposure,
                    args=(Arg("exposure", unit="us", min=0.01),), setting=True),
            Command("set_frame_rate", "間隔 (scft)", self._cmd_set_frame_rate,
                    args=(Arg("fps", unit="Hz", min=0.03),), setting=True),
            Command("capture", "拍攝 (tfc)", lambda: self._enqueue(protocol.CAPTURE)),
        ]

    def _cmd_send(self, text: str) -> str:
        return self._enqueue(text)

    def _cmd_set_frames(self, count: str) -> str:
        if not count.isdigit():
            raise ValueError(f"count must be an integer, got {count!r}")
        return self._enqueue(protocol.set_frames(int(count)))

    def _cmd_set_exposure(self, exposure: str) -> str:
        line = protocol.set_exposure(float(exposure))
        return f"{self._enqueue(line)} ({float(exposure):g} us)"

    def _cmd_set_frame_rate(self, fps: str) -> str:
        line = protocol.set_frame_rate(float(fps))
        return f"{self._enqueue(line)} ({float(fps):g} fps)"

    def _enqueue(self, line: str) -> str:
        """排進 console 佇列。console 沒接上就 ERR —— 不收下來晚點再送。"""
        if not self._connected:
            raise RuntimeError("console not connected")
        with self._queue_lock:
            self._queue.append(line)
        return f"queued: {line}"

    # ----------------------------------------------------------- console

    def open_device(self) -> None:
        try:
            self._serial = serial.Serial(self.port, baudrate=self._baudrate,
                                         timeout=READ_TIMEOUT, write_timeout=1.0)
        except (serial.SerialException, OSError) as exc:
            raise DeviceError(str(exc)) from exc
        self._serial.reset_input_buffer()
        self._lines = protocol.LineSplitter()
        self._prompt_seen = True   # console 剛開時是閒置的

    def close_device(self) -> None:
        with self._queue_lock:
            self._queue.clear()    # 斷線前沒送出去的命令不留到重連後才送
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def poll(self) -> None:
        assert self._serial is not None
        try:
            data = self._serial.read(1)
            waiting = self._serial.in_waiting
            if waiting:
                data += self._serial.read(waiting)
        except (serial.SerialException, OSError) as exc:
            raise DeviceError(str(exc)) from exc

        if data:
            if protocol.PROMPT in data:
                self._prompt_seen = True
            for line in self._lines.feed(data):
                self._publish_raw(line)
        else:
            # 安靜下來了:把沒換行的半行(通常是提示符)也送出去。
            partial = self._lines.flush()
            if partial is not None:
                self._publish_raw(partial)

        self._write_next()

    def _write_next(self) -> None:
        if not (self._prompt_seen or time.monotonic() - self._last_write > PROMPT_TIMEOUT):
            return
        with self._queue_lock:
            if not self._queue:
                return
            line = self._queue.popleft()
        try:
            self._serial.write(f"{line}\r\n".encode("utf-8"))
        except (serial.SerialException, OSError) as exc:
            raise DeviceError(str(exc)) from exc
        self._prompt_seen = False
        self._last_write = time.monotonic()
        self._captures.sent(line)
        self.activity("TX", line)

    def _publish_raw(self, line: str) -> None:
        if self._raw_pub is not None:
            self._raw_pub.send(line.encode("utf-8"))

    # ------------------------------------------------------------- 影像

    def on_start(self) -> None:
        self._raw_pub = ecal.Publisher(f"{self.prefix}/{RAW}", _TEXT)
        self._image_thread = threading.Thread(target=self._image_loop, daemon=True)
        self._image_thread.start()

    def on_stop(self) -> None:
        if self._image_thread is not None:
            self._image_thread.join(timeout=ACCEPT_TIMEOUT * 3)
        self._listener.close()

    def _image_loop(self) -> None:
        publishers = [
            ecal.Publisher(f"{self.prefix}/{IMAGE_PREFIX}{name}", _IMAGE)
            for name in image.CHANNELS
        ]
        self._listener.settimeout(ACCEPT_TIMEOUT)
        index = 0
        while not self._stop.is_set():
            try:
                conn, peer = self._listener.accept()
            except TimeoutError:
                continue
            except OSError as exc:
                if self._stop.is_set():
                    break
                self.activity("ERR", f"image accept failed: {exc}")
                self._stop.wait(ACCEPT_TIMEOUT)
                continue

            try:
                with conn:
                    conn.settimeout(RECEIVE_TIMEOUT)
                    data = _receive_all(conn)
            except OSError as exc:
                self.activity("ERR", f"image from {peer[0]} dropped: {exc}")
                continue

            try:
                planes = image.split_bayer(image.unpack12(data))
            except ValueError as exc:      # 大小不對:整張丟掉,不用半張資料
                self.activity("ERR", f"image from {peer[0]}: {exc}")
                continue
            # 不是 tfc 拍的(例如開 sensor 時的自我測試)照樣發,但標起來,讓接收端分得出來。
            captured = self._captures.frame_arrived()
            for channel, (publisher, plane) in enumerate(zip(publishers, planes)):
                publisher.send(image.encode(channel, index, plane, captured))
            note = "" if captured else " (not captured: no tfc pending)"
            self.activity("IMG", f"frame {index} from {peer[0]}{note}")
            index += 1


def _receive_all(conn: socket.socket) -> bytes:
    """收到對方關連線為止 —— 相機傳完一張就自己關。"""
    buffer = bytearray()
    chunk = memoryview(bytearray(256 * 1024))
    while True:
        received = conn.recv_into(chunk)
        if received == 0:
            return bytes(buffer)
        buffer.extend(chunk[:received])


@dataclass(frozen=True)
class PortOwner:
    pid: int
    name: str
    command: str
    parent_pid: int
    parent_command: str


def port_owner(port: int) -> PortOwner | None:
    """Windows 上誰在監聽這個 TCP port。查不到(或不是 Windows)回 None。"""
    if sys.platform != "win32":
        return None
    script = (
        f"$c = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue"
        " | Select-Object -First 1;"
        " if (-not $c) { exit };"
        " $p = Get-CimInstance Win32_Process -Filter \"ProcessId=$($c.OwningProcess)\";"
        " $q = Get-CimInstance Win32_Process -Filter \"ProcessId=$($p.ParentProcessId)\";"
        " [pscustomobject]@{pid=$p.ProcessId; name=$p.Name; cmd=$p.CommandLine;"
        " ppid=$p.ParentProcessId; pcmd=$q.CommandLine} | ConvertTo-Json -Compress"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if not result.stdout.strip():
        return None
    found = json.loads(result.stdout)
    return PortOwner(int(found["pid"]), found.get("name") or "?", found.get("cmd") or "",
                     int(found.get("ppid") or 0), found.get("pcmd") or "")


def listen(host: str, port: int, log=print) -> socket.socket:
    """監聽影像 port。**被舊的 RSI3000 node 佔著就把它砍掉再接手** —— 重複啟動時新的贏。

    佔著的如果不是 RSI3000 node(例如 C++ 版的 rsi3000_bridge),一律不碰,照樣報錯,
    並說出是誰佔著。
    """
    try:
        return socket.create_server((host, port))
    except OSError as exc:
        if exc.errno not in (errno.EADDRINUSE, 10048):
            raise
        owner = port_owner(port)
        if owner is None:
            raise
        if MODULE not in owner.command:
            raise OSError(exc.errno, f"port {port} is held by {owner.name} (pid {owner.pid}),"
                                     " not an RSI3000 node; leaving it alone") from exc

    # venv 的 python.exe 是個啟動器,真正監聽的是它的子行程:連同啟動器整棵一起收掉。
    target = owner.parent_pid if MODULE in owner.parent_command else owner.pid
    log(f"port {port} is held by a previous RSI3000 node (pid {owner.pid}); stopping it")
    subprocess.run(["taskkill", "/PID", str(target), "/T", "/F"], capture_output=True)

    deadline = time.monotonic() + TAKEOVER_TIMEOUT
    while True:
        try:
            return socket.create_server((host, port))
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rsi3000", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True,
                        help="console 的 COM(必填:console 沒有無害的辨識方式,不掃描)")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUDRATE)
    parser.add_argument("--tcp-host", default=DEFAULT_TCP_HOST, help="影像監聽位址")
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT, help="影像監聽 port")
    args = parser.parse_args(argv)

    try:
        listener = listen(args.tcp_host, args.tcp_port)
    except OSError as exc:
        print(f"TCP listen {args.tcp_host}:{args.tcp_port} failed: {exc}", file=sys.stderr)
        return 1

    node = Rsi3000Node(args.port, listener, baudrate=args.baud)
    print(f"{node.prefix} starting, images on tcp {args.tcp_host}:{args.tcp_port}")
    try:
        node.run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
