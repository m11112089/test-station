# PySide6 UI

站台的圖形介面。

**通用 UI,不為個別裝置撰寫畫面。** 程式碼裡沒有一行認識 BTM-4208SD 或 PFR-100L:
按鈕是照各 node 回報的 `describe` 長出來的,數值表是照資料 topic 長出來的。
**新增一種裝置不用改 UI。**

UI 送出的命令字串與測試計畫送的完全相同,走同一個 eCAL service
(README 1.2:GUI 沒有特權)。共用的部分在
[teststation/client.py](../src/teststation/client.py),**那一層不依賴 Qt** ——
測試計畫的 runner 是無頭腳本,也要用它。

程式碼在 [src/teststation/ui/](../src/teststation/ui/)。**這是站台唯一的人機介面**,
沒有命令列工具。

> **第一部分:怎麼用**(到「已知限制」為止)
> **[第二部分:程式碼規劃](#第二部分程式碼規劃)**

---

## 啟動

```
uv run ui
```

UI 自己開 eCAL session,**而且會順手把 Log node 帶起來** —— 忘記啟動它的代價是
整場測試沒錄到。儀器則用「啟動整站」分頁。

## 畫面配置

```
┌─────────────┬──────────────────────────────────────────────────┐
│ Node │ 命令 │ DUT console │ 測試計畫 │ 記錄 │ topic 工具 │ 啟動整站 │
│             ├──────────────────────────────────────────────────┤
│ BTM-4208SD  │  [命令清單] [命名 channel ch_ name_] [重新連線]    │
│  /COM11  ●  │  自由輸入 ______________________________________  │
│             │  歷史                                            │
│             │  BTM-4208SD/COM11 > set_channel ch3 SoC          │
│             │  OK ch3 = SoC                                    │
├─────────────┴──────────────────────────────────────────────────┤
│ 即時數值                                                        │
│ TOPIC                  NAME  VALUE  UNIT  UPDATED              │
│ BTM-4208SD/COM11/ch1   SoC   32.7   °C    13:27:11.852         │
│ BTM-4208SD/COM11/ch3   ch3   38.5   °C    13:27:12.356         │
└────────────────────────────────────────────────────────────────┘
```

左右、上下都是可拖的分隔線。

## 各塊各自做什麼

**Node 清單(左)** —— 從 eCAL 註冊資訊自動列出。字的顏色就是燈號,滑鼠移上去看完整狀態:

| 顏色 | 狀態 |
|---|---|
| 綠 | `ok` |
| 橘 | `reconnecting` |
| 紅 | `error: ...` |
| 灰 | 註冊得到但收不到心跳 —— node 卡住了 |

選哪個 node,「命令」與「DUT console」分頁就跟著換。重新整理時**選擇會保留**。

**點 Log 會切到「記錄」分頁**,不是停在通用命令按鈕上 —— 那裡才有勾選清單。
選回一般裝置就切回「命令」。正在看別的分頁時點 node 不會被打斷。

**命令** —— 按鈕與輸入框是按下 node 之後跟它要 `describe` 長出來的:

- 沒有參數 → 一個按鈕
- 有 `min`/`max` 的參數 → 帶範圍限制的數字框,**打不出超出範圍的值**
- 其他參數 → 文字框
- `"style": "danger"` → 紅色按鈕

下面的自由輸入列送什麼就是什麼,跟測試計畫的 `cmd()` 一模一樣。歷史區的回覆綠色是 `OK`、紅色是 `ERR`。

**即時數值(下)** —— 所有資料 topic 的最新值。一個 topic 一列,不會愈長愈多。

`TIME` 放第一欄,而且是 **node 在量測當下蓋的時間戳**,不是 UI 收到的時間。
放最左邊是因為它固定 23 個字 —— 擺在最後會被吃掉剩餘寬度的 `TOPIC` 壓成一半。
其餘各欄貼合內容,所以不會有東西被切掉。
`status`、`activity`、`raw` 這三個共通 topic 不會進來。
`NAME` 欄會跟著 `set_channel` 立刻改變。

**DUT console** —— 雙窗格,上半 DUT 的 `raw` 原始收發、下半 node 的 `activity`。
調整 DUT profile 的 `parse()` 時用來對照:上面有這行、下面沒有對應的 `PUB`,
就是 `parse()` 沒接到。

**topic 工具** —— 量某個 topic 的發佈頻率、發佈假資料。統計在
`client.rate_of()`、payload 處理在 `client.prepare_payload()`,
這個面板只負責選 topic 與顯示。

**測試計畫** —— 列出 `plans/*.py`,Run / Abort 送給 `Main-Control`。

**記錄** —— 「要記錄哪些 topic」,**依 node 分層**:勾 node 那一列就是這台全選,
只選一部分時那一列顯示成部分勾選。`status` / `activity` / `state` 不會出現。

**沒有開始/停止按鈕。** Log node 一啟動就開好檔了,按「套用選擇」那一刻就開始寫。
改變主意就重新勾、再套用一次。

勾選框**不會被 1 Hz 的狀態推播蓋掉** —— 只有 node 回報的選擇真的變了(launch 檔、
測試計畫)畫面才跟著動,不然使用者勾第二個之前第一個就沒了。

記錄狀態是 **Log node 推過來的**(`Log/state`),不是這裡猜的 —— 所以測試計畫
用 `control` 換了檔或改了選擇,這個畫面一樣會跟著動。

報告不在 UI 裡。關掉之後拿 CSV 跑 `uv run visualization <data.csv>`,
產出來的 HTML 自己開。細節見 [log.md](log.md)。

**啟動整站** —— 選一個 `launch/*.yaml` 依序啟動,輸出直接顯示在分頁裡。
等的是每個 node 的 `status` 變成 `ok`,**不是等固定秒數**(README 3.1)。
launch 檔裡每個 node 的 `setting` 會在它 ready 之後送過去。

**「存成 launch 檔」** 把目前的 node 與設定值寫成一份新的 yaml。存的是
**設定**不是動作 —— node 在 `describe` 裡標了 `"setting": true` 的才算,
所以 `output_on` 不會被存進去,下次開檔也不會意外送電。
同一個設定設過很多次的話,存的是最後一次的值。

**node 是 UI 的子行程:關掉 UI 就是結束測試。** 關閉時以反向順序進行,Log 最後關,
而且會先送 Log 一個 `stop` —— 所以錄到一半關站,檔案一樣會正常關好。

## 已知限制

- **「測試計畫」分頁按下去會收到 `ERR`**,因為 `Main-Control` node 還沒實作。
  面板本身是好的,node 一做好就會通。
- DUT console 的 `raw` 窗格要等 Serial / SSH DUT node 才有東西。
- 「報告」分頁需要 QtWebEngine。沒有的話會顯示提示,改用「用瀏覽器開」。

---
---

# 第二部分:程式碼規劃

## 模組分工

| 模組 | 負責 | 不該出現在這裡的東西 |
|---|---|---|
| `bridge.py` | eCAL ↔ Qt:探索、訂閱、非同步 service 呼叫 | 任何 widget |
| `panels.py` | 各個面板 | 任何 eCAL 呼叫、任何裝置專屬邏輯 |
| `app.py` | 主視窗、分頁配置 | 面板內部的細節 |
| `launch.py` | 依序啟動、就緒判定、順序關閉 | 對裝置的假設 |

**`panels.py` 完全不 import eCAL。** 面板只跟 `Station` 的 signal 與方法打交道,
所以測試可以直接餵 signal,不用起 eCAL —— 見 `tests/test_ui.py`。

## 執行緒模型(這是 GUI 最容易出事的地方)

```
eCAL 的訂閱執行緒 ──┐
                   ├─> Station 的 Signal ──(Qt 自動排隊)──> 主執行緒的 widget
QThreadPool 的     ─┘
service 呼叫執行緒
```

**規則:任何來自 eCAL 或 worker 的東西,一律先變成 signal。** 直接在 callback 裡碰
widget 會讓 Qt 當掉。`Station` 住在主執行緒,所以 signal 從別的執行緒發出時
Qt 會自動改用 queued connection,widget 因此永遠只在主執行緒被動到。

service 呼叫會阻塞(要等對方回 `OK`/`ERR`),所以丟進 `QThreadPool`,
結果用 `reply` signal 帶回來。**面板不會因為某個 node 沒反應而凍住。**

## 探索與訂閱的分工

| | 做法 | 為什麼 |
|---|---|---|
| 有哪些 node / topic | 每 2 秒問一次 eCAL 原生 monitoring | 清單變動不頻繁,輪詢夠用 |
| 燈號與數值 | 常駐訂閱,node 出現就掛上、消失就拆掉 | 資料是推過來的,不能輪詢 |

`Station._sync_status_subs()` 與 `_sync_data_subs()` 用集合差計算該加該減,
所以訂閱數永遠等於站上實際的 node / topic 數,不會愈積愈多。

DUT console 的訂閱是**例外**:只訂目前看著的那一個(`watch_console()`),
換 node 就換訂閱,不然每個 DUT 的 `raw` 都收會很吵。

## 通用性是怎麼守住的

`panels.py` 裡不准出現任何裝置名稱。三個關鍵點:

1. **按鈕來自 `describe`**,`CommandPanel._row()` 只看 `name` / `label` / `style` / `args`
2. **數值表來自 topic 名稱**,`is_data_topic()` 靠最後一段是不是共通 topic 來分類
3. **命令是字串**,UI 不解析語意,只負責把字串送出去、把回覆顯示出來
4. **哪些命令是設定由 node 自己說**(`describe` 的 `setting` 旗標),UI 不猜

跟測試計畫重疊的部分一律走 `teststation.client`:service 呼叫是 `client.call()`、
頻率統計是 `client.rate_of()`、payload 處理是 `client.prepare_payload()`、
topic 分類是 `core.topics`。**那一層不依賴 Qt**,所以無頭的 runner 也能用。

`Log` 面板是這條規則裡唯一「認得某個 node」的地方 —— 但 `Log` 是 README 2.1 訂死的
固定名稱,跟裝置不同。它也只用共通的管道:service 呼叫 + 一個 `Log/state` topic。

加一台新裝置要做的事:**零**。node 起來、`describe` 回得出來,UI 就認得它。

## 加一個新面板

1. `panels.py` 寫一個 widget,建構子收 `Station`,自己接需要的 signal
2. 需要新資料的話,在 `bridge.Station` 加 signal 與訂閱,**不要在面板裡碰 eCAL**
3. 純邏輯(統計、解析、格式)放 `teststation/client.py`,不要放在面板裡 ——
   測試計畫之後可能也要用
4. `app.py` 的 `MainWindow` 裡加進分頁
5. `tests/test_ui.py` 加測試:直接 `station.some_signal.emit(...)`,檢查畫面

## 踩過的坑

| 坑 | 症狀 | 處理 |
|---|---|---|
| `initialize()` 預設的 `DEFAULT`(55)不含 `MONITORING`(8) | `get_monitoring()` 丟 `RuntimeError` | `client.session()` 用 `init.ALL` |
| eCAL service callback 的回傳型別 | 回 `bytes` 或 `str` 被吞成 `bad cast`,client 端卻收到 `EXECUTED` | 必須回 `(ret_state, bytes)` |
| Windows 的 `QProcess.terminate()` 對 console 程式沒作用 | 每個 node 都等滿逾時才被砍 | 直接 `kill()`;Log 先收 `stop` 正常關檔 |
| 新建的 publisher 還沒配對 | `topic pub` 第一筆掉光 | 等 `get_subscriber_count() > 0` |
| 新建的 subscriber 配對要約 2 秒 | 頻率面板開頭幾秒沒東西 | 正常,不是壞了 |
| 視窗關掉後 worker 才回來 | `Signal source has been deleted` | emit 包 try/except RuntimeError |

前兩個的細節寫在 `core/node.py` 與 `client.py` 的 docstring,因為那不只影響 UI。

## 測試策略

`tests/conftest.py` 在 Qt 載入前把 `QT_QPA_PLATFORM` 設成 `offscreen`,所以
GUI 測試可以在沒有螢幕的機器(CI)上跑。

`Station` 的建構子不碰 eCAL(只建一個 `QTimer`),所以測試可以直接拿它當 signal 來源,
不需要 eCAL、不需要 node。`station.call` 用 monkeypatch 換掉就能斷言送出了什麼字串。

> **離屏渲染的截圖字會變成方框**,因為 offscreen 平台一個字型都沒有(連 `A` 都畫不出來)。
> 那不是字型問題,實際外觀要在桌面上跑 `uv run ui` 才看得到。
