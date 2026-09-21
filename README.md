# test-station

測試機台自動化平台。每台儀器與 DUT 都是一個 eCAL node,**UI 與測試計畫用同一套
eCAL 命令**溝通,測試過程可完全自動化,記錄下來的 CSV 事後產成可互動的 Plotly HTML 報告。

| 項目 | 選型 |
|---|---|
| 語言 / 環境 | Python,以 **uv** 管理 |
| Middleware | **eCAL 6**(`eclipse-ecal`),跨 PC 時所有機台必須同為 v6 |
| UI | PySide6 —— **站台唯一的人機介面** |
| 報告 | Plotly HTML,事後由 `uv run visualization` 產生 |
| 儀器 / DUT I/O | pyserial、paramiko(SSH) |

> **[第一部分:操作站台](#第一部分操作站台)** —— 要用這台機器的人看這裡
> **[第二部分:實作與契約](#第二部分實作與契約)** —— 要改程式、要接新裝置的人看這裡

---
---

# 第一部分:操作站台

## 安裝

```
uv sync
```

就這樣。`uv` 會自己建好環境、裝好所有東西,不用先 activate 什麼。

## 第一次跑起來

```
uv run ui
```

### 1. 啟動整站

到「**啟動整站**」分頁,選一份 `launch/*.yaml`。node 會**依序**起來,
每個都等它的燈變綠才起下一個(理由見第二部分 3)。

> **關掉 UI 就是結束測試。** node 是 UI 的子行程,關窗等於整站關閉。

### 2. 設定儀器

左邊點一個 node,到「**命令**」分頁。按鈕是照那台 node 自己回報的能力長出來的,
所以每台裝置看到的不一樣。例如把熱電偶的 ch3 命名成有意義的名字:

```
set_channel ch3 SoC_heatsink
```

之後所有畫面與報告上,那條線都叫 `SoC_heatsink`。

### 3. 錄一次

到「**Log**」分頁:

1. **勾選要記錄的 topic** —— 只錄勾到的
2. 填 run 名稱,按 **Start**
3. 過程中按 **Mark** 打事件標記(例如「DUT power on」)
4. 按 **Stop** 關檔

### 4. 產報告

同一個分頁選那次 run,按「**看報告**」。報告會開在「報告」分頁裡。

也可以事後在命令列產:

```
uv run visualization logs/2026-09-21_103723_burn_in_45C/data.csv
```

**報告是自足的檔案**(plotly 內嵌),可以複製到隨身碟、寄出去、在沒網路的電腦上開。

### 5. 存成 launch 檔

設定調好之後,在「啟動整站」分頁按「**存成 launch 檔**」。
下次直接開那個檔,所有設定會自動帶回來 —— 不用再設一次。

存的是**設定**(channel 名字、電壓值、要錄哪些 topic),不是動作。
所以開檔不會意外送電。

## UI 有什麼

| 分頁 | 做什麼 |
|---|---|
| Node 清單(左) | 站上有哪些 node,燈號就是狀態 |
| 命令 | 那台 node 能下的命令,按鈕自動長出來 |
| 即時數值 | 所有量測值的最新數字 |
| DUT console | DUT 的原始收發 |
| 測試計畫 | 選腳本、Run / Abort |
| Log | 勾 topic、Start / Mark / Stop、過去的 run |
| 報告 | 把報告開在視窗裡 |
| topic 工具 | 量發佈頻率、發假資料 |
| 啟動整站 | 選 launch 檔啟動、存成 launch 檔 |

細節見 **[docs/ui.md](docs/ui.md)**。

## 燈號看不懂的時候

| 顏色 | 意思 | 怎麼辦 |
|---|---|---|
| 綠 `ok` | 正常 | |
| 橘 `reconnecting` | 連線斷了,每 2 秒自己重試 | 檢查線有沒有鬆、port 是不是被別的程式佔住 |
| 紅 `error: ...` | 重試超過 30 秒放棄了 | 排除問題後,在「命令」分頁按「重新連線」 |
| 灰 | 註冊得到但收不到心跳 | node 卡住了,重開整站 |

儀器本身的故障排除(探棒、面板設定、線材)見各裝置的文件。

## 更細的文件

| 主題 | 文件 |
|---|---|
| UI 每一塊怎麼用 | [docs/ui.md](docs/ui.md) |
| 記錄與報告 | [docs/log.md](docs/log.md) |
| BTM-4208SD 熱電偶記錄器 | [docs/devices/BTM-4208SD/README.md](docs/devices/BTM-4208SD/README.md) |
| 還沒做完的事 | [TODO.md](TODO.md) |

---
---

# 第二部分:實作與契約

## 1. 架構原則

1. **契約在 topic 與 service,不在程式碼。** 每個 node 一份 `INTERFACE.md`。
2. **GUI 沒有特權。** UI 按鈕送出的命令字串,與測試計畫送的完全一樣,
   走的是同一個 client 層(`teststation.client`),那一層不依賴 Qt。
3. **只有 Log node 寫檔。** 全站單一 CSV schema;時間戳由 node 在量測當下蓋(多台 PC 以 NTP 對時)。
4. **Topic 描述物理位置,不描述意義。** `<device>/<port>/<channel>`,意義由 payload 的 `name` 欄位承載。
5. **記錄與視覺化分家。** CSV 是資料,報告只是它的一種樣子。

所有 node 都連接實機,不提供模擬模式。

```
                     ┌──────────┐   ┌──────────────┐
                     │ PySide6  │   │ Main-Control │   ← 命令來源(平權)
                     └────┬─────┘   └──────┬───────┘
                          └─ eCAL Service(純文字命令 → OK/ERR)─┘
                                 │
   ┌──────────┬──────────┬───────┴──┬──────────┬──────────┐
   │ PFR-100L │BTM-4208SD│  Serial  │   SSH    │   Log    │   ← node(各自獨立 process)
   └────┬─────┴────┬─────┴────┬─────┴────┬─────┴────▲─────┘
        └──── eCAL Pub/Sub(JSON 量測值)─────────────┘
                                                    │
                                          logs/<run>/data.csv
                                                    │
                              uv run visualization  ↓
                                          logs/<run>/report.html
```

## 2. 通訊契約

### 2.1 命令:eCAL Service

- 每個 node 提供一個 service,名稱為 `<device>/<port>`,例如 `PFR-100L/COM20`;Log 與 Main-Control 為固定名稱 `Log`、`Main-Control`。
- 命令是**一行純文字**:`<method> [args...]`,例如 `set_voltage 12`。
  最後一個參數可以吃掉整行剩下的文字(例如 `mark DUT power on` 是一個參數)。
- 回覆以 `OK` 或 `ERR` 開頭,後接說明,例如 `OK 12.000`、`ERR power limit 100W exceeded`。
  **`ERR` 表示什麼都沒發生** —— 被擋下來的命令回 `ERR`,不是回 `OK` 加一句說明。

**所有 node 共通的 method**(由 `core` 基底類別提供):

| method | 說明 |
|---|---|
| `describe` | 回傳 JSON 命令清單(見 2.3),UI 依此自動產生按鈕 |
| `set_channel <channel> <name>` | 設定 channel 的顯示名稱,寫入 payload 的 `name`;未設定時 = channel 名。是 setting |
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
| `<device>/<port>/activity` | node 收到的 service 呼叫與 publish 的事件,供 UI 的 DUT console 下半部顯示 |
| `<device>/<port>/state` | 選用:node 自己那份比 `status` 更細的狀態(例如 Log 的錄製進度) |
| `<device>/<port>/raw` | 僅 DUT node:DUT 原始輸出的每一行 |

`status` / `activity` / `state` 是 node 自己的狀態,**不是資料**,不會被記錄、
也不會出現在「要記錄哪些 topic」的清單上。
判斷一個 payload 是不是量測值,看它**有沒有 `v`** —— 不維護排除名單,那種名單一定會漏。

### 2.3 `describe` 格式

```json
[
  {"name": "output_on",   "label": "開始送電"},
  {"name": "output_off",  "label": "停止送電", "style": "danger"},
  {"name": "set_voltage", "label": "設定電壓", "setting": true,
   "args": [{"name": "v", "unit": "V", "min": 0, "max": 50}]}
]
```

無參數 → 按鈕;有參數 → 輸入框 + 按鈕(依 `min`/`max` 限制)。按下後送出 `set_voltage 12`。
同一份清單也是 `INTERFACE.md` 命令表的來源。

`"setting": true` 表示這個命令設的是**一個會留下來的值**,不是一次性的動作。
只有設定會被存進 launch 檔並在下次啟動時重放 —— 所以 `set_voltage` 標,
`output_on` 不標。**標錯了開檔就會送電。**

「同一個設定」以命令加上**最後一個參數以外**的所有詞為準:`set_channel ch3 X` 與
`set_channel ch1 Y` 是兩個設定,重設 ch3 則覆蓋前一次。variadic 命令(`record a b c`)
整份清單算一個設定。

## 3. Node 啟動與 port 辨識

```
uv run pfr_100l --port COM20     # 指定 port
uv run pfr_100l                  # 自動掃描
```

實務上由 UI 的「啟動整站」依序啟動(見 3.1),不手動一個一個起。

- 未指定 `--port` 時,node **自己掃描**所有 COM,以該裝置專屬的方式辨識,**使用中的 port 直接跳過**。
  兩台同型裝置因此會自然分配到不同 COM。
- **node 必須依序啟動,不可同時啟動**:掃描期間會暫時佔用別人的 port,導致對方誤判為「使用中」而跳過。
- 探測字串必須盡量無害(會打進其他裝置或 DUT 的 console)。
- Topic 的 port 永遠用 COM 名稱,不用序號(同型 DUT 可能接在同一台電腦)。

| 裝置 | 辨識方式 |
|---|---|
| PFR-100L | 送 `*IDN?`,回應含 `PFR-100L` |
| BTM-4208SD | **只聽不送**:9600 8N1 下收到符合格式的 16 字元封包 |
| Serial DUT | 由 DUT profile 的 `identify()` 決定 |
| SSH DUT | 不掃描,必須以 `--host` 指定 |

### 3.1 依序啟動整站

類似 ros launch。站台配置寫在 `launch/*.yaml`:

```yaml
# launch/burn_in_station.yaml
nodes:
  - run: log
    setting:                          # node ready 之後送過去的設定
      - record BTM-4208SD/COM7/ch3 PFR-100L/COM20/voltage
  - run: pfr_100l
    args: --port COM20                # 指定 port,不掃描
    setting:
      - set_voltage 12
  - run: pfr_100l                     # 第二台:自動掃描,跳過 COM20
  - run: btm_4208sd
    setting:
      - set_channel ch3 SoC_heatsink
  - run: serial_dut
    args: --dut axelera_board
  - run: ssh_dut
    args: --dut axelera_board --host 192.168.1.50
ready_timeout: 30                     # 每個 node 最多等 30 s 變 ok
```

- 依列出的順序啟動,**等前一個 node 的 `status` 變成 `ok`(掃描完成、port 已佔住)才啟動下一個**。
- 某 node 超過 `ready_timeout` 仍未 `ok`:紅字警示,**繼續啟動下一個**,不整批中止。
- 各 node 的輸出加前綴合併顯示,前綴在 node 註冊完成後換成它真正的 service 名稱。
- 每個 node 的 `setting` 在它 ready 之後才送。**只能放 `describe` 標成 `setting` 的命令**
  —— `output_on` 那種動作寫進來,開檔就會送電。
- **node 是 UI 的子行程:關掉 UI 就是結束測試。** 以反向順序關閉,Log 最後關;
  Log 會先收到一個 `stop` 把檔案正常關好,才被關掉。
- UI 的「存成 launch 檔」會把目前的 node 與設定值寫成這種檔案。
- 不使用 eCAL Sys:它只能「等固定秒數」,不理解 `status` 語意,且需安裝完整版 eCAL。

## 4. Node 說明

### PFR-100L — 可程式化電源(GW Instek)

- RS-232/RS-485,一個 process 對應一台機器、一個 COM,不做 multi-drop。
- 0–50 V / 0–10 A,**額定 100 W**:`set` 時就擋掉 `V設定 × I限流 > 100 W`。
  **這道鎖是防手滑,不是安全機制** —— 安全靠儀器自己的 OVP/OCP,兩者不要混為一談。
- node 開機時**查詢**機器現況,不改動它。保護值該設多少由現場決定,程式不發表意見。
- 命令與週期輪詢必須走同一個序列化 I/O 佇列,否則回應會交錯;取樣上限約 10–20 Hz。
- 原廠文件:`docs/devices/PFR-100L/`(含 Programming Manual)。

### BTM-4208SD — 12 channel 熱電偶記錄器(Lutron)

- RS-232 **單向連續輸出**,9600 8N1,**不接受任何命令**;單位、取樣等設定只能在面板操作。
- 封包 16 字元:D15 起始、D14 = `4`、D13 = channel(`1`–`9`、`A`–`C`)、D12–D11 單位(`01` °C / `02` °F)、
  D10 正負號、D9 小數位數、D8–D1 讀值、D0 結尾(手冊 p.23–24)。
- Topic:`BTM-4208SD/<port>/ch1` … `ch12`;**沒插探棒(over range)的 channel 不 publish**。
- 詳見 [docs/devices/BTM-4208SD/README.md](docs/devices/BTM-4208SD/README.md)。

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

**只記錄選定的 topic,而且不產報告。**

| method | 說明 |
|---|---|
| `record <topic> ...` | 選要記錄哪些 topic。**整份清單一次給完,覆蓋前一次**;是 setting |
| `start <run_name>` | 建立 `logs/YYYY-MM-DD_HHMMSS_<run_name>/`,開始訂閱選定的 topic |
| `mark <text>` | 在時間軸打事件標記(報告上畫成垂直線) |
| `stop` | 關檔 |

| 檔案 | 內容 |
|---|---|
| `data.csv` | long format:`t, topic, name, value, unit`;**每筆立即 flush** |
| `console.log` | 選到的 DUT `raw` topic 的原始輸出 |

報告事後產:

```
uv run visualization logs/<run>/data.csv
```

由 `sync.py`(按 topic 分組,**不對齊、不重取樣**)+ `chart_template.html` 產生,
plotly 內嵌所以離線可看。UI 的「看報告」叫的是同一份實作。詳見 [docs/log.md](docs/log.md)。

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

- `cmd()` 送出的字串與 UI 按鈕送的完全相同,走同一個 `teststation.client`。
- Main-Control 本身的 service:`run <plan>`、`abort`,並 publish 執行進度。
- **abort 或腳本例外時**,runner 在 `finally` 裡關閉所有 PFR 輸出,並對 Log 下 `mark` 與 `stop`。

## 5. 命令來源平權

UI 與測試計畫送出的命令字串**完全相同**,走同一個 eCAL service。

共用的部分在 `teststation/client.py`:session、探索、service 呼叫、頻率統計、
payload 處理。**那一層不依賴 Qt** —— 測試計畫的 runner 是無頭腳本,
把它綁進 `ui/` 會逼 runner 去 import PySide6,或是再抄一份一模一樣的程式碼。

站台**沒有命令列工具**。曾經有過一個 `st`,砍掉了:它跟 UI 是同一件事的兩份實作。

## 6. 異常處理

| 情況 | 行為 |
|---|---|
| 裝置讀寫失敗(如 USB 被拔) | node 不結束,狀態 `reconnecting`,每 2 秒重開**同一個 COM**(不重新掃描) |
| 重連超過 **30 秒** | 狀態 `error: ...`,停止自動重試,node 仍存活;可用 `reconnect` 手動重試 |
| 計畫執行中某 node 非 `ok` 超過 10 秒或心跳消失 | UI 即時狀態**紅字警示**,並寫入 Log;**不中止計畫** |
| 使用者 abort / 腳本例外 | 關閉所有 PFR 輸出 → Log `mark` → Log `stop` |
| Log 當掉 | 因逐筆 flush,最多損失最後一行 |
| UI 被硬砍(`taskkill /F`) | 子 node 變孤兒,會佔住 COM。用工作管理員砍掉,或見 [TODO.md](TODO.md) |

## 7. Repo 結構

```
test-station/
├─ pyproject.toml / uv.lock
├─ README.md
├─ TODO.md
├─ src/teststation/
│  ├─ client.py        # 站台 client:session、探索、service 呼叫、頻率、payload。**不依賴 Qt**
│  ├─ visualization.py # uv run visualization <data.csv> → report.html
│  ├─ core/            # node 基底與共通契約:service、describe、status、activity、COM 掃描、payload、topic 分類
│  ├─ nodes/
│  │  ├─ pfr_100l/     # node.py、scpi.py、INTERFACE.md
│  │  ├─ btm_4208sd/   # node.py、protocol.py、INTERFACE.md
│  │  ├─ serial_dut/
│  │  ├─ ssh_dut/
│  │  └─ log/          # node.py、sync.py、report.py、chart_template.html、plotly.min.js
│  ├─ duts/            # DUT profile
│  ├─ control/         # Main-Control runner 與計畫用 client API
│  └─ ui/              # PySide6:app、bridge、panels、launch
├─ launch/             # 站台配置(*.yaml)
├─ plans/              # 測試計畫腳本
├─ tests/
├─ docs/
│  ├─ ui.md            # PySide6 UI:操作 + 程式碼規劃
│  ├─ log.md           # 記錄與報告:操作 + 程式碼規劃
│  ├─ devices/         # 各裝置的原廠手冊 + 操作文件 README.md(PFR-100L、BTM-4208SD)
│  └─ interactive_chart.html   # 報告長相的範例
└─ logs/               # run 輸出(.gitignore)
```

資料夾與模組名稱中,型號的連字號以底線取代(`pfr_100l`);topic 與 service 名稱保留原廠寫法(`PFR-100L`)。

`docs/` 底下每份文件都分成兩部分:前半給操作的人,後半是程式碼規劃。

## 8. 預設值(可調整)

| 項目 | 預設 |
|---|---|
| 量測 publish 頻率 | 各 node 以 `--interval` 指定,預設 1 s;BTM-4208SD 依儀器輸出節奏 |
| 狀態心跳 | 1 s |
| 重連間隔 / 逾時 | 2 s / 30 s |
| 異常警示門檻 | 非 `ok` 持續 10 s |
| launch 就緒逾時 | 30 s |
| Python | 3.13;若 `eclipse-ecal` 無對應 wheel 則鎖定 3.12 |
