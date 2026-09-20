# BTM-4208SD — INTERFACE

12 channel 熱電偶記錄器(Lutron)。RS-232 單向連續輸出,9600 8N1,**不接受任何命令**。

Service 名稱:`BTM-4208SD/<port>`,例如 `BTM-4208SD/COM11`。

## 命令

儀器沒有可下的命令(單位、取樣時間只能在面板操作),所以這裡只有 core 的三個共通 method。

| method | 參數 | 說明 | 回覆範例 |
|---|---|---|---|
| `describe` | — | 回傳 JSON 命令清單 | `OK [{"name": "describe", ...}]` |
| `set_channel` | `<channel> <name>` | 設定 channel 顯示名稱,寫入 payload 的 `name` | `OK ch3 = SoC_heatsink` |
| `reconnect` | — | 手動重開連線(重連逾時後使用) | `OK reconnecting` |

```
st service call BTM-4208SD/COM11 "set_channel ch3 SoC_heatsink"
```

## Topic

| topic | 內容 |
|---|---|
| `BTM-4208SD/<port>/ch1` … `ch12` | 溫度讀值(JSON payload) |
| `BTM-4208SD/<port>/status` | `ok` / `reconnecting` / `error: <msg>`,1 Hz |
| `BTM-4208SD/<port>/activity` | service 呼叫與 publish 事件 |

沒插探棒(over range)的 channel **不 publish**,連 topic 都不會出現。
沒有 `raw` topic(那是 DUT node 才有)。

資料 payload:

```json
{"t": "2026-09-20 00:41:30.524", "v": 31.0, "unit": "°C", "name": "SoC_heatsink"}
```

* `unit` 跟著儀器面板走,`°C` 或 `°F`,由封包的 D12/D11 決定,node 不做換算。
* `name` 未用 `set_channel` 命名過時 = channel 名(`ch1`)。
* 發佈節奏依儀器輸出,實測一輪 12 個 channel 約 2 秒,沒有 `--interval`。

## 啟動

```
uv run btm_4208sd --port COM11      # 指定 port
uv run btm_4208sd                   # 自動掃描(只聽不送)
```

| 選項 | 預設 | 說明 |
|---|---|---|
| `--port` | 掃描 | 指定 COM |
| `--scan-timeout` | 3.0 s | 掃描時每個 port 聽多久 |
| `--data-timeout` | 10.0 s | 多久沒收到合格封包視為斷線 |
| `--sniff` | off | 只印原始封包,不啟動 eCAL node |
