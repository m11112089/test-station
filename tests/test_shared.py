"""共用層的測試:`core.topics` 的契約判斷式、`client` 的純函式。

這個檔存在的理由是**釘住「只有一份實作」**。這些東西曾經在 node、CLI、UI
各寫一份,而且寫得不一樣;誰想再抄一份,這裡的測試不會攔他,但至少
下一個人看得到正確的那份在哪。
"""

from __future__ import annotations

import pytest

from teststation import client as common
from teststation.core import topics


# ----------------------------------------------- 兩個判斷式,不是一個

@pytest.mark.parametrize("topic", [
    "BTM-4208SD/COM11/status",
    "Log/status",
    "Serial/COM5/activity",
])
def test_common_topics_are_never_data(topic):
    assert topics.is_common(topic)
    assert not topics.is_measurement_topic(topic)


def test_raw_is_not_a_measurement_but_is_not_common_either():
    """這個區別很重要:Log 要訂 raw(寫 console.log),但它不是量測值。

    把兩件事合成一個判斷式會弄壞其中一邊 —— 這正是先前兩處定義不一致的原因。
    """
    assert topics.is_raw("Serial/COM5/raw")
    assert not topics.is_common("Serial/COM5/raw")      # → Log 會訂它
    assert not topics.is_measurement_topic("Serial/COM5/raw")   # → UI 不當資料


@pytest.mark.parametrize("topic", [
    "BTM-4208SD/COM11/ch1",
    "PFR-100L/COM20/voltage",
    "SSH/192_168_1_50/soc_temp",
])
def test_measurement_topics(topic):
    assert topics.is_measurement_topic(topic)


def test_channel_is_the_last_segment():
    assert topics.channel_of("SSH/192_168_1_50/soc_temp") == "soc_temp"


# ------------------------------------------ 「有 v 才是量測值」只有一份

def test_payload_needs_v_to_be_a_measurement():
    assert topics.is_measurement({"t": "…", "v": 1.0, "unit": "°C"})
    assert not topics.is_measurement({"t": "…", "recording": True})   # Log/state
    assert not topics.is_measurement({"t": "…", "kind": "PUB"})       # activity
    assert not topics.is_measurement("ok")                            # status
    assert not topics.is_measurement([1, 2, 3])


# --------------------------------------------------------- 頻率統計

def test_rate_of_evenly_spaced_samples():
    rate = common.rate_of([0.0, 0.5, 1.0, 1.5])
    assert rate.hz == pytest.approx(2.0)
    assert rate.samples == 4
    assert rate.std == pytest.approx(0.0)


def test_rate_of_uneven_samples_reports_the_spread():
    rate = common.rate_of([0.0, 1.0, 1.1, 3.0])
    assert rate.min_gap == pytest.approx(0.1)
    assert rate.max_gap == pytest.approx(1.9)
    assert rate.std > 0


def test_rate_needs_two_samples():
    assert common.rate_of([]) is None
    assert common.rate_of([1.0]) is None


def test_rate_survives_identical_timestamps():
    """時鐘解析度不夠時可能收到完全一樣的時刻,不該除以零。"""
    assert common.rate_of([1.0, 1.0]).hz == 0.0


# ------------------------------------------------------- payload 準備

def test_missing_timestamp_is_filled_in():
    """所以 `{"v": 1.0}` 直接就符合 README 2.2 的 schema。"""
    message = common.prepare_payload('{"v": 1.0}')
    assert message["v"] == 1.0
    assert "t" in message


def test_existing_timestamp_is_kept():
    message = common.prepare_payload('{"t": "2026-01-01 00:00:00.000", "v": 1}')
    assert message["t"] == "2026-01-01 00:00:00.000"


def test_bad_json_raises_value_error():
    with pytest.raises(ValueError):
        common.prepare_payload("not json")


def test_a_json_array_is_not_a_payload():
    with pytest.raises(ValueError):
        common.prepare_payload("[1, 2, 3]")


# ------------------------------------------ 純邏輯模組不該拖進 eCAL

PURE_MODULES = [
    "teststation.core.topics",
    "teststation.core.describe",
    "teststation.core.payload",
    "teststation.nodes.btm_4208sd.protocol",
    "teststation.nodes.log.sync",
    "teststation.nodes.log.report",
    "teststation.visualization",
]


@pytest.mark.parametrize("module", PURE_MODULES)
def test_pure_modules_import_without_ecal(module):
    """這些模組不跟站台說話,匯入它們不該載入 eCAL。

    曾經不成立:package 的 `__init__.py` 轉出 node 類別,於是匯入任何子模組都會
    先跑父 package,把 eCAL 一起拖進來 —— 文件寫著「不碰 eCAL」,實際上碰了。
    所以這裡開一個乾淨的直譯器來看。
    """
    import subprocess
    import sys

    code = (
        f"import sys, importlib; importlib.import_module({module!r}); "
        "print(any('ecal' in m for m in sys.modules))"
    )
    result = subprocess.run([sys.executable, "-c", code],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", f"{module} 把 eCAL 拖進來了"
