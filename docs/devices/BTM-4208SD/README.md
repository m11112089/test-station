# BTM-4208SD — 12 channel 熱電偶記錄器(Lutron)

這台儀器 RS-232 **單向連續輸出**,9600 8N1,**不接受任何命令**。
單位(°C/°F)、取樣時間等設定只能在面板操作,node 讀到什麼就發什麼。

| | |
|---|---|
| 程式碼 | [src/teststation/nodes/btm_4208sd/](../../../src/teststation/nodes/btm_4208sd/) |
| 契約(命令表 / topic / payload) | [INTERFACE.md](../../../src/teststation/nodes/btm_4208sd/INTERFACE.md) |
| 原廠手冊 | [BTM-4208SDeop.pdf](BTM-4208SDeop.pdf)(RS232 在 p.22–24) |
| 待補的實機驗證 | [TODO.md](../../../TODO.md) |

封包格式、對齊策略、over range 的實際編碼這些**實作細節,寫在
[protocol.py](../../../src/teststation/nodes/btm_4208sd/protocol.py) 的 docstring 裡**,
這份文件不重複——那些東西會跟程式碼一起變,放在這裡一定會過期。

---

## 1. 上線前:先確認面板設定

儀器設定只能在面板做(手冊 §7):

| 面板項目 | 說明 | 手冊 |
|---|---|---|
| `t-CF` | 溫度單位 C / F。**會直接反映在 payload 的 `unit`**,node 不做換算 | §7-6 |
| `SP-t` | 取樣時間。影響 SD 卡記錄,RS232 仍是連續輸出 | §7-7 |
| `PoFF` | Auto Power Off。**長時間 burn-in 前務必設成 `no`**,否則機器會自己關機 | §7-4 |

用 DC 9V 變壓器供電時機器會常時開機(電源鍵失效),長時間測試建議這樣接(手冊 §8)。

---

## 2. 啟動

```
uv run btm_4208sd --port COM11      # 指定 port
uv run btm_4208sd                   # 自動掃描
```

```
scanning for BTM-4208SD (listen-only, 3s per port)...
  COM11: match
found on COM11
BTM-4208SD/COM11 starting
```

掃描時**完全不送任何東西**,只聽 3 秒有沒有合格封包,所以掃到別台裝置或 DUT 的
console 也不會有任何影響。開不起來的 port(被別的 process 佔住)直接跳過。

| 選項 | 預設 | 說明 |
|---|---|---|
| `--port` | 掃描 | 指定 COM |
| `--scan-timeout` | 3.0 s | 掃描時每個 port 聽多久 |
| `--data-timeout` | 10.0 s | 多久沒收到合格封包視為斷線 |
| `--sniff` | off | 只印原始封包,不啟動 eCAL node |

整站請用 UI 的「啟動整站」分頁,**不要手動同時啟動多個 node**——掃描期間會暫時佔住別人的 port,
會害對方誤判成「使用中」而跳過。

---

## 3. 驗證有沒有活著

開 UI(`uv run ui`),左邊的 **Node 清單**會出現 `BTM-4208SD/COM11`,燈號是綠的。
下面的**即時數值表**只會列出有插探棒的 channel:

```
TOPIC                  NAME  VALUE  UNIT  UPDATED
BTM-4208SD/COM11/ch1   ch1   31.0   °C    2026-09-20 00:41:30.524
```

**沒插探棒的 channel 完全不 publish**,連 topic 都不會出現。
插上探棒後會自己出現,不用重啟 node。

發佈節奏依儀器輸出,沒有 `--interval`。實測一輪 12 個 channel 約 2 秒。

---

## 4. 命名 channel

在 UI 的**命令**分頁,`set_channel` 那一列填 `ch3` 與 `SoC_heatsink` 再按按鈕
(按鈕是照這個 node 的 `describe` 自動長出來的)。

之後 payload 的 `name` 就變成 `SoC_heatsink`,報告上看到的也是這個名字。
測試計畫裡同樣一句:

```python
cmd("BTM-4208SD/COM11", "set_channel ch3 SoC_heatsink")
wait_until("BTM-4208SD/COM11/ch3", lambda v: v > 45, timeout=1800)
```

---

## 5. `--sniff`:直接看原始封包

上線前、換機、或懷疑解析不對時用。不啟動 eCAL、不佔 topic:

```
uv run btm_4208sd --port COM11 --sniff
```

```
b'\x0241010100000309\r'  ch1  30.9 °C
b'\x024501010000\x18\x18\x18\x18\r'  ch5  over range
```

**如果換了新機器、某個 channel 的封包印不出來**(被當成雜訊跳過),多半是那台的
over range 編碼不一樣。把 `--sniff` 看到的填充字元回報給改 `protocol.py` 的人,
或自己加進 `OVER_RANGE_CHARS`。

---

## 6. 故障排除

| 症狀 | 原因 / 處理 |
|---|---|
| 掃描找不到,`--port` 也收不到資料 | 機器沒開、或 3.5 mm 線沒插到底。先 `--sniff` 確認有沒有 byte 進來 |
| 即時數值表少了某些 channel | 正常:沒插探棒的 channel 不 publish。插上探棒後會自己出現 |
| status 一直 `reconnecting` | port 被別的 process 佔住,或 USB 轉接線鬆脫。每 2 秒重試一次 |
| status 變 `error: ...` | 重連超過 30 秒,已停止自動重試。排除後在 UI 的命令分頁按「重新連線」 |
| `error: no valid frame for 10s` | 機器開著但沒在送(Auto Power Off 關機了?),或訊號線鬆脫。這台只進不出,拔訊號線時 port 還在、read 不會失敗,只能靠「多久沒資料」判斷 |
| 單位拿到 `°F` | 面板 `t-CF` 設定,node 不做換算 |
| 掃描掃到別台裝置的 port | 正常會跳過;若某台裝置剛好送出結構相符的封包,改用 `--port` 指定 |
| 某個 channel 的封包 `--sniff` 印不出來 | 該機型的 over range 編碼不同,見上一節 |

---

## 7. 測試

```
uv run pytest
```

* [tests/test_btm_protocol.py](../../../tests/test_btm_protocol.py) —— 封包解析,
  含**實機擷取的位元組**當回歸測試資料。
* [tests/test_node_contract.py](../../../tests/test_node_contract.py) —— 真的跑一個
  eCAL node 起來,驗 `describe` / `set_channel` / payload 格式。裝置部分用 stub。

哪些情境還沒在實機上跑過,見 [TODO.md](../../../TODO.md)。
