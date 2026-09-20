# test-station

測試機台自動化平台。每台儀器與 DUT 都是一個 eCAL node,**terminal、UI、測試計畫都用同一套 eCAL 命令**溝通,
測試過程可完全自動化,結束後產生可互動的 Plotly HTML 報告。

| 項目 | 選型 |
|---|---|
| 語言 / 環境 | Python,以 **uv** 管理 |
| Middleware | **eCAL 6**(`eclipse-ecal`),跨 PC 時所有機台必須同為 v6 |
| UI | PySide6 |
| CLI | `st`(ROS 風格)+ `st console`(textual TUI) |
| 報告 | Plotly HTML(僅測試結束後產生,UI 內不畫即時曲線) |
| 儀器 / DUT I/O | pyserial、paramiko(SSH) |

---

## 1. 架構原則

1. **契約在 topic 與 service,不在程式碼。** 每個 node 一份 `INTERFACE.md`。
2. **GUI 沒有特權。** UI 按鈕送出的命令字串,與 `st`、測試計畫送的完全一樣。
3. **只有 Log node 寫檔。** 全站單一 CSV schema;時間戳由 node 在量測當下蓋(多台 PC 以 NTP 對時)。
4. **Topic 描述物理位置,不描述意義。** `<device>/<port>/<channel>`,意義由 payload 的 `name` 欄位承載。

所有 node 都連接實機,不提供模擬模式。

```
            ┌──────────┐   ┌──────────┐   ┌──────────────┐
            │  st CLI  │   │ PySide6  │   │ Main-Control │   ← 命令來源(平權)
            └────┬─────┘   └────┬─────┘   └──────┬───────┘
                 └──── eCAL Service(純文字命令 → OK/ERR)──┘
                                 │
   ┌──────────┬──────────┬───────┴──┬──────────┬──────────┐
   │ PFR-100L │BTM-4208SD│  Serial  │   SSH    │   Log    │   ← node(各自獨立 process)
   └────┬─────┴────┬─────┴────┬─────┴────┬─────┴────▲─────┘
        └──── eCAL Pub/Sub(JSON 量測值)─────────────┘
                                                    │
                                    logs/<run>/data.csv → report.html
```

---

## 2. 通訊契約

### 2.1 命令:eCAL Service

- 每個 node 提供一個 service,名稱為 `<device>/<port>`,例如 `PFR-100L/COM20`;Log 與 Main-Control 為固定名稱 `Log`、`Main-Control`。
- 命令是**一行純文字**:`<method> [args...]`,例如 `set_voltage 12`。
- 回覆以 `OK` 或 `ERR` 開頭,後接說明,例如 `OK 12.000`、`ERR power limit 100W exceeded`。

**所有 node 共通的 method**(由 `core` 基底類別提供):

| method | 說明 |
|---|---|
| `describe` | 回傳 JSON 命令清單(見 2.3),UI 依此自動產生按鈕 |
| `set_channel <channel> <name>` | 設定 channel 的顯示名稱,寫入 payload 的 `name`;未設定時 = channel 名 |
| `reconnect` | 手動重新開啟連線(重連逾時後使用) |

DUT node(Serial / SSH)額外提供 `send <raw text>`:原樣送給 DUT。

### 2.2 資料:eCAL Pub/Sub

Topic:`<device>/<port>/<channel>`,例如 `BTM-4208SD/COM7/ch3`、`PFR-100L/COM20/voltage`。
SSH 的 port 以 IP 表示,點換成底線:`SSH/192_168_1_50/soc_temp`。

Payload 為 JSON 字串,時間戳由 node 在量測當下蓋上,格式為人眼可讀的 `YYYY-MM-DD HH:MM:SS.mmm`:

```json
{"t": "2026-09-19 15:30:01.123", "v": 71.2, "unit": "°C", "name": "SoC_heatsink"}
```

時間戳為**本地時間、不帶時區**(與 `chart_template.html` 的處理方式一致)。跨 PC 時所有機台必須 NTP 對時,**且設為同一時區**。

