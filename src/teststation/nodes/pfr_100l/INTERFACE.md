# PFR-100L — INTERFACE

可程式化電源(GW Instek),0–50 V / 0–10 A,額定 100 W。SCPI,結尾 LF。

Service 名稱:`PFR-100L/<port>`,例如 `PFR-100L/COM20`。

## 接法

兩種接法都會在 PC 上變成一個 COM,node 程式碼完全相同。面板 `F-29` 決定用哪一種
(操作步驟見 [docs/devices/PFR-100L/README.md](../../../../docs/devices/PFR-100L/README.md))。

| 接法 | 線 | 面板設定 | `--baud` |
|---|---|---|---|
| USB-CDC | 後面板 **USB Type B** ↔ PC USB A | `F-29 = 3` | 不看 |
| RS232 | 後面板 Remote-IN(RJ-45)+ PSU-232 ↔ PC | `F-29 = 1`,`F-71` = 鮑率 | 要跟 `F-71` 一致(出廠 115200) |

連上時 activity 會寫 `... via USB-CDC` 或 `... via RS232`。USB 轉 RS232 的線算 RS232。

## 命令

| method | 參數 | 說明 | 回覆範例 |
|---|---|---|---|
| `output_on` | — | 開始送電 | `OK output on` |
| `output_off` | — | 停止送電 | `OK output off` |
| `set_voltage` | `<v>` V,0–50 | 設定電壓;是 setting | `OK 12.000` |
| `set_current` | `<a>` A,0–10 | 設定限流;是 setting | `OK 2.000` |
| `clear_protection` | — | 解除 OVP / OCP / OTP(AC fail 解不掉) | `OK protection cleared` |
| `describe` | — | 回傳 JSON 命令清單 | `OK [{"name": "output_on", ...}]` |
| `set_channel` | `<channel> <name>` | 設定 channel 顯示名稱 | `OK voltage = DUT_12V` |
| `reconnect` | — | 手動重開連線(重連逾時後使用) | `OK reconnecting` |

* `set_voltage` / `set_current` 會先擋 **V 設定 × I 限流 > 100 W**,擋下時回
  `ERR power limit 100W exceeded: ...`,**什麼都不送**。這是防手滑,不是安全機制。
* 每道設定命令送出後都會查 `:SYST:ERR?`,儀器不收就回 `ERR ... rejected: <code> <msg>`。
* 連上時只**查詢**設定值、輸出狀態、OVP、OCP(寫進 activity),不改動任何東西。

## Topic

| topic | 內容 |
|---|---|
| `PFR-100L/<port>/voltage` | 量測電壓(`:MEAS:ALL?`),V |
| `PFR-100L/<port>/current` | 量測電流(`:MEAS:ALL?`),A |
| `PFR-100L/<port>/power` | 量測功率(`:MEAS:POW?`),W |
| `PFR-100L/<port>/status` | `ok` / `reconnecting` / `error: <msg>`,1 Hz |
| `PFR-100L/<port>/activity` | service 呼叫與 publish 事件 |

## 啟動

```
uv run python -m teststation.nodes.pfr_100l.node --port COM20   # 指定 port
uv run python -m teststation.nodes.pfr_100l.node                # 自動掃描(送 *IDN?)
```

| 選項 | 預設 | 說明 |
|---|---|---|
| `--port` | 掃描 | 指定 COM |
| `--baud` | 115200 | RS232 鮑率,要跟 `F-71` 一致;USB-CDC 不看 |
| `--interval` | 1.0 s | 量測輪詢間隔 |

掃描或連線失敗時,訊息會提示去檢查 `F-29` —— 介面沒切對時儀器完全不回話。
