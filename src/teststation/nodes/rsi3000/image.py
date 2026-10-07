"""RSI3000E 影像格式:TCP 收到的 packed 12-bit → 四個 Bayer 通道。

本模組**不碰 I/O 也不碰 eCAL**,方便單獨測試。

線上格式(一個 TCP 連線 = 一張):raster 掃描的 RGGB mosaic,4096×3072,
每個 sample 12 bit、2 個 sample 緊密打包成 3 bytes(lsb 排法),共 18,874,368 bytes::

    列 0 = R,  Gr, R,  Gr, …
    列 1 = Gb, B,  Gb, B,  …

依列 / 行奇偶拆成 R / Gr / Gb / B 四個 2048×1536 平面,值仍是**右對齊的 12-bit 原值**
(0–4095,沒有任何位移或縮放)。每個通道各發一則訊息:24-byte header + 16-bit 平面,
header 與原本 C++ 版 `img::FrameHeader` 相同,所以兩邊的訂閱端可以互通。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

FULL_WIDTH = 4096
FULL_HEIGHT = 3072
CHANNEL_WIDTH = FULL_WIDTH // 2
CHANNEL_HEIGHT = FULL_HEIGHT // 2
BITS_VALID = 12
PACKED_FRAME_BYTES = FULL_WIDTH * FULL_HEIGHT * 3 // 2

CHANNELS = ("R", "Gr", "Gb", "B")
"""通道順序 = header 的 channel 編號 = 在 mosaic 裡的 (列奇偶, 行奇偶)。"""

MAGIC = 0x474D4945  # "EIMG"
HEADER = struct.Struct("<6I")
"""magic, width, height, bits_valid, channel, frame_index —— 全部 little-endian uint32。"""


def unpack12(packed: bytes, width: int = FULL_WIDTH, height: int = FULL_HEIGHT) -> np.ndarray:
    """packed 12-bit(lsb)→ uint16 mosaic,shape (height, width)。大小不對丟 ValueError。"""
    expected = width * height * 3 // 2
    if len(packed) != expected:
        raise ValueError(f"frame is {len(packed)} bytes, expected {expected}")
    raw = np.frombuffer(packed, dtype=np.uint8).reshape(-1, 3).astype(np.uint16)
    out = np.empty(raw.shape[0] * 2, dtype=np.uint16)
    out[0::2] = raw[:, 0] | ((raw[:, 1] & 0x0F) << 8)
    out[1::2] = (raw[:, 1] >> 4) | (raw[:, 2] << 4)
    return out.reshape(height, width)


def split_bayer(mosaic: np.ndarray) -> list[np.ndarray]:
    """RGGB mosaic → [R, Gr, Gb, B],各自是連續記憶體的平面。"""
    return [np.ascontiguousarray(mosaic[row::2, col::2])
            for row, col in ((0, 0), (0, 1), (1, 0), (1, 1))]


def encode(channel: int, frame_index: int, plane: np.ndarray) -> bytes:
    height, width = plane.shape
    header = HEADER.pack(MAGIC, width, height, BITS_VALID, channel, frame_index)
    return header + plane.astype("<u2", copy=False).tobytes()


@dataclass(frozen=True)
class Frame:
    channel: int
    index: int
    plane: np.ndarray
    """uint16,shape (height, width),右對齊的 12-bit 原值。"""


def decode(message: bytes) -> Frame | None:
    """一則通道訊息 → Frame;格式不對回 None(訂閱端直接略過)。"""
    if len(message) < HEADER.size:
        return None
    magic, width, height, _bits, channel, index = HEADER.unpack_from(message)
    if magic != MAGIC or channel >= len(CHANNELS):
        return None
    if len(message) != HEADER.size + width * height * 2:
        return None
    plane = np.frombuffer(message, dtype="<u2", offset=HEADER.size).reshape(height, width)
    return Frame(channel, index, plane)


@dataclass(frozen=True)
class Stat:
    min: int
    max: int
    mean: float
    std: float


def stat(plane: np.ndarray) -> Stat:
    """原始 DN 的統計。顯示怎麼縮放都不影響這裡。"""
    return Stat(int(plane.min()), int(plane.max()), float(plane.mean()), float(plane.std()))
