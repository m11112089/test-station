# GPP3650 — GW Instek GPP-3650 三通道可程式電源供應器

- `gpp3650_node` — device driver node（headless，獨立 process，SCPI over serial）
- `gpp3650_gui` — 驗證用 GUI（topic 方向與 node 相反，只透過 eCAL 溝通）

```
GPP3650/
├── CMakeLists.txt
├── node/
│   ├── main.cpp
│   ├── NodeApp.hpp/.cpp        # eCAL <-> 主執行緒 marshal、輪詢、斷線重連
│   └── Gpp3650Serial.hpp/.cpp  # SCPI over QSerialPort (含 --sim 模擬)
└── gui/
    ├── main.cpp
    └── GuiWindow.hpp/.cpp
```

## 裝置規格（依 GPP-3060/6030/3650 手冊）

| 通道 | 電壓 | 電流 | 備註 |
|---|---|---|---|
| CH1 / CH2 | 0 – 36.5 V | 0 – 5.2 A | 獨立輸出 |
| CH3 | 固定檔位 1.8 / 2.5 / 3.3 / 5.0 V | 5 A（固定） | 不可設電流、**無電流/功率回讀** |

通訊：RS-232 / USB CDC，115200 8N1，命令以 `\n` 結尾。
使用的 SCPI 指令：`*IDN?`（回應需含 `GPP`）、`VSET<ch>:<v>`、`ISET<ch>:<i>`、
`:OUTPut<ch>:STATe ON|OFF`、`:ALLOUTON` / `:ALLOUTOFF`、`:MEASure<ch>:ALL?`。

## Topic 規約

Topic 前綴 = `gpp3650/<port>`，`<port>` 由 `--port` 自動推導，**不另取邏輯名稱**：

| `--port` | Topic 前綴 |
|---|---|
| `COM4`（Windows） | `gpp3650/COM4` |
| `/dev/ttyUSB0`（Ubuntu，取路徑最後一段） | `gpp3650/ttyUSB0` |
| （`--sim` 模擬模式） | `gpp3650/SIM` |

多台同型裝置各自指定不同 `--port` 即可自動區分。
注意：Windows COM 編號與 Linux `ttyUSBx` 會隨插拔/重開機改變；
Linux 需要穩定識別時可改傳 `/dev/serial/by-id/...`（同樣取最後一段）。

### Node 訂閱（GUI / 測試流程 → node）

| Topic | Payload 範例 | 說明 |
|---|---|---|
| `gpp3650/<port>/cmd/output` | `{"ch":1,"on":true}` | 通道開關；`ch:0` = ALL ON/OFF |
| `gpp3650/<port>/cmd/setpoint` | `{"ch":1,"voltage":5.0,"current":1.0}` | voltage/current 皆選填，只套用有出現的欄位；自動 clamp 到規格範圍；CH3 只吃 voltage 且 snap 到 1.8/2.5/3.3/5.0 |
| `gpp3650/<port>/cmd/config` | `{"rate_hz":2.0}` | 量測發佈頻率 0.1–10 Hz，預設 1 Hz |

### Node 發佈（node → GUI / 測試流程）

| Topic | Payload 範例 | 說明 |
|---|---|---|
| `gpp3650/<port>/meas` | `{"device":"gpp3650/COM4","seq":12,"timestamp_ms":1720000000000,"channels":[{"ch":1,"voltage":5.001,"current":0.5,"power":2.5},{"ch":2,...},{"ch":3,"voltage":3.3}]}` | 依 rate_hz 週期發佈；CH3 無電流/功率回讀 |
| `gpp3650/<port>/status` | `{"device":"gpp3650/COM4","connected":true,"port":"COM4","idn":"GW INSTEK,GPP-3650,...","sim":false,"rate_hz":1.0,"error":"","timestamp_ms":...}` | 2 秒心跳 + 連線/斷線/錯誤事件即時發佈 |

`status` 存在的理由：安全 interlock（高壓/高溫）的監督程式必須能分辨
「量測值是 0」和「裝置斷線」，只靠 meas topic 無法區分。

## 執行

```bash
# 1. driver node
./gpp3650_node --port COM3              # Windows  -> topic 前綴 gpp3650/COM3
./gpp3650_node --port /dev/ttyUSB0      # Ubuntu   -> topic 前綴 gpp3650/ttyUSB0
./gpp3650_node --sim                    # 模擬模式 -> topic 前綴 gpp3650/SIM
./gpp3650_node --list-ports             # 列出可用串口
./gpp3650_node --port COM4              # 第二台裝置: 換 port 即自動區分

# 2. 驗證 GUI（另一個 process，只透過 eCAL 溝通; --port 與 node 相同即可配對）
./gpp3650_gui                           # 對應 gpp3650_node --sim
./gpp3650_gui --port COM3
./gpp3650_gui --port /dev/ttyUSB0
```

也可以用 eCAL Monitor 觀察所有 topic，或以任何 eCAL 程式直接對
`gpp3650/cmd/*` 發佈 JSON 測試 node。

## Node 行為細節

- 斷線處理：開埠失敗 / 回應逾時 / 解析失敗 → 發佈 `status`（含 error）→
  每 3 秒自動重連；重連成功後恢復輪詢。
- 命令在未連線時到達 → 忽略並發佈帶 error 的 `status`。
- setpoint 超出範圍會被 clamp / snap，並在 node log 留下警告。
- `--sim` 模擬模式：假設 10 Ω 電阻性負載，輸出 = 設定值 ± 2 mV 雜訊，
  電流 = min(Iset, V/10)。
