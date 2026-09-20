# Log 與報告

站上唯一會寫檔的 node,以及它產出的互動式報告。

| | |
|---|---|
| 程式碼 | [src/teststation/nodes/log/](../src/teststation/nodes/log/) |
| 契約 | [INTERFACE.md](../src/teststation/nodes/log/INTERFACE.md) |

> **第一部分:怎麼用**(到「已知限制」為止)
> **[第二部分:程式碼規劃](#第二部分程式碼規劃)**

---

## 錄一次

```
uv run log                                    # 另一個終端機
st service call Log "start burn_in_45C"
st service call Log "mark DUT power on"
st service call Log "stop"
```

或在 UI 的 **Log 分頁**按 Start / Mark / Stop。兩邊送的是同一個字串,
所以 `st` 下的 `start`,UI 上一樣會亮起來 —— 狀態是 Log node 推的,不是各自猜的。

錄製中 UI 顯示:

```
● 錄製中 burn_in_45C
1523 筆 · 2 個 mark · 14 個 topic · 起於 2026-09-20 20:16:53.272
C:\...\logs\2026-09-20_201653_burn_in_45C
```

## 產出

`logs/YYYY-MM-DD_HHMMSS_<run_name>/`

| 檔案 | 內容 |
|---|---|
| `data.csv` | 每一筆量測值,照收到的順序 |
| `console.log` | DUT 的原始輸出 |
| `report.html` | 互動圖表,**離線可看** |

`data.csv` 長這樣 —— 各裝置各自的節奏,交錯在一起:

```csv
t,topic,name,value,unit
2026-09-20 20:12:38.824,BTM-4208SD/COM11/ch3,ch3,37.6,°C
2026-09-20 20:12:40.075,BTM-4208SD/COM11/ch1,ch1,31.5,°C
2026-09-20 20:12:42.838,Log/mark,DUT power on,,
2026-09-20 20:12:44.000,PFR-100L/COM20/voltage,voltage,12.0,V
```

**沒有對齊、沒有重取樣、沒有內插。** 熱電偶一個 channel 約 0.5 Hz、電源約 1 Hz,
硬湊成同一個網格只會製造出沒量過的數字。報告上每個點都是真的量到的值。

## 看報告

三條路,看到的是同一份檔案:

1. UI 的 **Log 分頁** → 選一次 run → **看報告**(開在 UI 的「報告」分頁裡)
2. 同一個位置 → **用瀏覽器開**
3. 直接點開 `logs/<run>/report.html`

報告是**自足的**:plotly 內嵌在裡面,所以可以複製到隨身碟、寄出去、在沒有網路的
機台上開 —— 都看得到圖。代價是每份約 4.6 MB。

報告上能做的事:拖曳框選一段時間看統計(筆數 / 最小 / 最大 / 平均 / 標準差)、
點圖例開關單一條線、用上方按鈕只看某一組、複製統計表。`mark` 畫成垂直虛線加標籤。

## 時區

log 裡的時間是**不帶時區的牆上時間**。報告刻意也用同樣的方式顯示,所以不管開報告的人
在哪個時區,看到的都是當初記錄的那個鐘面時間。

跨 PC 時所有機台必須 NTP 對時**且設為同一時區**(README 2.2)。

## 已知限制

- `console.log` 要等 Serial / SSH DUT node 做好才會有東西。
- 報告的 `bands`(區段底色)目前是空的 —— `mark` 畫成垂直線。要把成對的 mark
  變成區段還沒做。

---
---

# 第二部分:程式碼規劃

## 模組分工

| 模組 | 負責 | 不該出現在這裡的東西 |
|---|---|---|
| `node.py` | 訂閱、寫檔、`start`/`mark`/`stop`、狀態 | 任何圖表邏輯 |
| `sync.py` | 讀回 `data.csv` → series。**完全不碰 eCAL** | 任何 HTML |
| `report.py` | series → `DATA` → 灌進模板 | 任何 I/O 以外的統計 |
| `chart_template.html` | 畫圖、互動、區間統計 | 任何資料處理 |

`sync.py` 與 `report.py` 不 import eCAL,也不需要 node 在跑 —— 拿一份舊的
`data.csv` 就能重新產報告:

```python
from teststation.nodes.log import report
report.write(Path("logs/2026-09-20_201653_burn_in_45C"))
```

## 三個設計決定

**一、mark 寫進 `data.csv`,不另開檔案。**
用保留 topic `Log/mark`,`value` 與 `unit` 留空。這樣事件跟資料天然照時間排好,
而且 README 4 列的三個檔案維持不變。`sync.load()` 讀的時候再把它拆出來。

**二、「有沒有 `v`」就是量測值的判準。**
Log 收到 payload 沒有 `v` 就丟掉,UI 的即時數值表也是同一條規則。所以
`Log/state` 這種 node 自有的狀態 topic 自然被擋在外面,**不需要維護一份排除名單**。

**三、時間戳用發布端蓋的,不是 Log 收到的時間。**
payload 裡的 `t` 是各 node 在量測當下蓋的(README 2.2)。跨 PC 時網路延遲因此
不會污染時間軸。Log 只在 payload 沒帶 `t` 時才補上自己的時間。

## 時間戳的往返

log 裡是不帶時區的牆上時間。要在瀏覽器裡顯示成同一個鐘面時間,兩邊做了對稱的事:

```
sync.to_ms()                        chart_template 的 wall()
"2026-09-20 20:12:38.824"            1789935158824
  → 當成 UTC 解析 → ms               → 用 getUTCHours() 等印回去
```

一來一回剛好抵消,所以讀報告的人在哪個時區都不影響。**改任何一邊都要同時改另一邊**,
不然整張圖會平移一個 UTC 偏移量。`tests/test_log.py` 釘住了這個轉換。

## 模板的 DATA 形狀

```js
{
  title, built, t0,                    // t0 = 整份記錄的起點(epoch ms)
  window: {start, end},
  groups: {裝置名: 顯示名}, order: [裝置名],
  cols: [{name, group, unit, ref, vis,
          t: [相對 t0 的整數毫秒],      // ← 每條長度可以不同
          v: [讀值]}],
  marks: [{t, text}],
  bands: [{t0, t1}], limits: [{y, label}],
}
```

`t` 存相對值是為了省空間(絕對毫秒是 13 位數,相對值通常 4–7 位)。
模板那邊 `absT()` 再加回 `t0`。

模板原本吃的是 `t0 + i*dt` 的等間隔網格;改成每條自己的時間戳只動了四處,因為
統計函式 `stat(s,a,b)` 本來就走 `s.t` 做二分搜尋,從沒假設等間隔。

## 為什麼 Log 需要 `init.ALL`

Log 要自己找出站上有哪些資料 topic,用的是 eCAL 原生的 monitoring API。
而 `ecal.initialize()` 預設的 `init.DEFAULT`(55)**不含 `MONITORING`(8)**。

這個踩過:少了它 `get_monitoring()` 丟 `RuntimeError`,而當初那個例外被吞掉了 ——
結果是**檔案照開、報告照產,只是裡面一筆資料都沒有**。現在 `_discover()` 失敗會在
`Log/activity` 上喊一次 `discovery failed, nothing will be recorded`。

寧可吵,也不要安靜地錄出空檔案。

## 測試策略

| 檔案 | 測什麼 | 需要 eCAL? |
|---|---|---|
| `tests/test_log.py` | 時間戳轉換、CSV 讀回、報告組裝、內嵌 plotly | 否 |
| `tests/test_ui.py` | Log 面板的狀態顯示與 run 清單 | 否 |

`test_log.py` 有兩個測試是**防止回歸**用的:`test_no_grid_fields_are_emitted`
(別讓 `dt`/`n` 網格偷偷回來)與 `test_series_keep_their_own_sample_times`。

報告能不能真的在瀏覽器裡跑起來,是用 QtWebEngine 載進去執行 JS 驗的(traces 數、
mark 標註、有沒有 JS 錯誤),不在自動化測試裡 —— 那需要 4.6 MB 的報告與一個
Chromium,跑起來太慢。
