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


# ------------------------------------------------ 哪些影像是 tfc 拍的

def test_frames_without_a_tfc_are_not_captures():
    tracker = protocol.CaptureTracker()
    tracker.sent("csp 1")                      # 開 sensor:裝置會自己送一張自我測試
    assert tracker.frame_arrived() is False


def test_each_tfc_expects_scnf_frames():
    tracker = protocol.CaptureTracker()
    tracker.sent("scnf 2")
    tracker.sent("tfc")
    assert [tracker.frame_arrived() for _ in range(3)] == [True, True, False]


def test_before_any_scnf_everything_after_tfc_counts_until_csp():
    tracker = protocol.CaptureTracker()
    tracker.sent("tfc")
    assert [tracker.frame_arrived() for _ in range(5)] == [True] * 5
    tracker.sent("csp 1")                      # 開關 sensor 歸零
    assert tracker.frame_arrived() is False


def test_tfcs_add_up():
    tracker = protocol.CaptureTracker()
    tracker.sent("scnf 1")
    tracker.sent("tfc")
    tracker.sent("tfc")
    assert [tracker.frame_arrived() for _ in range(3)] == [True, True, False]


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
    assert frame.captured


def test_the_not_captured_flag_rides_in_the_upper_half_of_bits_valid():
    """C++ 版把 offset 12 當成 uint32 的 bits_valid:低 16 位仍是 12,旗標在高 16 位。"""
    message = image.encode(0, 1, np.zeros((2, 2), np.uint16), captured=False)
    assert len(message) == 24 + 8
    assert int.from_bytes(message[12:16], "little") == 12 | (image.FLAG_NOT_CAPTURED << 16)
    assert image.decode(message).captured is False


