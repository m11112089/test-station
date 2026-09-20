"""GUI 測試要在沒有螢幕的環境跑,所以在 Qt 載入前就把平台設成 offscreen。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
