# PEL-3031E — INTERFACE

直流電子負載(GW Instek),0–150 V / 0–60 A / 300 W。SCPI,結尾 LF
(PEL-3000E Programming Manual V1.11)。

Service 名稱:`PEL-3031E/<port>`,例如 `PEL-3031E/COM21`。

## 接法

只有 **USB-CDC**:後面板 **USB Type B** ↔ PC USB A,面板
`Shift + Help(Utility)> Interface[F3] = USB`。PC 上會多一個 COM,不看鮑率。
GPIB 是選配、本站不支援;這台**沒有 RS232**。

## 命令

| method | 參數 | 說明 | 回覆範例 |
|---|---|---|---|
| `load_on` | — | 開始拉載(`:INP ON`) | `OK load on` |
| `load_off` | — | 停止拉載(`:INP OFF`) | `OK load off` |
| `set_mode` | `<mode>`:`CC` / `CV` / `CP` | 操作模式;是 setting | `OK CC` |
| `set_current` | `<a>` A,0–60 | CC 模式的電流(`:CURR:VA`);是 setting | `OK 2.5000` |
| `set_voltage` | `<v>` V,0–150 | CV 模式的電壓(`:VOLT:VA`);是 setting | `OK 12.0000` |
| `set_power` | `<w>` W,0–300 | CP 模式的功率(`:POW:VA`);是 setting | `OK 24.00` |
| `describe` | — | 回傳 JSON 命令清單 | `OK [{"name": "load_on", ...}]` |
| `set_channel` | `<channel> <name>` | 設定 channel 顯示名稱 | `OK current = DUT_load` |
| `reconnect` | — | 手動重開連線(重連逾時後使用) | `OK reconnecting` |

* **`set_current` 會順便把模式切到 CC,`set_power` 會切到 CP;`set_voltage` 不切換。**
  這是實機(FW V1.03.002)量到的行為,手冊沒寫。拉載中(`load_on`)改值時要特別注意 ——
  CC 拉載時下 `set_power` 會立刻變成 CP 拉載。launch 檔裡 `set_mode` 放**最後一行**,
  以它為準。
* **拉載中用 `set_mode` 真的換模式,儀器會自己把 load 關掉**(設值順便換模式則不會)。
  這時回覆是 `OK CV (load turned off by the mode change; load_on to resume)`,要再 `load_on`。
* 範圍只檢查到 High range 的額定。Low range(6 A / 15 V / 30 W)的上限由儀器擋,
  回 `ERR ':CURR:VA 10.0000' rejected: -222 Data out of range (:CURR:VA)`。
* 每道設定命令送出後都會查 `:SYST:ERR?`,儀器不收就回 `ERR ... rejected: <code> <msg>`,**什麼都沒變**。
* CR 模式刻意不開:CR 單位出廠是 mS,`:RESistance` 只在單位設成 OHM 時有效。
* range 與保護值(OCP / OPP / OVP / UVP)node 不設定,只在連線時查詢。
  不查 `:VRANge?`:實機在 CR 模式下對它不回話。

## Topic

| topic | 內容 |
|---|---|
| `PEL-3031E/<port>/voltage` | 量測電壓(`:MEAS:VOLT?`),V |
| `PEL-3031E/<port>/current` | 量測電流(`:MEAS:CURR?`),A |
| `PEL-3031E/<port>/power` | 量測功率(`:MEAS:POW?`),W |
| `PEL-3031E/<port>/status` | `ok` / `reconnecting` / `error: <msg>`,1 Hz |
| `PEL-3031E/<port>/activity` | service 呼叫與 publish 事件 |

## 啟動

```
uv run python -m teststation.nodes.pel_3031e.node --port COM21   # 指定 port
uv run python -m teststation.nodes.pel_3031e.node                # 自動掃描(送 *IDN?)
```

| 選項 | 預設 | 說明 |
|---|---|---|
| `--port` | 掃描 | 指定 COM |
| `--interval` | 1.0 s | 量測輪詢間隔 |
