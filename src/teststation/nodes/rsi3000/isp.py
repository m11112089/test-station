"""RGB 疊圖的影像處理(ISP)。**只給畫面用**,不碰原始平面、不影響統計。

本模組不碰 Qt 也不碰 eCAL,方便單獨測試。處理順序固定:

    四通道 → RGB(線性 12-bit)→ AWB → (之後:CCM …)→ 轉 8-bit 顯示

每個步驟都是可以開關的;GUI 的「影像處理」區塊照 `Options` 的欄位長出按鈕。

AE(`ae_step`)也在這裡,但它不是畫面處理:它依前一張影像算下一次的曝光,
GUI 會真的把那個值送給裝置。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .image import Stat
from .protocol import MAX_TICKS, TICKS_PER_US

FULL_SCALE = 4095
SATURATED = FULL_SCALE
"""到這個值就是飽和:它的顏色已經失真,不拿來估白平衡。"""

AE_TARGET_DN = 1500.0
"""AE 要把「沒飽和的像素」的平均(四通道,原始 DN)拉到多少。約 37% 滿刻度。"""

AE_TOLERANCE = 0.05
"""平均落在目標 ±5% 內就不動,免得每張都送一次命令。"""

AE_MAX_SATURATED = 0.20
"""飽和像素超過 20%(大半張都過曝)才算過曝:不管平均多少,先往下調。

曾經是 1%,實機上因此卡死:畫面角落一塊燈光在 0.6 ms 還是飽和、面積超過 1%,
AE 被它一路往下壓到平均只剩 600 DN,再被平均拉回去,在 0.6–1.2 ms 之間來回。
燈、反光這類小亮區本來就該飽和,不該拖暗整張。
"""

AE_OVEREXPOSED_STEP = 0.5
"""過曝時至少往下調到原本的一半。"""

AE_MAX_STEP = 2.0
"""一次最多調 2 倍(或 1/2)。下一張可能還是舊曝光拍的,步子太大會來回震盪。"""

AE_MIN_US = 10.0
AE_MAX_US = MAX_TICKS / TICKS_PER_US
"""裝置只收 32-bit tick,約 42.9 秒。"""


@dataclass(frozen=True)
class Options:
    auto_contrast: bool = False
    awb: bool = False


@dataclass(frozen=True)
class Gains:
    """各色乘上的增益。G 固定為 1,只調 R 與 B。"""

    r: float = 1.0
    g: float = 1.0
    b: float = 1.0


@dataclass(frozen=True)
class AeStep:
    mean: float
    """這張「沒飽和的像素」的平均(DN)—— AE 測光用的亮度。"""

    saturated: float
    """飽和像素的比例,0–1。"""

    exposure_us: float | None
    """下一張要用的曝光;None = 不用調(已在目標範圍內,或已經頂到上下限)。"""


def ae_step(current_us: float, planes: list[np.ndarray],
            target: float = AE_TARGET_DN) -> AeStep:
    """依前一張影像算下一次的曝光。

    測光用「沒飽和的像素」的平均:飽和的地方已經量不出亮度,算進去只會被小亮區拉偏。
    亮度跟曝光近似成正比,所以下一次 = 現在 × 目標 / 平均,再限制單步幅度。
    大半張都過曝(飽和超過 20%)時優先往下調。
    註:沒有扣黑電平,平均裡含著底噪,收斂點會比目標略暗一點。
    """
    sample = np.concatenate([p[::4, ::4].ravel() for p in planes])
    clipped = sample >= SATURATED
    saturated = float(clipped.mean())
    unclipped = sample[~clipped]
    mean = float(unclipped.mean()) if unclipped.size else float(FULL_SCALE)

    ratio = target / max(mean, 1.0)
    overexposed = saturated > AE_MAX_SATURATED
    if overexposed:
        ratio = min(ratio, AE_OVEREXPOSED_STEP)
    elif abs(ratio - 1.0) <= AE_TOLERANCE:
        return AeStep(mean, saturated, None)
    ratio = min(max(ratio, 1.0 / AE_MAX_STEP), AE_MAX_STEP)

    proposed = min(max(current_us * ratio, AE_MIN_US), AE_MAX_US)
    if abs(proposed - current_us) <= current_us * 1e-3:
        return AeStep(mean, saturated, None)        # 已經頂到上下限
    return AeStep(mean, saturated, proposed)


def to_8bit(sample: np.ndarray, low: float, high: float, auto_contrast: bool) -> np.ndarray:
    """12-bit → 8-bit 顯示值:固定 `>> 4`,或 Auto-Contrast 時把 [low, high] 拉伸到 0–255。"""
    if not auto_contrast:
        return (np.clip(sample, 0, FULL_SCALE).astype(np.uint16) >> 4).astype(np.uint8)
    span = max(1.0, float(high) - float(low))
    stretched = (sample.astype(np.float32) - float(low)) * (255.0 / span)
    return stretched.clip(0, 255).astype(np.uint8)


def awb_gains(r: np.ndarray, g: np.ndarray, b: np.ndarray) -> Gains:
    """Gray world:假設畫面平均起來是灰的,把 R、B 的平均拉到跟 G 一樣。

    三色任一飽和的像素不算 —— 飽和處的顏色比例已經壞了。全部都飽和或某色全黑時不調。
    """
    valid = (r < SATURATED) & (g < SATURATED) & (b < SATURATED)
    if not valid.any():
        return Gains()
    means = [float(c[valid].mean()) for c in (r, g, b)]
    if min(means) <= 0:
        return Gains()
    return Gains(r=means[1] / means[0], g=1.0, b=means[1] / means[2])


def compose_rgb(planes: list[np.ndarray], stats: list[Stat], options: Options,
                downscale: int = 2) -> tuple[np.ndarray, Gains]:
    """四通道 → (H, W, 3) uint8 的 RGB,以及這次用到的白平衡增益。

    R、G = (Gr + Gb) / 2、B,各縮小 `downscale` 倍。每個 Bayer quad 剛好對應一個像素,不內插。

    Auto-Contrast 的拉伸方式取決於 AWB:
    * AWB 關:三色各自拉伸(等於一個很粗的白平衡,這是原本的行為)
    * AWB 開:三色共用同一個範圍 —— 各自拉伸會把剛調好的顏色比例又洗掉
    """
    r, gr, gb, b = (plane[::downscale, ::downscale] for plane in planes)
    g = (gr.astype(np.float32) + gb) / 2
    r = r.astype(np.float32)
    b = b.astype(np.float32)

    gains = Gains()
    if options.awb:
        gains = awb_gains(r, g, b)
        r = r * gains.r
        b = b * gains.b
        if options.auto_contrast:
            low = min(float(c.min()) for c in (r, g, b))
            high = max(float(c.max()) for c in (r, g, b))
            return np.dstack([to_8bit(c, low, high, True) for c in (r, g, b)]), gains
        return np.dstack([to_8bit(c, 0, 0, False) for c in (r, g, b)]), gains

    s_r, s_gr, s_gb, s_b = stats
    ranges = [(s_r.min, s_r.max),
              (min(s_gr.min, s_gb.min), max(s_gr.max, s_gb.max)),
              (s_b.min, s_b.max)]
    return np.dstack([to_8bit(c, low, high, options.auto_contrast)
                      for c, (low, high) in zip((r, g, b), ranges)]), gains