**所有 node 共通的 topic**:

| topic | 內容 |
|---|---|
| `<device>/<port>/status` | `ok` / `reconnecting` / `error: <msg>`,每秒一次,兼心跳 |
| `<device>/<port>/activity` | node 收到的 service 呼叫與 publish 的事件,供 `st console` 下半部顯示 |
| `<device>/<port>/raw` | 僅 DUT node:DUT 原始輸出的每一行 |

### 2.3 `describe` 格式

```json
[
  {"name": "output_on",   "label": "開始送電"},
  {"name": "output_off",  "label": "停止送電", "style": "danger"},
  {"name": "set_voltage", "label": "設定電壓",
   "args": [{"name": "v", "unit": "V", "min": 0, "max": 50}]}
]
```

無參數 → 按鈕;有參數 → 輸入框 + 按鈕(依 `min`/`max` 限制)。按下後送出 `set_voltage 12`。
同一份清單也是 `st` 的 Tab 補完來源,以及 `INTERFACE.md` 命令表的來源。

---

## 3. Node 啟動與 port 辨識

```
uv run pfr_100l --port COM20     # 指定 port
uv run pfr_100l                  # 自動掃描
```

- 未指定 `--port` 時,node **自己掃描**所有 COM,以該裝置專屬的方式辨識,**使用中的 port 直接跳過**。
  兩台同型裝置因此會自然分配到不同 COM。
- **node 必須依序啟動,不可同時啟動**:掃描期間會暫時佔用別人的 port,導致對方誤判為「使用中」而跳過。
  實務上以 `st launch` 啟動整站(見 3.1)。
- 探測字串必須盡量無害(會打進其他裝置或 DUT 的 console)。
- Topic 的 port 永遠用 COM 名稱,不用序號(同型 DUT 可能接在同一台電腦)。

| 裝置 | 辨識方式 |
|---|---|
| PFR-100L | 送 `*IDN?`,回應含 `PFR-100L` |
| BTM-4208SD | **只聽不送**:9600 8N1 下收到符合格式的 16 字元封包 |
| Serial DUT | 由 DUT profile 的 `identify()` 決定 |
| SSH DUT | 不掃描,必須以 `--host` 指定 |

### 3.1 `st launch` — 依序啟動整站

類似 ros launch。站台配置寫在 `launch/*.yaml`:

```yaml
# launch/burn_in_station.yaml
nodes:
  - run: log
  - run: pfr_100l
    args: --port COM20          # 指定 port,不掃描
  - run: pfr_100l               # 第二台:自動掃描,跳過 COM20
  - run: btm_4208sd
  - run: serial_dut
    args: --dut axelera_board
  - run: ssh_dut
    args: --dut axelera_board --host 192.168.1.50
ready_timeout: 30               # 每個 node 最多等 30 s 變 ok
```

```
st launch launch/burn_in_station.yaml
```

- 依列出的順序啟動,**等前一個 node 的 `status` 變成 `ok`(掃描完成、port 已佔住)才啟動下一個**。
- 某 node 超過 `ready_timeout` 仍未 `ok`:紅字警示,**繼續啟動下一個**,不整批中止。
- launcher 留在前景,各 node 輸出加前綴(如 `[pfr_100l/COM20]`)合併顯示。
- Ctrl+C 以**反向順序**關閉所有 node,Log 最後關,確保收到其他 node 的最後資料。
- UI 也可選擇 launch 檔一鍵啟動。
- 不使用 eCAL Sys:它只能「等固定秒數」,不理解 `status` 語意,且需安裝完整版 eCAL。

---

## 4. Node 說明

### PFR-100L — 可程式化電源(GW Instek)

- RS-232/RS-485,一個 process 對應一台機器、一個 COM,不做 multi-drop。
- 0–50 V / 0–10 A,**額定 100 W**:`set` 時就擋掉 `V × I > 100 W`。
- 命令與週期輪詢必須走同一個序列化 I/O 佇列,否則回應會交錯;取樣上限約 10–20 Hz。
- 原廠文件:`docs/devices/PFR-100L/`(含 Programming Manual)。

