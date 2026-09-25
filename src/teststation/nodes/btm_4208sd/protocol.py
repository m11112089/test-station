"""BTM-4208SD 16 字元封包解析(手冊 10. RS232 PC SERIAL INTERFACE,p.22–24)。

本模組**不碰 I/O**,只做 bytes → Reading,方便單獨測試。

RS232 FORMAT:9600, N, 8, 1。儀器**單向連續輸出**,不接受任何命令。

封包 16 字元,線上順序由 D15 先送到 D0::

    D15 D14 D13 D12 D11 D10 D9 D8 D7 D6 D5 D4 D3 D2 D1 D0

    D15      Start Word(手冊未給定值;實機量到 0x02 STX,本實作不驗證)
    D14      固定 '4'
    D13      channel:'1'–'9' = CH1–CH9,'A'/'B'/'C' = CH10/CH11/CH12
    D12 D11  單位:'01' = °C,'02' = °F
    D10      正負號:'0' 正,'1' 負
    D9       小數點位置(由右數):'0'–'3'
    D8…D1    讀值,D8 = MSD、D1 = LSD(例:顯示 1234 → "00001234")
    D0       End Word(手冊未給定值;實機量到 0x0D CR,本實作不驗證)

手冊沒有給 Start/End Word 的實際值,所以這裡**不靠 delimiter 切封包**,
改用「16 位元組視窗 + 結構檢查」對齊(見 FrameReader),對齊後起訖字元是什麼都不影響。
實機雖然是 STX/CR,但不寫死,免得換韌體或換機就解不出來。
"""

from __future__ import annotations

from dataclasses import dataclass

FRAME_SIZE = 16

CHANNEL_DIGITS = "123456789ABC"
"""D13 的取值,索引 + 1 即 channel 編號(CH1–CH12)。"""

UNITS = {"01": "°C", "02": "°F"}
"""D12 D11。"""

OVER_RANGE_CHARS = {"\x18", " "}
"""沒插探棒(over range)時,D8…D1 裡出現的填充字元。

手冊沒有描述 over range 的編碼。實機(韌體 0912-BTM4208SD)量到的是把顯示用的
低 4 位填成 ``0x18``(CAN),高 4 位仍是 '0',整個讀值欄位長這樣::

    b'\\x024501010000\\x18\\x18\\x18\\x18\\r'   # CH5 沒插探棒
                  ^^^^ ^^^^^^^^^^^^^^^^
                  高4位 低4位 = 0x18

所以判定條件是「D8…D1 裡出現任一個填充字元」,而不是「全部都是」。
空白是為了別家韌體留的餘裕;若你的機器又是別的編碼,用 ``btm_4208sd --sniff``
看原始封包後把字元加進這個集合即可。
"""

MAX_DECIMALS = 3


@dataclass(frozen=True)
class Reading:
    """一個 channel 的一筆讀值。"""

    channel: int
    """1–12。"""

    unit: str
    """``°C`` 或 ``°F``。"""

    value: float | None
    """None = over range(沒插探棒),node 不會 publish 這筆。"""

    @property
    def over_range(self) -> bool:
        return self.value is None

    @property
    def topic_channel(self) -> str:
        """Topic 的最後一段:``ch1`` … ``ch12``。"""
        return f"ch{self.channel}"


def parse_frame(frame: bytes | str) -> Reading | None:
    """解析剛好 16 個字元的封包;不符合格式回傳 None(呼叫端據此重新對齊)。"""
    if len(frame) != FRAME_SIZE:
        return None
    if isinstance(frame, bytes):
        try:
            text = frame.decode("ascii")
        except UnicodeDecodeError:
            return None
    else:
        text = frame

    # text[0] = D15、text[15] = D0。
    if text[1] != "4":
        return None

    position = CHANNEL_DIGITS.find(text[2])
    if position < 0:
        return None
    channel = position + 1

    unit = UNITS.get(text[3:5])
    if unit is None:
        return None

    polarity = text[5]
    if polarity not in "01":
        return None

    decimals = text[6]
    if not decimals.isdigit() or int(decimals) > MAX_DECIMALS:
        return None

    digits = text[7:15]
    if digits.isdigit():
        magnitude = int(digits) / (10 ** int(decimals))
        value = -magnitude if polarity == "1" else magnitude
    elif set(digits) & OVER_RANGE_CHARS and set(digits) <= OVER_RANGE_CHARS | set("0123456789"):
        value = None  # over range:沒插探棒
    else:
        return None

    return Reading(channel=channel, unit=unit, value=value)


class FrameReader:
    """把連續的 byte 串切成封包,並在雜訊或半個封包之後自動重新對齊。

    儀器是單向連續輸出,node 隨時可能從封包中間開始收,所以用結構檢查對齊:
    看 16 位元組的視窗,解得出來就吃掉 16 個,解不出來就丟 1 個再試。
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[Reading]:
        """餵進剛收到的位元組,回傳這次湊齊的所有讀值(可能是空的)。"""
        self._buffer.extend(data)
        readings: list[Reading] = []
        while len(self._buffer) >= FRAME_SIZE:
            reading = parse_frame(bytes(self._buffer[:FRAME_SIZE]))
            if reading is None:
                del self._buffer[0]
                continue
            del self._buffer[:FRAME_SIZE]
            readings.append(reading)
        return readings

    @property
    def pending(self) -> int:
        """還沒湊成封包的位元組數,恆 < 16。"""
        return len(self._buffer)


def looks_like_btm(data: bytes, *, min_frames: int = 2) -> bool:
    """掃描時的辨識依據:這段資料裡有沒有至少 ``min_frames`` 個合格封包。

    要求兩個封包是因為只靠結構檢查,單一封包仍有極小機率誤判別台裝置的輸出。
    """
    return len(FrameReader().feed(data)) >= min_frames
