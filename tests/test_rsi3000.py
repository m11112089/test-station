"""RSI3000E:console 命令換算、影像格式、GUI 的純計算,以及 node 的端到端路徑。

端到端那段真的跑一個 eCAL node:serial 換成 stub,TCP 送一張完整大小的合成影像,
確認「packed 12-bit → 四通道 topic」與「service 命令 → console」真的成立。
"""

from __future__ import annotations

import socket
import threading
import time

import ecal.nanobind_core as ecal
import numpy as np
import pytest
from PySide6.QtCore import QPoint

from teststation import client
from teststation.nodes.rsi3000 import image, protocol


def pack12(mosaic: np.ndarray) -> bytes:
    """unpack12 的反向:每兩個 sample 打包成 3 bytes(lsb 排法)。"""
    flat = mosaic.astype(np.uint16).reshape(-1)
    a, b = flat[0::2], flat[1::2]
    packed = np.empty((a.size, 3), dtype=np.uint8)
    packed[:, 0] = a & 0xFF
    packed[:, 1] = (a >> 8) | ((b & 0x0F) << 4)
    packed[:, 2] = b >> 4
    return packed.tobytes()


# ------------------------------------------------------------ 命令換算

def test_exposure_is_converted_to_10ns_ticks():
    assert protocol.exposure_ticks(1_000_000) == 100_000_000
    assert protocol.set_exposure(1_000_000) == "sce l 5F5E100"


def test_frame_rate_becomes_a_frame_time_in_ticks():
    assert protocol.set_frame_rate(1.0) == "scft * 5F5E100"
    assert protocol.frame_ticks(10.0) == 10_000_000


def test_frames():
    assert protocol.set_frames(3) == "scnf 3"


@pytest.mark.parametrize("call", [
    lambda: protocol.set_exposure(43_000_000),   # > 32-bit tick(約 42.9 s)
    lambda: protocol.set_exposure(0),
    lambda: protocol.set_frame_rate(0.02),       # 50 s 一張,超過 32-bit
    lambda: protocol.set_frame_rate(0),
    lambda: protocol.set_frames(0),
])
def test_out_of_range_values_are_rejected_not_truncated(call):
    with pytest.raises(ValueError):
        call()


def test_console_output_is_split_into_lines():
    splitter = protocol.LineSplitter()
    assert splitter.feed(b"scnf 3\r\nOK\r") == ["scnf 3"]
    assert splitter.feed(b"\n> ") == ["OK"]
    assert splitter.flush() == "> "        # 提示符沒有換行,靠 flush 送出
    assert splitter.flush() is None


# ------------------------------------------------------------ 影像格式

def test_unpack12_matches_the_wire_layout():
    # 兩個 sample 0xABC、0x123 → bytes BC 3A 12
    assert image.unpack12(bytes([0xBC, 0x3A, 0x12]), width=2, height=1).tolist() == [[0xABC, 0x123]]


def test_unpack12_round_trips():
    mosaic = np.arange(8 * 4, dtype=np.uint16).reshape(4, 8) * 120 % 4096
    assert np.array_equal(image.unpack12(pack12(mosaic), width=8, height=4), mosaic)


def test_wrong_frame_size_is_rejected():
    with pytest.raises(ValueError):
        image.unpack12(b"\x00" * 10, width=2, height=2)


def test_bayer_split_by_row_and_column_parity():
    mosaic = np.array([[1, 2, 1, 2],
                       [3, 4, 3, 4]], dtype=np.uint16)
    r, gr, gb, b = image.split_bayer(mosaic)
    assert (r.tolist(), gr.tolist(), gb.tolist(), b.tolist()) == (
        [[1, 1]], [[2, 2]], [[3, 3]], [[4, 4]])


def test_channel_message_round_trips_with_the_cpp_header():
    plane = np.array([[0, 4095], [273, 819]], dtype=np.uint16)
    message = image.encode(2, 7, plane)
    assert len(message) == 24 + plane.size * 2
    assert message[:4] == b"EIMG"                # magic 0x474D4945,little-endian 下讀起來就是 EIMG
    frame = image.decode(message)
    assert (frame.channel, frame.index) == (2, 7)
    assert np.array_equal(frame.plane, plane)


