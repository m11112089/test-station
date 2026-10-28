# PEL-3031E — 直流電子負載(GW Instek)

1–150 V / 0–60 A / **300 W**,單通道。SCPI 控制,遠端介面只有
**USB-CDC(後面板 USB Type B)**;GPIB 是選配、本站不支援;這台**沒有 RS232**。

| | |
|---|---|
| 程式碼 | [src/teststation/nodes/pel_3031e/](../../../src/teststation/nodes/pel_3031e/) |
| 契約(命令表 / topic / payload) | [INTERFACE.md](../../../src/teststation/nodes/pel_3031e/INTERFACE.md) |
| 使用者手冊 | [gw-instek-pel-3031e_manual_763360.pdf](gw-instek-pel-3031e_manual_763360.pdf)(面板 p.12–19、遠端 p.201–211、規格 p.229–235) |
| 程式手冊 | [PEL-3000E_Programming_Manual_V1.11.pdf](PEL-3000E_Programming_Manual_V1.11.pdf)(介面 p.5–14、命令 p.15–150) |
| 待補的實機驗證 | [TODO.md](../../../TODO.md) |

程式手冊是從 GW Instek 官網下載的(使用者手冊不含命令表)。
SCPI 收發、錯誤確認、數值解析這些**實作細節,寫在
[core/scpi.py](../../../src/teststation/core/scpi.py) 與
[node.py](../../../src/teststation/nodes/pel_3031e/node.py) 的 docstring 裡**,這份文件不重複。

---

## 1. 上線前:面板設定

### 1.1 選介面:Interface = USB

程式手冊 p.5:

1. 用 USB A ↔ B 線(GTL-246,一般印表機線即可)接 PC 與**後面板**的 USB Type B。
   前面板那個是 USB A,只能插隨身碟,不能拿來遠端控制。
2. 按 **Shift**,再按 **Help**(Shift + Help = **Utility**)。
3. 按螢幕下方的 **Interface [F3]** 軟鍵。
4. 把 Interface 設成 **USB**。

第一次插上時,Windows 裝置管理員 →「連接埠 (COM 和 LPT)」會多一個 COM。
沒出現就裝驅動程式(隨機光碟,或 GW Instek 官網)。USB **不看鮑率**,node 沒有 `--baud`。

### 1.2 保護設定

node **只查詢、不設定**保護值 —— 該設多少由現場決定,程式不發表意見。
上線前在面板 **Main > Configure [F5] > Protection [F1]** 確認(使用者手冊 p.72–77、p.217):

| 項目 | 出廠 | 說明 |
|---|---|---|
| OCP Level / Setting | 最大值 / `LIMIT` | `LIMIT` 是限流繼續拉載,`LOFF` 是跳脫停止拉載 |
| OPP Level / Setting | 最大值 / `LIMIT` | 同上,針對功率 |
| OVP | `OFF` | 輸入電壓過高時保護 |
| UVP | `OFF` | 輸入電壓過低時保護 —— 做電池放電時多半要設 |

### 1.3 range

current range(H 0–60 A / L 0–6 A)與 voltage range(H 150 V / L 15 V)node 不切換,
用面板目前的設定(出廠都是 **H**)。L range 解析度較好,但 `set_current 10` 這種超過 L 上限的值
會被儀器拒收,回 `ERR ... rejected: -222 Data out of range (:CURR:VA)`。

---

## 2. 啟動

平常由 UI 的「啟動整站」帶起來。要單獨跑(除錯、驗機)就走模組路徑:

```
uv run python -m teststation.nodes.pel_3031e.node --port COM21   # 指定 port
uv run python -m teststation.nodes.pel_3031e.node                # 自動掃描
```

```
scanning for PEL-3031E (*IDN?)...
  COM20: no match
  COM21: match
found on COM21
PEL-3031E/COM21 starting
```

掃描時在每個 port 送一行 `*IDN?`,回應含 `PEL-3031E` 才算數。開不起來的 port
(被別的 process 佔住)直接跳過。

