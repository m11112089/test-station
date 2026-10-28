# PFR-100L — 可程式化電源(GW Instek)

0–50 V / 0–10 A,**額定 100 W**(multi-range:電壓與電流的乘積受 100 W 限制)。
SCPI 控制,接法用 **USB-CDC(後面板 USB Type B)** 或 **RS232(Remote-IN)**,
兩種在 PC 上都是一個 COM,node 程式碼相同。

| | |
|---|---|
| 程式碼 | [src/teststation/nodes/pfr_100l/](../../../src/teststation/nodes/pfr_100l/) |
| 契約(命令表 / topic / payload) | [INTERFACE.md](../../../src/teststation/nodes/pfr_100l/INTERFACE.md) |
| 原廠手冊 | [PFR-100_Programming manual_EN_V2_20260714.pdf](PFR-100_Programming%20manual_EN_V2_20260714.pdf)(面板 p.12–20、介面 p.28–44、SCPI p.70–124) |
| 待補的實機驗證 | [TODO.md](../../../TODO.md) |

SCPI 收發、錯誤確認、100 W 鎖這些**實作細節,寫在
[scpi.py](../../../src/teststation/nodes/pfr_100l/scpi.py) 的 docstring 裡**,
這份文件不重複——那些東西會跟程式碼一起變,放在這裡一定會過期。

---

## 1. 上線前:面板設定

### 1.1 選介面:`F-29`

機器一次只開一種遠端介面,由功能設定 `F-29` 決定。**介面沒切對,儀器完全不回話**,
node 只會看到逾時。`F-29` 不是實體開關,是前面板功能選單的第 29 項(手冊 p.19–20、p.22):

1. 拔負載、關 Output。
2. 按 **Function** 鍵(螢幕下方按鍵列最左邊,鍵下小字 M1)。鍵燈亮,螢幕上排顯示 `F-01`。
3. 轉 **Voltage 旋鈕**(右上),直到上排顯示 `F-29`。
4. 轉 **Current 旋鈕**(右下)改下排的值:

   | 值 | 介面 | 本站支援 |
   |---|---|---|
   | 0 | 關閉 | |
   | 1 | RS232(Remote-IN,RJ-45) | ✔ |
   | 2 | RS485(Remote-IN,RJ-45) | |
   | **3** | **USB-CDC(後面板 USB Type B)** | ✔ |
   | 4 | GPIB(原廠選配) | |
   | 5 | LAN Socket(TCP 2268) | |
   | 6 | LAN Web | |

5. **按一下 Voltage 旋鈕**存檔,螢幕閃 `ConF`。
6. 再按 **Function** 鍵離開,鍵燈熄。

按鍵沒反應:面板被鎖就**長按** Lock/Local;RMT 燈亮(正在被遠端控制)就**短按** Lock/Local 回本機。

### 1.2 接線

| 接法 | 線 | 面板設定 | 手冊 |
|---|---|---|---|
| **USB-CDC** | USB A ↔ B(GTL-246,一般印表機線即可),插**後面板**的 Type B | `F-29 = 3`;`F-21 = 1` 表示已連上 PC | p.28 |
| **RS232** | PSU-232(RJ-45 ↔ DB9),插 **Remote-IN**(不是 Remote-OUT) | `F-29 = 1`;`F-71` 鮑率(出廠 7 = 115200)、`F-72`~`F-74` = 8N1 | p.41–43 |

* USB-CDC 第一次插上時,Windows 裝置管理員 →「連接埠 (COM 和 LPT)」會多一個 COM。
  沒出現就裝 GW Instek 官網的 USB 驅動程式。
* USB-CDC **不看鮑率**;RS232 的 `F-71` 不是 115200 時,node 要加 `--baud`。
* 開 USB 遠端時,**前面板的 USB A 口會停用**(手冊 p.28)。

### 1.3 保護與開機行為

node **只查詢、不設定**保護值 —— 該設多少由現場決定,程式不發表意見(README 4)。上線前在面板確認:

| 項目 | 說明 | 手冊 |
|---|---|---|
| OVP / OCP | 按 **PROT** 鍵設定。出廠 = 1.1 × 額定(55 V / 11 A),多半要依 DUT 調低 | p.98、p.102、p.146 |
| `F-92` Power ON Output | 出廠 0 = Safe Mode,**開機時 Output 是 OFF**。別改成開機自動送電 | p.146 |

node 的 100 W 鎖只是防手滑,**真正的保護靠 OVP / OCP**。

---

## 2. 啟動

平常由 UI 的「啟動整站」帶起來。要單獨跑(除錯、驗機)就走模組路徑:

```
uv run python -m teststation.nodes.pfr_100l.node --port COM20   # 指定 port
uv run python -m teststation.nodes.pfr_100l.node                # 自動掃描
```

```
scanning for PFR-100L (*IDN? at 115200 baud)...
  COM11: no match
  COM20: match
found on COM20 (USB-CDC)
PFR-100L/COM20 starting
```

掃描時在每個 port 送一行 `*IDN?`,回應含 `PFR-100L` 才算數。`*IDN?` 是標準查詢,
打到別台儀器頂多回一行身分,打到 DUT console 也只是一行看不懂的字。
開不起來的 port(被別的 process 佔住)直接跳過。

| 選項 | 預設 | 說明 |
|---|---|---|
| `--port` | 掃描 | 指定 COM |
| `--baud` | 115200 | RS232 鮑率,要跟 `F-71` 一致;USB-CDC 不看 |
| `--interval` | 1.0 s | 量測輪詢間隔 |

