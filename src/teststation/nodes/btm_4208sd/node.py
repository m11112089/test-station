"""BTM-4208SD node —— 12 channel 熱電偶記錄器(Lutron)。

儀器 RS-232 **單向連續輸出**,9600 8N1,**不接受任何命令**:
單位(°C/°F)、取樣時間等設定只能在面板操作,所以這個 node 除了 core 的共通 method
之外不提供任何裝置命令,只負責把封包解出來 publish。

Topic:``BTM-4208SD/<port>/ch1`` … ``ch12``;沒插探棒(over range)的 channel 不 publish。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

import serial

from ...core.node import DeviceError, Node
from ...core.scan import find_port
from .protocol import FrameReader, looks_like_btm, parse_frame

BAUDRATE = 9600
"""手冊 p.24:9600, N, 8, 1。"""

READ_TIMEOUT = 0.5
"""每次 poll 最多阻塞這麼久,讓主迴圈能及時反應停止與心跳。"""

DEFAULT_SCAN_TIMEOUT = 3.0
"""掃描時在每個 port 上聽多久。儀器約每秒送一輪,3 秒足夠聽到數個封包。"""

DEFAULT_DATA_TIMEOUT = 10.0
"""連上之後多久沒收到合格封包就視為斷線(儀器關機、線鬆脫)。"""


class Btm4208sdNode(Node):
    device = "BTM-4208SD"

    def __init__(self, port: str, *, data_timeout: float = DEFAULT_DATA_TIMEOUT) -> None:
        super().__init__(port)
        self._data_timeout = data_timeout
        self._serial: serial.Serial | None = None
        self._reader = FrameReader()
        self._last_frame = 0.0

    # 這台儀器不收命令,所以沒有裝置專屬 method;describe 只會有 core 的三個。

    def open_device(self) -> None:
        try:
            self._serial = serial.Serial(
                self.port, baudrate=BAUDRATE, bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                timeout=READ_TIMEOUT,
            )
        except (serial.SerialException, OSError) as exc:
            raise DeviceError(str(exc)) from exc
        self._reader = FrameReader()
        self._last_frame = time.monotonic()

    def close_device(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def poll(self) -> None:
        assert self._serial is not None
        try:
            chunk = self._serial.read(1)
            waiting = self._serial.in_waiting
            if waiting:
                chunk += self._serial.read(waiting)
        except (serial.SerialException, OSError) as exc:
            raise DeviceError(str(exc)) from exc

        # 時間戳蓋在讀到資料的當下,同一批 byte 的封包共用這個時刻。
        when = datetime.now()
        for reading in self._reader.feed(chunk):
            self._last_frame = time.monotonic()
            if reading.over_range:
                continue  # 沒插探棒,不 publish
            self.publish(reading.topic_channel, reading.value, reading.unit, when)

        if time.monotonic() - self._last_frame > self._data_timeout:
            raise DeviceError(f"no valid frame for {self._data_timeout:.0f}s")


def probe(handle: serial.Serial) -> bool:
    """掃描用的探測:**只聽不送**,在 timeout 內收到合格封包就算數。

    完全不寫入,所以就算誤開到別台裝置或 DUT 的 console 也不會有任何影響
    (README 3:探測字串必須盡量無害)。
    """
    deadline = time.monotonic() + (handle.timeout or DEFAULT_SCAN_TIMEOUT)
    data = bytearray()
    while time.monotonic() < deadline:
        chunk = handle.read(64)
        if not chunk:
            break
        data.extend(chunk)
        if looks_like_btm(bytes(data)):
            return True
    return looks_like_btm(bytes(data))


def resolve_port(port: str | None, scan_timeout: float) -> str:
    if port:
        return port
    print(f"scanning for {Btm4208sdNode.device} (listen-only, {scan_timeout:.0f}s per port)...")
    found = find_port(probe, baudrate=BAUDRATE, timeout=scan_timeout,
                      log=lambda line: print(f"  {line}"))
    if found is None:
        raise SystemExit("no BTM-4208SD found; check the meter is on and try --port")
    print(f"found on {found}")
    return found


def sniff(port: str, scan_timeout: float) -> None:
    """把原始封包印出來,不碰 eCAL。用於上線前確認格式(特別是 over range 的編碼)。"""
    # 單位是 °C / °F,別讓 console 的編碼把整個 sniff 弄掛。
    sys.stdout.reconfigure(errors="replace")
    with serial.Serial(port, baudrate=BAUDRATE, timeout=READ_TIMEOUT) as handle:
        print(f"sniffing {port} at {BAUDRATE} 8N1, Ctrl+C to stop")
        buffer = bytearray()
        while True:
            buffer.extend(handle.read(1) + handle.read(handle.in_waiting or 0))
            while len(buffer) >= 16:
                frame = bytes(buffer[:16])
                reading = parse_frame(frame)
                if reading is None:
                    del buffer[0]
                    continue
                del buffer[:16]
                shown = "over range" if reading.over_range else f"{reading.value} {reading.unit}"
                print(f"{frame!r}  ch{reading.channel:<2} {shown}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="btm_4208sd", description=__doc__)
    parser.add_argument("--port", help="指定 COM;不給則自動掃描")
    parser.add_argument("--scan-timeout", type=float, default=DEFAULT_SCAN_TIMEOUT,
                        help="掃描時每個 port 聽多久(秒)")
    parser.add_argument("--data-timeout", type=float, default=DEFAULT_DATA_TIMEOUT,
                        help="多久沒收到合格封包就視為斷線(秒)")
    parser.add_argument("--sniff", action="store_true",
                        help="只印原始封包,不啟動 eCAL node")
    args = parser.parse_args(argv)

    try:
        port = resolve_port(args.port, args.scan_timeout)
    except (serial.SerialException, OSError) as exc:
        print(f"serial error: {exc}", file=sys.stderr)
        return 1

    if args.sniff:
        try:
            sniff(port, args.scan_timeout)
        except KeyboardInterrupt:
            pass
        return 0

    node = Btm4208sdNode(port, data_timeout=args.data_timeout)
    print(f"{node.prefix} starting")
    try:
        node.run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