@pytest.mark.parametrize("message", [
    b"",
    b"\x00" * 24,                                              # magic 不對
    image.encode(0, 0, np.zeros((2, 2), np.uint16))[:-2],      # 少了資料
    image.HEADER.pack(image.MAGIC, 2, 2, 12, 9, 0) + b"\x00" * 8,   # channel 超出範圍
])
def test_bad_messages_are_ignored(message):
    assert image.decode(message) is None


def test_stats_read_raw_dn():
    s = image.stat(np.array([[0, 4095], [100, 100]], dtype=np.uint16))
    assert (s.min, s.max) == (0, 4095)
    assert s.mean == pytest.approx(1073.75)


# ------------------------------------------------------- GUI 的純計算

def test_preview_shifts_12_bit_down_to_8_bit():
    from teststation.nodes.rsi3000.gui import preview
    plane = np.full((image.CHANNEL_HEIGHT, image.CHANNEL_WIDTH), 4095, np.uint16)
    cell = preview(plane, image.stat(plane), auto_contrast=False)
    assert cell.shape == (384, 512)
    assert cell.dtype == np.uint8 and cell.max() == 255


def test_auto_contrast_stretches_only_the_display_copy():
    from teststation.nodes.rsi3000.gui import preview
    plane = np.full((image.CHANNEL_HEIGHT, image.CHANNEL_WIDTH), 100, np.uint16)
    plane[::8, ::8] = 110
    before = plane.copy()
    cell = preview(plane, image.stat(plane), auto_contrast=True)
    assert (cell.min(), cell.max()) == (0, 255)
    assert np.array_equal(plane, before)          # 原始平面沒被動到


def _planes(values):
    return [np.full((image.CHANNEL_HEIGHT, image.CHANNEL_WIDTH), v, np.uint16) for v in values]


def test_rgb_takes_r_mean_of_greens_and_b():
    from teststation.nodes.rsi3000.gui import compose_rgb
    planes = _planes((4095, 1600, 2400, 0))      # G = 2000 → 125(>> 4)
    rgb = compose_rgb(planes, [image.stat(p) for p in planes], auto_contrast=False)
    assert rgb.shape == (768, 1024, 3) and rgb.dtype == np.uint8
    assert rgb[0, 0].tolist() == [255, 125, 0]


def test_rgb_auto_contrast_stretches_each_colour():
    from teststation.nodes.rsi3000.gui import compose_rgb
    planes = _planes((100, 200, 200, 300))
    for p, bright in zip(planes, (110, 210, 210, 310)):
        p[::4, ::4] = bright
    rgb = compose_rgb(planes, [image.stat(p) for p in planes], auto_contrast=True)
    for colour in range(3):
        assert (rgb[..., colour].min(), rgb[..., colour].max()) == (0, 255)


def test_dragging_on_the_rgb_view_maps_by_two():
    from teststation.nodes.rsi3000.gui import drag_to_roi
    # RGB 畫面整張一格、縮小 2 倍:(600, 400) → (700, 500) 就是通道的 (1200, 800) 起 202×202
    assert drag_to_roi(QPoint(600, 400), QPoint(700, 500), rgb=True) == (1200, 800, 202, 202)


def test_roi_is_clamped_to_the_channel():
    from teststation.nodes.rsi3000.gui import clamp_roi
    assert clamp_roi(-5, 2000, 99999, 0) == (0, 1535, 2048, 1)


def test_dragging_maps_to_channel_pixels_in_the_start_quadrant():
    from teststation.nodes.rsi3000.gui import drag_to_roi
    # 從 Gr 那一格(右上)的 (10, 20) 拖到 (110, 70),座標放大 4 倍
    assert drag_to_roi(QPoint(512 + 10, 20), QPoint(512 + 110, 70)) == (40, 80, 404, 204)


# ------------------------------------------------------- node 端到端