驗機用的最小站台是 [launch/pfr_only.yaml](../../../launch/pfr_only.yaml):只起這一台、自動掃描、
不帶任何設定。UI「啟動整站」分頁選它即可。

自己寫 launch 檔的寫法:

```yaml
nodes:
  - run: pfr_100l
    args: --port COM20          # RS232 非 115200 時加 --baud 9600 之類
    setting:
      - set_current 2
      - set_voltage 12
```

`setting` 只放 `set_voltage` / `set_current`。**`output_on` 不能寫進來** —— 開檔就會送電。

整站請用 UI 的「啟動整站」分頁,**不要手動同時啟動多個 node** —— 掃描期間會暫時佔住別人的 port,
會害對方誤判成「使用中」而跳過。

---

## 3. 驗證有沒有活著

1. 面板 **RMT** 燈亮。
2. 開 UI(`uv run ui`),左邊的 **Node 清單**出現 `PFR-100L/COM20`,燈號是綠的。
3. **DUT console** 分頁下半部(activity)會有連線當下讀到的機器現況:

   ```
   SYS  GW-INSTEK PFR-100L SN TW1234567 FW 01.01.12345678 via USB-CDC
   SYS  set 12 V / 2 A, output OFF, OVP 55 V, OCP 11 A
   ```

   `via USB-CDC` / `via RS232` 是這次走的接法(USB 轉 RS232 線算 RS232)。

4. 下面的**即時數值表**每秒一筆,是**量測值**不是設定值 —— Output OFF 時都是 0:

   ```
   TIME                     TOPIC                     NAME     VALUE  UNIT
   2026-10-28 14:02:11.031  PFR-100L/COM20/voltage    voltage  12.0   V
   2026-10-28 14:02:11.031  PFR-100L/COM20/current    current  0.85   A
   2026-10-28 14:02:11.031  PFR-100L/COM20/power      power    10.2   W
   ```

---

## 4. 操作

UI 的**命令**分頁會照這個 node 的 `describe` 自動長出按鈕:

| 按鈕 | 命令 | 說明 |
|---|---|---|
| 設定限流 | `set_current <a>` | 0–10 A |
| 設定電壓 | `set_voltage <v>` | 0–50 V |
| 開始送電 | `output_on` | |
| 停止送電 | `output_off` | 紅色按鈕 |
| 解除保護 | `clear_protection` | OVP / OCP / OTP 跳脫後解除(AC fail 解不掉) |

**順序建議:先設限流、再設電壓、最後開始送電。**

* `電壓 × 限流 > 100 W` 的設定會被擋下,回 `ERR power limit 100W exceeded: ...`,**什麼都沒送到機器**。
  例如限流已經是 10 A,就不能把電壓設到 12 V —— 先把限流降下來。
* 機器不收的命令會回 `ERR ... rejected: <code> <msg>`(node 每道設定都會查 `:SYST:ERR?` 確認)。
* 回 `OK` 才表示機器真的改了。

測試計畫裡是同樣的字串:

```python
cmd("PFR-100L/COM20", "set_current 2")
cmd("PFR-100L/COM20", "set_voltage 12")
cmd("PFR-100L/COM20", "output_on")
wait_until("PFR-100L/COM20/current", lambda a: a > 0.5, timeout=10)
```

channel 也可以命名:`set_channel voltage DUT_12V`,之後 payload 的 `name` 與報告上都顯示 `DUT_12V`。

---

## 5. 故障排除

| 症狀 | 原因 / 處理 |
|---|---|
| 掃描找不到,錯誤訊息提示 `F-29` | 介面沒切對:USB 要 `F-29 = 3`,RS232 要 `F-29 = 1` |
| USB 插了但沒有新的 COM | `F-29` 不是 3,或缺驅動程式。看 `F-21` 是不是 1 |
| RS232 `--port` 指定了還是逾時 | `F-71` 鮑率跟 `--baud` 不一致;或線插到 Remote-OUT;或 PSU-232 沒插到底 |
| 回 `ERR not connected` | node 還沒連上(status 不是 `ok`),等它連上再下命令 |
| 回 `ERR power limit 100W exceeded` | 正常:100 W 鎖。先降另一個值再設 |
| Output 開了但電壓上不去 | 進了 CC(限流太低),或保護跳脫。看面板 ALM 燈;跳脫後按「解除保護」 |
| status 一直 `reconnecting` | port 被別的 process 佔住,或 USB 被拔。每 2 秒重開**同一個 COM** |
| 拔插 USB 後一直連不回來 | Windows 把它配到了別的 COM 號。看裝置管理員,改 `--port` |
| status 變 `error: ...` | 重連超過 30 秒,已停止自動重試。排除後在 UI 的命令分頁按「重新連線」 |
| 面板按鍵都不能按 | 遠端控制中(RMT 燈亮)。短按 Lock/Local 回本機 |

---

## 6. 測試

```
uv run pytest
```

* [tests/test_pfr_100l.py](../../../tests/test_pfr_100l.py) —— SCPI 解析、100 W 鎖、
  `:SYST:ERR?` 確認、拔線進重連、`F-29` 沒切對的錯誤提示。儀器用 `FakePfr`(假的
  `serial.Serial`)代替,**USB-CDC 與 RS232 兩種接法各跑一次**。
* [tests/test_node_contract.py](../../../tests/test_node_contract.py) —— 真的跑一個
  eCAL node 起來,驗 `describe` / `set_channel` / payload 格式。裝置部分用 stub。

**還沒接過實機**,哪些情境要上機才驗得到,見 [TODO.md](../../../TODO.md)。
