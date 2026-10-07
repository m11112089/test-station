"""RSI3000E console 命令與單位換算。

本模組**不碰 I/O**,只做「數值 → console 命令字串」,方便單獨測試。

裝置的時間單位是 tick = 10 ns:

* 曝光:µs × 100 → `sce l <hex>`
* 間隔:由 fps 換算成一個 frame 的 tick 數 1e8 / fps → `scft * <hex>`

兩者都只送 low32(`l` / `*`),所以上限是 0xFFFFFFFF tick ≈ 42.9 秒。
更長要另送 high32(`h`),目前不支援,超過就直接 ERR,不偷偷截斷。
"""

from __future__ import annotations

DEVICE = "RSI3000"
"""service 與 topic 的第一段。node 與 GUI 都用它。"""

TICKS_PER_US = 100
TICKS_PER_SECOND = 100_000_000
MAX_TICKS = 0xFFFFFFFF

PROMPT = b">"
"""console 的提示符。看到它表示上一條命令處理完,可以送下一條。"""

SENSOR_ON = "csp 1"
SENSOR_OFF = "csp 0"
CAPTURE = "tfc"


def _checked(ticks: int, what: str) -> int:
    if ticks <= 0:
        raise ValueError(f"{what} must be positive")
    if ticks > MAX_TICKS:
        raise ValueError(f"{what} exceeds 32-bit ticks (max {MAX_TICKS / TICKS_PER_SECOND:.1f} s)")
    return ticks


def exposure_ticks(exposure_us: float) -> int:
    return _checked(round(exposure_us * TICKS_PER_US), "exposure")


def frame_ticks(fps: float) -> int:
    if fps <= 0:
        raise ValueError("fps must be positive")
    return _checked(round(TICKS_PER_SECOND / fps), "frame interval")


def set_frames(count: int) -> str:
    if count <= 0:
        raise ValueError("frames must be positive")
    return f"scnf {count}"


def set_exposure(exposure_us: float) -> str:
    return f"sce l {exposure_ticks(exposure_us):X}"


def set_frame_rate(fps: float) -> str:
    return f"scft * {frame_ticks(fps):X}"


class LineSplitter:
    """把 console 的位元組流切成行,供 `raw` topic 一行一則發佈。

    提示符 `>` 後面不會有換行,所以另有 `flush()`:一段時間沒新資料時,
    把半行(通常就是提示符)也送出去,不然畫面上永遠看不到它。
    """

    def __init__(self) -> None:
        self._pending = bytearray()

    def feed(self, data: bytes) -> list[str]:
        self._pending.extend(data)
        lines = []
        while (end := self._pending.find(b"\n")) >= 0:
            lines.append(self._decode(self._pending[:end]))
            del self._pending[:end + 1]
        return lines

    def flush(self) -> str | None:
        if not self._pending:
            return None
        line = self._decode(self._pending)
        self._pending.clear()
        return line

    @staticmethod
    def _decode(raw: bytes | bytearray) -> str:
        return bytes(raw).decode("utf-8", "replace").rstrip("\r")