### BTM-4208SD — 12 channel 熱電偶記錄器(Lutron)

- RS-232 **單向連續輸出**,9600 8N1,**不接受任何命令**;單位、取樣等設定只能在面板操作。
- 封包 16 字元:D15 起始、D14 = `4`、D13 = channel(`1`–`9`、`A`–`C`)、D12–D11 單位(`01` °C / `02` °F)、
  D10 正負號、D9 小數位數、D8–D1 讀值、D0 結尾(手冊 p.23–24)。
- Topic:`BTM-4208SD/<port>/ch1` … `ch12`;**沒插探棒(over range)的 channel 不 publish**。
- 可用 `set_channel ch3 SoC_heatsink` 命名。

### Serial / SSH — DUT node

通用傳輸 node,DUT 相關的邏輯全部放在 **DUT profile**(`src/teststation/duts/<name>.py`):

| profile 內容 | 用途 |
|---|---|
| `identify(port)` | 掃描時辨識這台 DUT |
| `commands` | 對外開放的 method(格式同 `describe`) |
| `parse(line)` | 從一行回傳解析出 `{channel: value}`,node 負責 publish |

```
uv run serial_dut --dut axelera_board [--port COM5]
uv run ssh_dut    --dut axelera_board --host 192.168.1.50
```

新增一種 DUT 只需新增一個 profile,不改 node。

### Log

| method | 說明 |
|---|---|
| `start <run_name>` | 建立 `logs/YYYY-MM-DD_HHMMSS_<run_name>/`,開始訂閱所有資料 topic |
| `mark <text>` | 在時間軸打事件標記(報告上畫成區段或垂直線) |
| `stop` | 關檔,並自動產生 `report.html` |

run 資料夾內容:

| 檔案 | 內容 |
|---|---|
| `data.csv` | long format:`t, topic, name, value, unit`;**每筆立即 flush** |
| `console.log` | 所有 DUT `raw` topic 的原始輸出 |
| `report.html` | 由 `sync.py`(pandas 對齊到同一時間軸)+ `chart_template.html` 產生 |

報告產出後由使用者在 HTML 上自行調整(顯示群組、區間統計等)。

### Main-Control — 測試計畫 runner

測試計畫是 `plans/` 下的 Python 腳本,使用 `teststation.control` 提供的 client API:

```python
from teststation.control import cmd, start, stop, mark, wait_until, sleep

start("burn_in_45C")
cmd("BTM-4208SD/COM7", "set_channel ch3 SoC_heatsink")
cmd("PFR-100L/COM20", "set_voltage 12")
cmd("PFR-100L/COM20", "output_on")
mark("DUT power on")
wait_until("BTM-4208SD/COM7/ch3", lambda v: v > 45, timeout=1800)
sleep(3600)
stop()
```

- `cmd()` 送出的字串與 `st` 手打的完全相同。
- Main-Control 本身的 service:`run <plan>`、`abort`,並 publish 執行進度。
- **abort 或腳本例外時**,runner 在 `finally` 裡關閉所有 PFR 輸出,並對 Log 下 `mark` 與 `stop`。

---

## 5. 操作介面

### 5.1 `st` CLI(ROS 風格)

```
st node list
st service list
st service call     PFR-100L/COM20 "set_voltage 12"
st service describe PFR-100L/COM20
st topic list
st topic echo BTM-4208SD/COM7/ch3
st topic pub  test/fake/ch1 '{"v": 1.0}'
st topic hz   BTM-4208SD/COM7/ch3
st                                   # 互動模式(REPL),Tab 補完來自 describe
```

### 5.2 `st console` — DUT 雙窗格 TUI