| 選項 | 預設 | 說明 |
|---|---|---|
| `--port` | 掃描 | 指定 COM |
| `--interval` | 1.0 s | 量測輪詢間隔 |

驗機用的最小站台是 [launch/pel_only.yaml](../../../launch/pel_only.yaml):只起這一台、自動掃描、
不帶任何設定。UI「啟動整站」分頁選它即可。

自己寫 launch 檔的寫法:

```yaml
nodes:
  - run: pel_3031e
    args: --port COM21
    setting:
      - set_current 2.5
      - set_mode CC              # 模式放最後一行:設 CC / CP 值會順便切模式
```

`setting` 只放 `set_mode` / `set_current` / `set_voltage` / `set_power`。
**`load_on` 不能寫進來** —— 開檔就會開始拉載。

整站請用 UI 的「啟動整站」分頁,**不要手動同時啟動多個 node** —— 掃描期間會暫時佔住別人的 port,
會害對方誤判成「使用中」而跳過。

---

## 3. 驗證有沒有活著

1. 面板顯示進入遠端狀態(按 **Shift + Main**(Local)可回本機)。
2. 開 UI(`uv run ui`),左邊的 **Node 清單**出現 `PEL-3031E/COM21`,燈號是綠的。
3. **DUT console** 分頁下半部(activity)會有連線當下讀到的機器現況,原樣照錄儀器的回應:

   ```
   SYS  GW PEL-3031E SN GES130082 FW V1.03.002 via USB-CDC
   SYS  mode CR, load OFF, range I Low, set CC 0.5600 / CV 16.500 / CP 1.700,
        OCP LIMIT, 63.000, OPP LIMIT, 315.00, OVP OFF, UVP 0.000
   ```

4. 下面的**即時數值表**每秒一筆,是**量測值**不是設定值。沒拉載時電流、功率是 0,
   電壓是接在輸入端那台電源的電壓:

   ```
   TIME                     TOPIC                      NAME     VALUE  UNIT
   2026-10-28 16:40:02.115  PEL-3031E/COM21/voltage    voltage  12.0   V
   2026-10-28 16:40:02.115  PEL-3031E/COM21/current    current  2.5    A
   2026-10-28 16:40:02.115  PEL-3031E/COM21/power      power    30.0   W
   ```

---

## 4. 操作

UI 的**命令**分頁會照這個 node 的 `describe` 自動長出按鈕:

| 按鈕 | 命令 | 說明 |
|---|---|---|
| 操作模式 CC/CV/CP | `set_mode <mode>` | `CC` 定電流、`CV` 定電壓、`CP` 定功率 |
| CC 電流 | `set_current <a>` | 0–60 A |
| CV 電壓 | `set_voltage <v>` | 0–150 V |
| CP 功率 | `set_power <w>` | 0–300 W |
| 開始拉載 | `load_on` | |
| 停止拉載 | `load_off` | 紅色按鈕 |

**順序建議:先設值、再選模式、最後開始拉載。** 模式放在值之後,就不會被「設值順便切模式」蓋掉。

* **`set_current` 會順便把模式切到 CC,`set_power` 會切到 CP;`set_voltage` 不切換。**
  這是實機(FW V1.03.002)量到的,手冊沒寫。**拉載中改值要特別注意** ——
  CC 拉載時下 `set_power` 會立刻變成 CP 拉載。
* **拉載中用 `set_mode` 真的換模式,儀器會自己把 load 關掉**(設值順便換模式則不會)。
  回覆會寫 `(load turned off by the mode change; load_on to resume)`,要再按一次「開始拉載」。