@pytest.mark.parametrize("message", [
    b"",
    b"\x00" * 24,                                              # magic 不對
    image.encode(0, 0, np.zeros((2, 2), np.uint16))[:-2],      # 少了資料
    image.HEADER.pack(image.MAGIC, 2, 2, 12, 0, 9, 0) + b"\x00" * 8,   # channel 超出範圍
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


def _rgb(planes, **options):
    from teststation.nodes.rsi3000 import isp
    return isp.compose_rgb(planes, [image.stat(p) for p in planes], isp.Options(**options))


def test_rgb_takes_r_mean_of_greens_and_b():
    rgb, _gains = _rgb(_planes((4095, 1600, 2400, 0)))     # G = 2000 → 125(>> 4)
    assert rgb.shape == (768, 1024, 3) and rgb.dtype == np.uint8
    assert rgb[0, 0].tolist() == [255, 125, 0]


def test_rgb_auto_contrast_stretches_each_colour():
    planes = _planes((100, 200, 200, 300))
    for p, bright in zip(planes, (110, 210, 210, 310)):
        p[::4, ::4] = bright
    rgb, _gains = _rgb(planes, auto_contrast=True)
    for colour in range(3):
        assert (rgb[..., colour].min(), rgb[..., colour].max()) == (0, 255)


def test_awb_makes_a_grey_scene_grey():
    rgb, gains = _rgb(_planes((1000, 2000, 2000, 1600)), awb=True)
    assert (gains.r, gains.g, gains.b) == pytest.approx((2.0, 1.0, 1.25))
    r, g, b = rgb[0, 0].tolist()
    assert r == g == b == 125


def test_awb_ignores_saturated_pixels():
    planes = _planes((1000, 2000, 2000, 1600))
    planes[0][:100] = 4095                     # 一塊過曝:不該把 R 的增益拉低
    _rgb_image, gains = _rgb(planes, awb=True)
    assert gains.r == pytest.approx(2.0)


def test_awb_with_auto_contrast_keeps_the_balance():
    """AWB 開時 Auto-Contrast 三色共用範圍 —— 各自拉伸會把剛調好的比例洗掉。"""
    planes = _planes((1000, 2000, 2000, 1600))
    for p in planes:
        p[::4, ::4] = p[::4, ::4] * 6 // 5     # 亮一點的灰:三色同比例
    rgb, _gains = _rgb(planes, awb=True, auto_contrast=True)
    for pixel in (rgb[0, 0], rgb[0, 1]):         # 亮的灰、暗的灰
        assert pixel[0] == pixel[1] == pixel[2]
    assert (rgb[0, 0, 0], rgb[0, 1, 0]) == (255, 0)


def _ae(values, current=10_000.0, saturate_rows=0):
    from teststation.nodes.rsi3000 import isp
    planes = _planes(values)
    for p in planes:
        p[:saturate_rows] = 4095
    return isp.ae_step(current, planes)


def test_ae_brightens_a_dark_frame_in_proportion():
    step = _ae((1000,) * 4)                       # 目標 1500 → × 1.5
    assert step.exposure_us == pytest.approx(15_000.0)


def test_ae_darkens_a_bright_frame():
    step = _ae((3000,) * 4)
    assert step.exposure_us == pytest.approx(5_000.0)


def test_ae_steps_are_limited_to_two_times():
    assert _ae((100,) * 4).exposure_us == pytest.approx(20_000.0)


def test_ae_leaves_a_frame_on_target_alone():
    step = _ae((1520,) * 4)                       # ±5% 內
    assert step.exposure_us is None
    assert step.mean == pytest.approx(1520)


def test_a_small_bright_region_does_not_drag_the_exposure_down():
    """實機:角落一塊燈光一直飽和(> 1%),以前 AE 因此一路壓暗到平均 600 DN 再來回擺盪。

    測光只看沒飽和的像素,所以背景 1000 DN → 照樣 × 1.5。
    """
    step = _ae((1000,) * 4, saturate_rows=100)    # 100 / 1536 列 ≈ 6.5% 飽和
    assert step.saturated > 0.01
    assert step.mean == pytest.approx(1000)
    assert step.exposure_us == pytest.approx(15_000.0)


def test_mostly_overexposed_wins_over_the_mean():
    """大半張都飽和(> 20%):不管沒飽和的那些多暗,先減半。"""
    step = _ae((500,) * 4, saturate_rows=400)     # 400 / 1536 列 ≈ 26% 飽和
    assert step.saturated > 0.2
    assert step.exposure_us == pytest.approx(5_000.0)


def test_ae_stops_at_the_32_bit_tick_limit():
    from teststation.nodes.rsi3000 import isp
    assert _ae((100,) * 4, current=isp.AE_MAX_US).exposure_us is None


def test_awb_off_leaves_gains_at_one():
    _rgb_image, gains = _rgb(_planes((1000, 2000, 2000, 1600)))
    assert (gains.r, gains.g, gains.b) == (1.0, 1.0, 1.0)


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


# --------------------------------------------- GUI 的 AE:一輪只調一次

@pytest.fixture
def window():
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
    from teststation.nodes.rsi3000.gui import Rsi3000Window

    class FakeLink(QObject):
        frame = Signal(object)
        raw = Signal(str)
        status = Signal(str)
        reply = Signal(str, str)
        node = "RSI3000/COM98"

        def __init__(self):
            super().__init__()
            self.sent: list[str] = []

        def call(self, *lines):
            self.sent.extend(lines)

        def close(self):
            pass

    _app = QApplication.instance() or QApplication([])
    link = FakeLink()
    win = Rsi3000Window(link)
    yield win, link
    win.close()


def _feed(win, index, value, captured=True):
    for channel in range(4):
        plane = np.full((image.CHANNEL_HEIGHT, image.CHANNEL_WIDTH), value, np.uint16)
        win._on_frame(image.Frame(channel, index, plane, captured))


def test_ae_adjusts_once_per_capture_not_once_per_frame(window):
    """實機:scnf 10 一輪裡中途送的 sce 不會生效,10 張都一樣亮。

    以前 AE 每張都拿欄位上的值再減半,一輪就從 250 ms 掉到 1 ms,下一輪嚴重欠曝。
    """
    win, link = window
    win._exposure.setValue(250_000)
    win._processing["ae"].setChecked(True)
    win._capture()
    for index in range(10):
        _feed(win, index, 3855)                  # 同一輪,亮度不變
    exposures = [line for line in link.sent if line.startswith("set_exposure")]
    assert exposures == ["set_exposure 250000.00",      # 開 AE:先對齊起點
                         "set_exposure 125000.00"]      # 這一輪只調一次
    assert win.exposure_us() == pytest.approx(125_000)


def test_the_next_capture_starts_from_what_ae_sent(window):
    win, link = window
    win._exposure.setValue(250_000)
    win._processing["ae"].setChecked(True)
    win._capture()
    _feed(win, 0, 3855)                          # 過亮 → 125 ms
    win._capture()
    _feed(win, 1, 2000)                          # 用 125 ms 拍的:1500 / 2000 → 93.75 ms
    exposures = [line for line in link.sent if line.startswith("set_exposure")]
    assert exposures == ["set_exposure 250000.00", "set_exposure 125000.00",
                         "set_exposure 93750.00"]


def test_turning_ae_on_sends_the_starting_exposure(window):
    """GUI 剛開時不知道裝置上的曝光;開 AE 先把欄位上的值送過去,起點才一致。"""
    win, link = window
    win._exposure.setValue(10_000)
    win._processing["ae"].setChecked(True)
    assert link.sent == ["set_exposure 10000.00"]


def test_ae_skips_frames_that_are_not_captures(window):
    win, link = window
    win._processing["ae"].setChecked(True)
    link.sent.clear()
    _feed(win, 0, 340, captured=False)           # 開 sensor 的自我測試
    assert not [line for line in link.sent if line.startswith("set_exposure")]


# ----------------------------------------------- 重複啟動:新的接手

def test_a_port_held_by_another_program_is_left_alone(monkeypatch):
    from teststation.nodes.rsi3000 import node as rsi
    blocker = socket.create_server(("127.0.0.1", 0))
    port = blocker.getsockname()[1]
    monkeypatch.setattr(rsi, "port_owner", lambda _port: rsi.PortOwner(
        1234, "rsi3000_bridge.exe", "rsi3000_bridge.exe COM6", 1, "explorer.exe"))
    killed = []
    monkeypatch.setattr(rsi.subprocess, "run", lambda *a, **k: killed.append(a))
    with pytest.raises(OSError, match="rsi3000_bridge.exe"):
        rsi.listen("127.0.0.1", port)
    assert not killed
    blocker.close()


def test_a_previous_rsi3000_node_is_stopped_and_replaced(monkeypatch):
    from teststation.nodes.rsi3000 import node as rsi
    blocker = socket.create_server(("127.0.0.1", 0))
    port = blocker.getsockname()[1]
    monkeypatch.setattr(rsi, "port_owner", lambda _port: rsi.PortOwner(
        222, "python3.13.exe", f"python -m {rsi.MODULE} --port COM9",
        111, f".venv\\Scripts\\python.exe -m {rsi.MODULE} --port COM9"))
    killed = []

    def fake_taskkill(args, **_kwargs):
        killed.append(args)
        blocker.close()            # 舊的 node 死了,port 放出來

    monkeypatch.setattr(rsi.subprocess, "run", fake_taskkill)
    listener = rsi.listen("127.0.0.1", port, log=lambda _line: None)
    assert killed == [["taskkill", "/PID", "111", "/T", "/F"]]   # 連啟動器整棵收掉
    assert listener.getsockname()[1] == port
    listener.close()


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
        assert frames[channel].captured is False      # 沒送過 tfc:不算拍的
    for sub in subs:
        sub.remove_receive_callback()


def test_frames_after_tfc_are_marked_as_captures(node):
    """scnf 2 + tfc:接下來兩張是拍的,第三張(例如開 sensor 的自我測試)不是。"""
    captured: dict[int, bool] = {}
    sub = ecal.Subscriber(f"{node.prefix}/image_R")
    sub.set_receive_callback(lambda _t, _dt, data, *_: captured.setdefault(
        image.decode(data.buffer).index, image.decode(data.buffer).captured))
    time.sleep(1.5)

    node.fake.feed(b"> ")
    assert _call(node, "set_frames 2").startswith("OK")
    assert _call(node, "capture").startswith("OK")
    node.fake.feed(b"> ")
    assert _wait_for(lambda: b"tfc\r\n" in node.fake.written)

    packed = pack12(np.zeros((image.FULL_HEIGHT, image.FULL_WIDTH), np.uint16))
    before = len(captured)
    for _ in range(3):
        with socket.create_connection(node._listener.getsockname()) as conn:
            conn.sendall(packed)
        count = len(captured)
        assert _wait_for(lambda: len(captured) > count, timeout=15)
    newest = [captured[i] for i in sorted(captured)][before:]
    assert newest == [True, True, False]
    sub.remove_receive_callback()