```
st console Serial/COM5
┌─ DUT console ─────────────────────────────────┐
│ [RX] soc: 71.2C  ddr: 58.0C                    │  raw topic
│ [TX] > temp                                    │  經 send method
│ > _                                            │
├─ node activity ───────────────────────────────┤
│ 15:30:01.123 PUB Serial/COM5/soc_temp 71.2 °C  │  activity topic
│ 15:30:02.500 SRV set_channel soc_temp → OK     │
└────────────────────────────────────────────────┘
```

上半是 DUT 原始收發,下半是 node 解析後 publish 的值與收到的命令。調整 profile 的 `parse()` 時用來對照。

### 5.3 PySide6 UI

通用 UI,不為個別裝置撰寫畫面:

1. **Node 清單**:從 eCAL 註冊資訊自動列出,燈號依 `status` topic。
2. **命令面板**:依 `describe` 自動產生按鈕與輸入框,另附自由輸入的命令列與歷史。
3. **即時數值表**:所有資料 topic 的最新值、`name`、單位、更新時間。
4. **測試計畫**:選擇計畫、Run / Abort、進度與輸出。
5. **Log 控制**:Start / Mark / Stop、開啟最近一份報告。
6. **DUT console 分頁**:與 `st console` 相同的雙窗格。

---

## 6. 異常處理

| 情況 | 行為 |
|---|---|
| 裝置讀寫失敗(如 USB 被拔) | node 不結束,狀態 `reconnecting`,每 2 秒重開**同一個 COM**(不重新掃描) |
| 重連超過 **30 秒** | 狀態 `error: ...`,停止自動重試,node 仍存活;可用 `reconnect` 手動重試 |
| 計畫執行中某 node 非 `ok` 超過 10 秒或心跳消失 | UI 即時狀態與 terminal **紅字警示**,並寫入 Log;**不中止計畫** |
| 使用者 abort / 腳本例外 | 關閉所有 PFR 輸出 → Log `mark` → Log `stop`,照常產出報告 |
| Log 當掉 | 因逐筆 flush,最多損失最後一行 |

---

## 7. Repo 結構

```
test-station/
├─ pyproject.toml / uv.lock
├─ README.md
├─ TODO.md
├─ src/teststation/
│  ├─ core/            # node 基底:service、describe、set_channel、status、activity、COM 掃描、payload
│  ├─ nodes/
│  │  ├─ pfr_100l/     # node.py、scpi.py、INTERFACE.md
│  │  ├─ btm_4208sd/   # node.py、protocol.py、INTERFACE.md
│  │  ├─ serial_dut/
│  │  ├─ ssh_dut/
│  │  └─ log/          # recorder、sync.py、report.py、chart_template.html
│  ├─ duts/            # DUT profile
│  ├─ control/         # Main-Control runner 與計畫用 client API
│  ├─ cli/             # st、st console
│  └─ ui/              # PySide6
├─ launch/             # st launch 站台配置(*.yaml)
├─ plans/              # 測試計畫腳本
├─ tests/              # 封包解析與 node 契約測試
├─ docs/
│  ├─ st.md            # st 命令列的操作文件
│  ├─ ui.md            # PySide6 UI 的操作文件
│  ├─ devices/         # 各裝置的原廠手冊 + 操作文件 README.md(PFR-100L、BTM-4208SD)
│  └─ interactive_chart.html   # 報告長相的範例
└─ logs/               # run 輸出(.gitignore)
```

資料夾與模組名稱中,型號的連字號以底線取代(`pfr_100l`);topic 與 service 名稱保留原廠寫法(`PFR-100L`)。

---

## 8. 預設值(可調整)

| 項目 | 預設 |
|---|---|
| 量測 publish 頻率 | 各 node 以 `--interval` 指定,預設 1 s;BTM-4208SD 依儀器輸出節奏 |
| 狀態心跳 | 1 s |
| 重連間隔 / 逾時 | 2 s / 30 s |
| 異常警示門檻 | 非 `ok` 持續 10 s |
| Python | 3.13;若 `eclipse-ecal` 無對應 wheel 則鎖定 3.12 |
