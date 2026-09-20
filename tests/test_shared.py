"""共用層的測試:`core.topics` 的契約判斷式、`cli.common` 的純函式。

這個檔存在的理由是**釘住「只有一份實作」**。這些東西曾經在 node、`st`、UI
各寫一份,而且寫得不一樣;誰想再抄一份,這裡的測試不會攔他,但至少
下一個人看得到正確的那份在哪。
"""

from __future__ import annotations

import pytest

from teststation.cli import common
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
