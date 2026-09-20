"""BTM-4208SD 封包解析的測試(手冊 p.22–24 的欄位定義)。"""

from __future__ import annotations

import pytest

from teststation.nodes.btm_4208sd.protocol import (
    FrameReader,
    looks_like_btm,
    parse_frame,
)

START = "\x02"
END = "\r"


def frame(channel="1", unit="01", polarity="0", decimals="1", digits="00000250",
          start=START, end=END) -> bytes:
    """組一個 16 字元封包:D15 D14 D13 D12 D11 D10 D9 D8…D1 D0。"""
    text = start + "4" + channel + unit + polarity + decimals + digits + end
    assert len(text) == 16, len(text)
    return text.encode("ascii")


# ------------------------------------------------------------------ 正常讀值

def test_positive_celsius_one_decimal():
    reading = parse_frame(frame(digits="00000250", decimals="1"))
    assert reading.channel == 1
    assert reading.value == pytest.approx(25.0)
    assert reading.unit == "°C"
    assert reading.topic_channel == "ch1"
    assert not reading.over_range


def test_negative_polarity():
    reading = parse_frame(frame(polarity="1", digits="00000123", decimals="1"))
    assert reading.value == pytest.approx(-12.3)


@pytest.mark.parametrize("decimals,digits,expected", [
    ("0", "00001234", 1234.0),
    ("1", "00001234", 123.4),
    ("2", "00001234", 12.34),
    ("3", "00001234", 1.234),
])
def test_decimal_point_position(decimals, digits, expected):
    assert parse_frame(frame(decimals=decimals, digits=digits)).value == pytest.approx(expected)


def test_fahrenheit_unit():
    assert parse_frame(frame(unit="02")).unit == "°F"


@pytest.mark.parametrize("digit,channel", [
    ("1", 1), ("9", 9), ("A", 10), ("B", 11), ("C", 12),
])
def test_channel_digits(digit, channel):
    reading = parse_frame(frame(channel=digit))
    assert reading.channel == channel
    assert reading.topic_channel == f"ch{channel}"


def test_start_and_end_word_are_not_validated():
    """手冊沒有給 Start/End Word 的值,所以解析不依賴它們。"""
    reading = parse_frame(frame(start="X", end="Y"))
    assert reading is not None
    assert reading.value == pytest.approx(25.0)


# ------------------------------------------------------------------ over range

def test_over_range_has_no_value():
    """實機沒插探棒時送 '0000' + 四個 0x18。"""
    reading = parse_frame(frame(digits="0000" + "\x18" * 4))
    assert reading.over_range
    assert reading.value is None
    assert reading.channel == 1


def test_over_range_tolerates_blank_fill():
    assert parse_frame(frame(digits=" " * 8)).over_range


# -------------------------------------------------------------- 不合格的封包

@pytest.mark.parametrize("bad", [
    frame()[:15],                      # 長度不足
    frame() + b"\x00",                 # 長度過長
    frame(channel="D"),                # channel 超出 1-9/A-C
    frame(unit="03"),                  # 單位不是 01/02
    frame(polarity="2"),               # 正負號只有 0/1
    frame(decimals="4"),               # 小數點最多 3 位
    frame(digits="0000A250"),          # 讀值夾雜非數字
    b"\x02" + b"5" + b"100000000250\r",  # D14 不是 '4'
])
def test_malformed_frames_are_rejected(bad):
    assert parse_frame(bad) is None


def test_non_ascii_frame_is_rejected():
    assert parse_frame(b"\xff" * 16) is None


# ------------------------------------------------------------ 串流切割與對齊

def test_reader_splits_back_to_back_frames():
    reader = FrameReader()
    readings = reader.feed(frame(channel="1") + frame(channel="2"))
    assert [r.channel for r in readings] == [1, 2]
    assert reader.pending == 0


def test_reader_resyncs_after_garbage():
    """從封包中間開始收也要能自己對齊。"""
    reader = FrameReader()
    readings = reader.feed(b"junk" + frame(channel="3") + frame(channel="4"))
    assert [r.channel for r in readings] == [3, 4]


def test_reader_handles_frames_split_across_reads():
    reader = FrameReader()
    whole = frame(channel="5")
    assert reader.feed(whole[:7]) == []
    assert reader.pending == 7
    readings = reader.feed(whole[7:])
    assert [r.channel for r in readings] == [5]


def test_reader_buffer_stays_bounded():
    reader = FrameReader()
    for _ in range(100):
        reader.feed(b"\x00" * 16)
    assert reader.pending < 16


# ---------------------------------------------------------------------- 辨識

def test_identify_needs_two_frames():
    """掃描時只聽不送:收到兩個合格封包才認定是這台。"""
    assert not looks_like_btm(frame())
    assert looks_like_btm(frame() + frame(channel="2"))


def test_identify_rejects_other_device_chatter():
    assert not looks_like_btm(b"PFR-100L,GW-INSTEK,1.02\r\n" * 4)


# ----------------------------------------------------- 實機擷取的回歸測試資料

CAPTURED = (
    b"\x0241010100000309\r"
    b"\x024501010000\x18\x18\x18\x18\r"
    b"\x024201010000\x18\x18\x18\x18\r"
    b"\x024601010000\x18\x18\x18\x18\r"
    b"\x0243010100000374\r"
    b"\x024701010000\x18\x18\x18\x18\r"
)
"""BTM-4208SD(韌體 0912-BTM4208SD)COM11 實測位元組:CH1/CH3 插探棒,其餘 over range。

注意儀器**不按 channel 順序**送,而且一輪固定送滿 12 個 channel。
"""


def test_captured_stream_from_real_meter():
    readings = FrameReader().feed(CAPTURED)
    assert len(readings) == 6
    assert [r.channel for r in readings] == [1, 5, 2, 6, 3, 7]

    with_value = {r.channel: r.value for r in readings if not r.over_range}
    assert with_value == {1: pytest.approx(30.9), 3: pytest.approx(37.4)}
    assert all(r.unit == "°C" for r in readings)

    over = [r.channel for r in readings if r.over_range]
    assert over == [5, 2, 6, 7]


def test_captured_stream_resyncs_from_mid_frame():
    """從封包中間開始收(node 隨時可能接上),仍要對齊到後面的封包。"""
    readings = FrameReader().feed(CAPTURED[9:])
    assert [r.channel for r in readings] == [5, 2, 6, 3, 7]
