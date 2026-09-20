# Log — INTERFACE

全站唯一會寫檔的 node(README 1.3)。service 名稱固定是 `Log`,**沒有 port**。

## 命令

| method | 參數 | 說明 | 回覆範例 |
|---|---|---|---|
| `start` | `<run_name>` | 建立 run 資料夾,開始訂閱所有資料 topic | `OK logs/2026-09-20_201653_burn_in_45C` |
| `mark` | `<text>` | 在時間軸打事件標記。**text 吃掉整行剩下的字**,不用引號 | `OK DUT power on` |
| `stop` | — | 關檔,並產生 `report.html` | `OK .../report.html (1523 rows)` |
| `describe` | — | 命令清單 | |
| `set_channel` | `<channel> <name>` | (共通 method,Log 用不到) | |
| `reconnect` | — | (共通 method,Log 沒有裝置可重連) | |

```
st service call Log "start burn_in_45C"
st service call Log "mark DUT power on"
st service call Log "stop"
```

重複 `start`、對著沒在錄的 Log 下 `mark` / `stop` 都會回 `OK` 加一句說明,不會壞掉。

## Topic

| topic | 內容 |
|---|---|
| `Log/status` | `ok` / `error: <msg>`,1 Hz 心跳 |
| `Log/activity` | service 呼叫、訂閱了哪些 topic、報告產生 |
| `Log/state` | 錄製狀態,1 Hz(見下) |

`Log/state` 的 payload:

```json
{"t": "2026-09-20 20:16:53.272", "recording": true, "run": "burn_in_45C",
 "dir": "C:\\...\\logs\\2026-09-20_201653_burn_in_45C",
 "rows": 1523, "marks": 2, "topics": 14, "started": "2026-09-20 20:16:53.272"}
```

**故意不帶 `v`**,所以不會被當成量測值收進報告或即時數值表 —— 判斷一個 payload
是不是量測值的通則就是「有沒有 `v`」(README 2.2),不需要另外維護排除名單。

Log **不 publish 任何量測值**。

## run 資料夾

`logs/YYYY-MM-DD_HHMMSS_<run_name>/`

| 檔案 | 內容 |
|---|---|
| `data.csv` | long format `t, topic, name, value, unit`,**每筆立即 flush** |
| `console.log` | 所有 DUT `raw` topic 的原始輸出 |
| `report.html` | `stop` 時產生,plotly 內嵌,**離線可看** |

`data.csv` 照收到的順序寫,**不對齊、不重取樣**:

```csv
t,topic,name,value,unit
2026-09-20 20:12:38.824,BTM-4208SD/COM11/ch3,ch3,37.6,°C
2026-09-20 20:12:40.075,BTM-4208SD/COM11/ch1,ch1,31.5,°C
2026-09-20 20:12:42.838,Log/mark,DUT power on,,
2026-09-20 20:12:44.000,PFR-100L/COM20/voltage,voltage,12.0,V
```

* 時間戳是**各 node 在量測當下蓋的**,不是 Log 收到的時間 —— 網路延遲不會污染時間軸。
* mark 用 `Log/mark` 這個保留 topic 寫在同一個檔裡,所以事件跟資料天然照時間排好。
* `name` 取每個 topic 的最後一筆:測試中途 `set_channel` 改名,報告上用新名字。

## 啟動

```
uv run log                      # logs/ 在當前目錄
uv run log --logs-dir D:/runs
```

Log 要自己找出站上有哪些資料 topic,所以它是**唯一需要 eCAL `MONITORING` 元件**的 node
(`components = ecal.init.ALL`)。少了它會訂不到任何 topic,而檔案照開、報告照產,
只是裡面沒有資料 —— 這種情況 `Log/activity` 會出現 `discovery failed` 的紅字。

`st launch` 裡 Log 要放第一個,這樣反向關閉時它最後關,收得到其他 node 的最後資料。