class FakeSerial:
    """記下 node 寫出的東西;`feed` 的資料會被 node 當成 console 輸出讀到。"""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self._incoming = bytearray()
        self._lock = threading.Lock()

    def feed(self, data: bytes) -> None:
        with self._lock:
            self._incoming.extend(data)

    @property
    def in_waiting(self) -> int:
        with self._lock:
            return len(self._incoming)

    def read(self, size: int = 1) -> bytes:
        with self._lock:
            data = bytes(self._incoming[:size])
            del self._incoming[:size]
        if not data:
            time.sleep(0.02)
        return data

    def write(self, data: bytes) -> int:
        self.written.append(data)
        return len(data)

    def reset_input_buffer(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture(scope="module")
def node():
    from teststation.nodes.rsi3000.node import Rsi3000Node

    class StubNode(Rsi3000Node):
        def open_device(self) -> None:
            self._serial = self.fake = FakeSerial()

    server = socket.create_server(("127.0.0.1", 0))
    instance = StubNode("COM98", server)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    for _ in range(100):
        if instance.status == "ok":
            break
        time.sleep(0.05)
    yield instance
    instance.stop()
    thread.join(timeout=10)


def _call(node, line: str) -> str:
    return client.call(node.prefix, line, connect_timeout=5.0)


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_commands_are_written_to_the_console_with_crlf(node):
    assert _call(node, "send help me") == "OK queued: help me"
    assert _wait_for(lambda: b"help me\r\n" in node.fake.written)


def test_settings_are_converted_by_the_node(node):
    reply = _call(node, "set_exposure 1000000")
    assert reply == "OK queued: sce l 5F5E100 (1e+06 us)"
    assert _call(node, "set_exposure 50000000").startswith("ERR exposure exceeds")


def test_next_command_waits_for_the_prompt(node):
    node.fake.written.clear()
    node.fake.feed(b"> ")                      # 先讓 console 閒下來
    time.sleep(0.4)
    _call(node, "send first")
    _call(node, "send second")
    assert _wait_for(lambda: b"first\r\n" in node.fake.written)
    time.sleep(0.1)
    assert b"second\r\n" not in node.fake.written    # 還沒等到提示符,也還沒滿 300 ms
    node.fake.feed(b"ok\r\n> ")
    assert _wait_for(lambda: b"second\r\n" in node.fake.written)


def test_console_output_reaches_the_raw_topic(node):
    received: list[str] = []
    sub = ecal.Subscriber(f"{node.prefix}/raw")
    sub.set_receive_callback(lambda _t, _dt, data, *_: received.append(data.buffer.decode()))
    time.sleep(1.0)
    node.fake.feed(b"RSI3000E v1.0\r\n")
    assert _wait_for(lambda: "RSI3000E v1.0" in received)
    sub.remove_receive_callback()


def test_a_tcp_frame_becomes_four_channel_topics(node):
    frames: dict[int, image.Frame] = {}
    subs = []
    for name in image.CHANNELS:
        sub = ecal.Subscriber(f"{node.prefix}/image_{name}")
        sub.set_receive_callback(
            lambda _t, _dt, data, *_: frames.setdefault(
                image.decode(data.buffer).channel, image.decode(data.buffer)))
        subs.append(sub)
    time.sleep(1.5)                            # 等 eCAL 配對,不然第一張會掉

    mosaic = np.empty((image.FULL_HEIGHT, image.FULL_WIDTH), np.uint16)
    mosaic[0::2, 0::2], mosaic[0::2, 1::2] = 273, 546
    mosaic[1::2, 0::2], mosaic[1::2, 1::2] = 819, 4095
    with socket.create_connection(node._listener.getsockname()) as conn:
        conn.sendall(pack12(mosaic))

    assert _wait_for(lambda: len(frames) == 4, timeout=15)
    for channel, expected in enumerate((273, 546, 819, 4095)):
        plane = frames[channel].plane
        assert plane.shape == (image.CHANNEL_HEIGHT, image.CHANNEL_WIDTH)
        assert int(plane.min()) == int(plane.max()) == expected
    for sub in subs:
        sub.remove_receive_callback()
