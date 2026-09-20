# `st` —— 站台命令列

ROS 風格的命令列,站上所有操作的入口。

**`st` 沒有任何特權。** 它送出的命令字串,跟 UI 按鈕、測試計畫 `cmd()` 送的**完全一樣**
(README 1.2),走的也是同一個 eCAL service。所以 `st` 能做到的事 UI 都能做,反之亦然;
用 `st` 手動試通了,寫進測試計畫就會動。

程式碼在 [src/teststation/cli/](../src/teststation/cli/)。

---

## 命令一覽

| 命令 | 作用 |
|---|---|
| `st launch <yaml>` | 依序啟動整站 |
| `st node list` | 列出 node 與燈號 |
| `st service list` | 列出所有 service |
| `st service describe <svc>` | 列出某個 node 的命令清單 |
| `st service call <svc> "<命令>"` | 送一行純文字命令 |
| `st topic list` | 列出所有被發佈的 topic 與頻率 |
| `st topic echo <topic>` | 印出該 topic 的每一筆 |
| `st topic hz <topic>` | 量測發佈頻率 |
| `st topic pub <topic> '<json>'` | 發佈假資料 |
| `st console <svc>` | DUT 雙窗格 TUI |
| `st` | 互動模式(REPL) |

`st --help`、`st topic --help` 有完整選項。

---

## `st launch` —— 啟動整站

```
st launch launch/btm_only.yaml
```

```
--- starting btm_4208sd --port COM11
[BTM-4208SD/COM11] ready
--- 1 node(s) up, 0 未就緒。Ctrl+C 關閉整站
```

站台配置長這樣:

```yaml
nodes:
  - run: btm_4208sd
    args: --port COM11
  - run: btm_4208sd          # 第二台:不給 port,自動掃描,會跳過 COM11
ready_timeout: 30
```

**關鍵是它等的是 `status` 變 `ok`,不是等固定秒數。** node 沒給 `--port` 時會掃描所有 COM,
掃描期間會暫時佔住別人的 port;同時啟動會讓彼此誤判成「使用中」而跳過。所以下一個 node
必須等前一個掃完、port 佔住了才起。(這也是不用 eCAL Sys 的原因,見 README 3.1。)

其他行為:

- 某個 node 超過 `ready_timeout` 仍未 `ok`:**紅字警示,但繼續啟動下一個**,不整批中止。
- 各 node 的輸出加上前綴合併顯示,前綴在 node 註冊完成後會變成它真正的 service 名稱。
- Ctrl+C 以**反向順序**關閉,Log 最後關,確保它收得到其他 node 的最後資料。
- 離開碼:全部就緒 = 0,有任何一個沒就緒 = 1。

> **硬砍 launcher 會留下孤兒 node。** `taskkill /F` 或工作管理員的「結束工作」不給
> Python 執行關閉流程的機會,子 node 會活著佔住 COM,下次啟動就掃不到裝置。
> 發生時 `st node list` 看得到,手動砍掉即可。正常 Ctrl+C 不會有這問題。

---

## 查看站台狀態

```
$ st node list
NODE              STATUS  HOST             PID
----------------  ------  ---------------  -----
BTM-4208SD/COM11  ok      Liscotech-NB004  14160
```

`STATUS` 直接來自各 node 的 `status` topic(1 Hz 心跳):`ok` / `reconnecting` /
`error: ...`。顯示 `no heartbeat` 代表 service 註冊得到但心跳收不到 —— node 卡住了。

```
$ st topic list
TOPIC                      HZ    NODE              HOST
-------------------------  ----  ----------------  ---------------
BTM-4208SD/COM11/activity  1.14  BTM-4208SD_COM11  Liscotech-NB004
BTM-4208SD/COM11/ch1       0.57  BTM-4208SD_COM11  Liscotech-NB004
BTM-4208SD/COM11/status    1.00  BTM-4208SD_COM11  Liscotech-NB004
```

**只有正在發佈的 topic 會出現。** BTM-4208SD 沒插探棒的 channel 不 publish,所以
`ch2` 之類看不到是正常的。

---

## 下命令

```
$ st service describe BTM-4208SD/COM11
METHOD       ARGS              LABEL       STYLE
-----------  ----------------  ----------  -----
describe                       命令清單
set_channel  <channel> <name>  命名 channel
reconnect                      重新連線

$ st service call BTM-4208SD/COM11 "set_channel ch3 SoC_heatsink"
OK ch3 = SoC_heatsink
```

回覆一律以 `OK` 或 `ERR` 開頭。`ERR` 時離開碼是 1,可以直接寫進腳本判斷。

先 `describe` 再 `call` 是通則 —— 每個 node 的命令都由它自己宣告,不用記。

---

## 看資料

```
$ st topic echo BTM-4208SD/COM11/ch1
2026-09-20 12:16:33.298  32.5 °C  (ch1)
2026-09-20 12:16:35.057  32.5 °C  (ch1)
```

括號裡是 `name`(用 `set_channel` 命名過就會變)。`-n 3` 收滿三筆就停,適合寫進腳本。

`echo` 對三種 topic 都能用,格式自動判斷:量測值、`activity` 事件、以及 `status`
那種純文字。

```
$ st topic hz BTM-4208SD/COM11/ch1
average rate:   0.567  min: 1.765s  max: 1.765s  std dev: 0.00000  window: 2
```

開頭幾秒會印 `no new messages` —— eCAL 的訂閱配對要約 2 秒,不是壞掉。

---

## 餵假資料

還沒接實機、或要單獨測 Log 與 UI 時:

```
st topic pub test/fake/ch1 '{"v": 1.0}'                    # 送一筆
st topic pub test/fake/ch1 '{"v": 1.0}' -r 2 -n 20         # 2 Hz 送 20 筆
```

payload 少了 `t` 會自動補上現在時間,所以只寫 `{"v": 1.0}` 就符合 README 2.2 的 schema。

---

## 互動模式

直接打 `st` 進入 REPL:

```
st> service call BTM-4208SD/COM11 set_ch<Tab>
```

Tab 補完的來源是**站上實際跑著的東西**,不是寫死的清單:

| 補完位置 | 來源 |
|---|---|
| service / topic 名稱 | eCAL 的 monitoring 註冊資訊 |
| method 名稱 | 對該 service 呼叫 `describe` |

命令歷史存在 `~/.st_history`。

`st` 也吃管線輸入,可以寫成腳本:

```
printf 'node list\ntopic echo BTM-4208SD/COM11/ch1 -n 1\nexit\n' | st
```

---

## `st console` —— DUT 雙窗格

```
st console Serial/COM5
```

上半是 DUT 原始收發(`raw` topic,以及你輸入後送出的 `send <text>`),
下半是 node 解析後 publish 的值與收到的命令(`activity` topic)。

調整 DUT profile 的 `parse()` 時用來對照:**上面有這行、下面沒有對應的 `PUB`,
就是 `parse()` 沒接到。**

對沒有 `raw` topic 的儀器 node(例如 BTM-4208SD)也能開,但上半部只會有你送出的東西
—— `raw` 是 DUT node 專屬的。

---

## 已知限制

- `st console` 的 `raw` 窗格與送出路徑,要等 Serial / SSH DUT node 做好才能真的用上。
- `st launch` 被硬砍時會留孤兒 node,見上面的警告。