* **CP 在 Low range、低功率時不可靠,先別用。** 2026-10-28 實測(PFR-100L 5 V 供電,CP 2 W):
  10 次裡 8 次**崩潰** —— PEL 把電流拉到電源限流(1 A 或 2 A 都一樣),自己端電壓掉到 ~0 V,
  像短路;另外 2 次能拉,但量到 3.25 W,跟設定差很多。精度規格本來就只有
  ±(0.6% 設定值 + 1.4% × 300 W) ≈ ±4.2 W。原因還沒查(可能跟 `:POWer:RESPonse` 有關),
  見 TODO。要定電流就用 CC,CC 與 CV 實測都正常。
* 機器不收的命令會回 `ERR ... rejected: <code> <msg>`(node 每道設定都會查 `:SYST:ERR?` 確認),
  **什麼都沒變**。回 `OK` 才表示機器真的改了。
* CR(定電阻)模式沒有開放。要用再說 —— CR 的單位出廠是 mS(電導),跟歐姆混用很容易設錯。

測試計畫裡是同樣的字串,例如搭配 PFR-100L 做 12 V / 2.5 A 拉載:

```python
cmd("PFR-100L/COM20", "set_current 3")
cmd("PFR-100L/COM20", "set_voltage 12")
cmd("PFR-100L/COM20", "output_on")
cmd("PEL-3031E/COM21", "set_current 2.5")
cmd("PEL-3031E/COM21", "set_mode CC")
cmd("PEL-3031E/COM21", "load_on")
wait_until("PEL-3031E/COM21/current", lambda a: a > 2.4, timeout=10)
```

---

## 5. 故障排除

| 症狀 | 原因 / 處理 |
|---|---|
| 掃描找不到,錯誤訊息提示 `Interface [F3] = USB` | Utility 裡的 Interface 不是 USB,或線插到前面板。見 1.1 |
| USB 插了但沒有新的 COM | 缺驅動程式,或 Interface 不是 USB |
| `set_current` 回 `ERR ... -222 Data out of range` | 超過目前 range 的上限(L range 只到 6.12 A)。改面板 range 或降值 |
| 設了值但電流沒變 | 模式是 CV:`set_voltage` 不會切模式,CV 下改 CC 值以外的東西看不出來。看 activity 的 `mode` |
| 模式自己變了 | 正常:`set_current` 切到 CC、`set_power` 切到 CP(實機行為) |
| 換模式後電流變 0 | 正常:真的換模式時儀器會關 load。再 `load_on` |
| CP 拉載時電壓掉到 ~0 V、電流卡在電源限流 | CP 崩潰(見第 4 節)。立刻 `load_off`,改用 CC |
| 回 `ERR not connected` | node 還沒連上(status 不是 `ok`),等它連上再下命令 |
| 拉載中電流被限住 | OCP / OPP 設成 `LIMIT` 而且到了上限,或輸入電源本身的限流 |
| status 一直 `reconnecting` | port 被別的 process 佔住,或 USB 被拔。每 2 秒重開**同一個 COM** |
| 拔插 USB 後一直連不回來 | Windows 把它配到了別的 COM 號。看裝置管理員,改 `--port` |
| status 變 `error: ...` | 重連超過 30 秒,已停止自動重試。排除後在 UI 的命令分頁按「重新連線」 |
| 面板按鍵都不能按 | 遠端控制中。按 **Shift + Main**(Local)回本機(使用者手冊 p.12) |

---

## 6. 測試

```
uv run pytest
```

* [tests/test_pel_3031e.py](../../../tests/test_pel_3031e.py) —— 模式與設定值、量測輪詢、
  儀器拒收時回 `ERR` 且不變、拔線進重連、Interface 沒設對的錯誤提示。儀器用 `FakePel`
  (假的 `serial.Serial`)代替,照手冊模擬「查詢帶單位」與「OVP / UVP 回 `OFF`」。
* [tests/test_node_contract.py](../../../tests/test_node_contract.py) —— 真的跑一個
  eCAL node 起來,驗 `describe` / `set_channel` / payload 格式。裝置部分用 stub。

**還沒接過實機**,哪些情境要上機才驗得到,見 [TODO.md](../../../TODO.md)。
