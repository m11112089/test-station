# PySide6 UI

站台的圖形介面。

**通用 UI,不為個別裝置撰寫畫面。** 程式碼裡沒有一行認識 BTM-4208SD 或 PFR-100L:
按鈕是照各 node 回報的 `describe` 長出來的,數值表是照資料 topic 長出來的。
**新增一種裝置不用改 UI。**

跟 [`st`](st.md) 一樣,UI 送出的命令字串與 `st`、測試計畫送的完全相同,走同一個
eCAL service(README 1.2:GUI 沒有特權)。

程式碼在 [src/teststation/ui/](../src/teststation/ui/)。

> **第一部分:怎麼用**(到「已知限制」為止)
> **[第二部分:程式碼規劃](#第二部分程式碼規劃-1)**

---

## 啟動

```
uv run st ui
```

UI 自己開 eCAL session,不需要先啟動別的東西。站台還沒起來時,用「啟動整站」分頁。

## 畫面配置

```
┌─────────────┬──────────────────────────────────────────────────┐
│ Node        │ 命令 │ DUT console │ 測試計畫 │ Log │ 啟動整站     │
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

## 六塊各自做什麼

**Node 清單(左)** —— 從 eCAL 註冊資訊自動列出。字的顏色就是燈號,滑鼠移上去看完整狀態:

| 顏色 | 狀態 |
|---|---|
| 綠 | `ok` |
| 橘 | `reconnecting` |
| 紅 | `error: ...` |
| 灰 | 註冊得到但收不到心跳 —— node 卡住了 |

選哪個 node,「命令」與「DUT console」分頁就跟著換。重新整理時**選擇會保留**。

**命令** —— 按鈕與輸入框是按下 node 之後跟它要 `describe` 長出來的:

- 沒有參數 → 一個按鈕
- 有 `min`/`max` 的參數 → 帶範圍限制的數字框,**打不出超出範圍的值**
- 其他參數 → 文字框
- `"style": "danger"` → 紅色按鈕

下面的自由輸入列送什麼就是什麼,跟 `st service call` 一模一樣。歷史區的回覆綠色是 `OK`、紅色是 `ERR`。

**即時數值(下)** —— 所有資料 topic 的最新值。一個 topic 一列,不會愈長愈多。
`status`、`activity`、`raw` 這三個共通 topic 不會進來。
`NAME` 欄會跟著 `set_channel` 立刻改變。

**DUT console** —— 與 `st console` 相同的雙窗格,上半 `raw`、下半 `activity`。

**測試計畫** —— 列出 `plans/*.py`,Run / Abort 送給 `Main-Control`。

**Log** —— Start / Mark / Stop 送給 `Log`,「開啟最近報告」找 `logs/*/report.html` 裡最新的一份用瀏覽器打開。

**啟動整站** —— 選一個 `launch/*.yaml` 一鍵啟動,輸出直接顯示在分頁裡。
它是去跑 `st launch`,不是另外寫一份啟動邏輯 —— 依序啟動、等 `status` 變 `ok`、
反向關閉這些語意只能有一個實作。關掉 UI 會一併收掉 launcher。

## 已知限制

- **「測試計畫」與「Log」分頁按下去會收到 `ERR`**,因為 `Main-Control` 與 `Log` node
  還沒實作。面板本身是好的,node 一做好就會通。
- DUT console 的 `raw` 窗格要等 Serial / SSH DUT node 才有東西。

---
---

# 第二部分:程式碼規劃

## 模組分工

| 模組 | 負責 | 不該出現在這裡的東西 |
|---|---|---|
| `bridge.py` | eCAL ↔ Qt:探索、訂閱、非同步 service 呼叫 | 任何 widget |
| `panels.py` | 六個面板 | 任何 eCAL 呼叫、任何裝置專屬邏輯 |
| `app.py` | 主視窗、分頁配置、launch 一鍵啟動 | 面板內部的細節 |

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

加一台新裝置要做的事:**零**。node 起來、`describe` 回得出來,UI 就認得它。

## 加一個新面板

1. `panels.py` 寫一個 widget,建構子收 `Station`,自己接需要的 signal
2. 需要新資料的話,在 `bridge.Station` 加 signal 與訂閱,**不要在面板裡碰 eCAL**
3. `app.py` 的 `MainWindow` 裡加進分頁
4. `tests/test_ui.py` 加測試:直接 `station.some_signal.emit(...)`,檢查畫面

## 測試策略

`tests/conftest.py` 在 Qt 載入前把 `QT_QPA_PLATFORM` 設成 `offscreen`,所以
GUI 測試可以在沒有螢幕的機器(CI)上跑。

`Station` 的建構子不碰 eCAL(只建一個 `QTimer`),所以測試可以直接拿它當 signal 來源,
不需要 eCAL、不需要 node。`station.call` 用 monkeypatch 換掉就能斷言送出了什麼字串。

> **離屏渲染的截圖字會變成方框**,因為 offscreen 平台一個字型都沒有(連 `A` 都畫不出來)。
> 那不是字型問題,實際外觀要在桌面上跑 `st ui` 才看得到。
