# Log — INTERFACE

全站唯一會寫檔的 node(README 1.3)。service 名稱固定是 `Log`,**沒有 port**。

**一啟動就開檔**,資料夾名是啟動當下的時間。`record` 設定完就**立刻開始寫** ——
沒有「開始記錄」這個動作。**不產報告** —— 那是事後 `uv run visualization <data.csv>`。

## 命令

| method | 參數 | 說明 | 回覆範例 |
|---|---|---|---|
| `record` | `<topic> ...` | 選要記錄哪些 topic。**整份清單一次給完,覆蓋前一次**,而且**立刻生效**。是 setting | `OK 3 topic(s)` |
| `start` | `<run_name>` | 關掉現在這個檔,另開一個帶名字的。給測試計畫劃分 run 用 | `OK logs/2026-09-21_114517_burn_in_45C` |
| `stop` | — | 關檔。**不產報告** | `OK .../data.csv (1523 rows)` |
| `describe` | — | 命令清單 | |
| `set_channel` | `<channel> <name>` | (共通 method,Log 用不到) | |
| `reconnect` | — | (共通 method,Log 沒有裝置可重連) | |

`record` 是 **setting**(`describe` 裡標了 `"setting": true`),所以 UI 會把它存進
launch 檔,下次啟動自動帶回來。UI 的 **記錄分頁**就是一組 topic 勾選框加上「套用選擇」。

一次都沒 `record` 就只是什麼都不寫 —— 檔案還是開著的,等你選。
對著已經關掉的 Log 下 `stop` 回 `OK no file open`(無害的空操作)。

## Topic

| topic | 內容 |
|---|---|
| `Log/status` | `ok` / `error: <msg>`,1 Hz 心跳 |
| `Log/activity` | service 呼叫、訂閱了哪些 topic、報告產生 |
| `Log/state` | 錄製狀態,1 Hz(見下) |

`Log/state` 的 payload:

```json
{"t": "2026-09-20 20:16:53.272", "recording": true, "run": "burn_in_45C",
 "dir": "C:\...\logs\2026-09-20_201653_burn_in_45C",
 "rows": 1523, "topics": 3,
 "selected": ["BTM-4208SD/COM11/ch1", "BTM-4208SD/COM11/ch3"],
 "started": "2026-09-20 20:16:53.272"}
```

`selected` 讓 UI 的勾選框跟著走 —— 設定可能來自 launch 檔,不是在畫面上按的。

**故意不帶 `v`**,所以不會被當成量測值收進報告或即時數值表 —— 判斷一個 payload
是不是量測值的通則就是「有沒有 `v`」(README 2.2)。`state` 本身也列在共通 topic 裡,
所以不會出現在「要記錄哪些 topic」的清單上。

Log **不 publish 任何量測值**。

## run 資料夾

`logs/YYYY-MM-DD_HHMMSS/`,`start <name>` 開的則是 `logs/YYYY-MM-DD_HHMMSS_<name>/`

| 檔案 | 內容 |
|---|---|
| `data.csv` | long format `t, topic, name, value, unit`,**每筆立即 flush** |
| `console.log` | 選到的 DUT `raw` topic 的原始輸出 |

報告不在這裡:`uv run visualization logs/<run>/data.csv` 會在旁邊產出 `report.html`。

`data.csv` 照收到的順序寫,**不對齊、不重取樣**:

```csv
t,topic,name,value,unit
2026-09-20 20:12:38.824,BTM-4208SD/COM11/ch3,ch3,37.6,°C
2026-09-20 20:12:40.075,BTM-4208SD/COM11/ch1,ch1,31.5,°C
2026-09-20 20:12:44.000,PFR-100L/COM20/voltage,voltage,12.0,V
```

* 時間戳是**各 node 在量測當下蓋的**,不是 Log 收到的時間 —— 網路延遲不會污染時間軸。
* `name` 取每個 topic 的最後一筆:測試中途 `set_channel` 改名,報告上用新名字。

## 啟動

```
# UI 一開就會自己起一個;要單獨跑就走模組路徑
uv run python -m teststation.nodes.log.node
uv run python -m teststation.nodes.log.node --logs-dir D:/runs
```

Log 要自己找出站上有哪些資料 topic,所以它是**唯一需要 eCAL `MONITORING` 元件**的 node
(`components = ecal.init.ALL`)。少了它會訂不到任何 topic,而檔案照開、報告照產,
只是裡面沒有資料 —— 這種情況 `Log/activity` 會出現 `discovery failed` 的紅字。

**Log 不列在 launch 檔裡** —— UI 一開就起了一個。關站時 launcher 會先送它一個
`stop`,而且它一定最後關,所以收得到其他 node 的最後資料,檔案也會正常關好。
