# TERMINAL — 本機 terminal（平台 shell）橋接

- `terminal_node` — 「一個掛在 eCAL 上的 shell」（headless，獨立 process）
- `terminal_term` — 互動終端（minicom 風格，只透過 eCAL 溝通）

用途：測試流程需要呼叫第三方工具（iperf、ping、廠商 CLI）時，
對 tx 送指令行就跟真人打字一樣 —— 測試腳本對「DUT console（UART）」
與「本機 shell」是同一套 raw byte topic 介面。

shell 跑在**偽終端**（Windows ConPTY / POSIX forkpty）上，是一條持續的
互動 session：回顯、prompt、行編輯、歷史、Ctrl+C、顏色都與本機 terminal
相同。相依性零新增 —— ConPTY 在 kernel32（Win10 1809+）、forkpty 在
glibc，皆為 OS 內建，無任何第三方庫。

```
TERMINAL/
├── CMakeLists.txt
├── node/
│   ├── main.cpp
│   ├── TerminalNode.hpp/.cpp   # pty <-> eCAL 橋接、shell 結束 3 秒重啟
│   └── PtyProcess.hpp/.cpp     # 跨平台偽終端包裝 (ConPTY / forkpty)
└── term/
    └── main.cpp                # 跨平台 raw console (conio / termios), 擴充鍵轉 VT
```

## Topic 規約

node 沒有執行參數 —— 它永遠 spawn 平台 shell（Windows=`powershell`、
Linux=`bash`；PowerShell 的 `ls`/`pwd`/`cat` alias 讓兩平台指令習慣一致）。
唯一的參數是 topic 後綴 `--id`（預設 `shell`），只在要跑多個實例時才需要：

| 啟動 | Topic 前綴 |
|---|---|
| `terminal_node` | `terminal/shell` |
| `terminal_node --id B` | `terminal/B` |

| Topic | 方向 | Payload |
|---|---|---|
| `terminal/<id>/tx` | eCAL → 終端輸入 | 原始位元組 = 對它打字（Enter = CR `\r`） |
| `terminal/<id>/rx` | 終端輸出 → eCAL | 原始位元組，含回顯與 VT 序列（UTF-8） |
| `terminal/<id>/status` | node → 訂閱者 | JSON，2 秒心跳 + 事件即時發佈 |

`status` 範例：
`{"device":"terminal/shell","connected":true,"command":"powershell","pid":1234,
"exit_code":null,"tx_bytes":21,"rx_bytes":412,"error":"","timestamp_ms":...}`
（`exit_code` 為上一次 shell 結束的代碼，尚未結束過為 `null`）

## 執行

```bash
# 終端機 1: driver node（不帶參數即可）
./terminal_node

# 終端機 2: 互動終端（不帶參數直接配對, 像本機 terminal 一樣用）
./terminal_term
# 離開: Ctrl+Q 或 Ctrl+]  (Esc / Ctrl+C / 方向鍵都會送給遠端, 跟真 terminal 一樣;
#       中文輸入法模式下 ] 會被組成全形 】, 用 Ctrl+Q)
```

測試腳本呼叫第三方工具 = 對 tx 送指令行（`ecal_topic pub --newline cr` 補 Enter；
shell argv 內嵌換行常被弄掉，所以由工具補）：

```powershell
.\ecal_topic pub terminal/shell/tx "iperf3 -c 192.168.1.10" --newline cr
.\ecal_topic echo terminal/shell/rx --count 10 --timeout 30000
# 要 exit code: 再送 "echo $LASTEXITCODE" (PowerShell) / "echo $?" (bash), 從 rx 解析
```

## Node 行為細節

- shell 結束（打 `exit` / 崩潰）→ 發佈 `status`（含 exit_code）→ 3 秒後
  重啟一個新 shell（同串口斷線重連語意）。
- tx 在 shell 未執行時到達 → 丟棄並發佈帶 error 的 `status`。
- stderr 與 stdout 天然合流（同一個 terminal），rx 就是真人看到的畫面串流。
- 輸出走 terminal → 行緩衝即時輸出（pipe 下 iperf 要 --forceflush 的問題
  不存在）；Windows 側 ConPTY 輸出統一為 UTF-8（無 CP950 問題）。
- 偽終端固定 120x30（遠端沒有視窗尺寸可跟隨）。

## 注意事項

- rx 內含 VT escape 序列（顏色/游標控制）：`terminal_term` 直接交給本機
  console 渲染；測試腳本要 parse 純文字時需先濾掉（或忍受它）。
- ConPTY 用**絕對座標**重繪畫面，所以 `terminal_term` 會進入 alternate
  screen（乾淨畫布，座標系與遠端 pty 一致，telnet/ssh 同法），離開時
  還原原畫面。剛接上時遠端不會重繪歷史，畫面是空的 —— 按一下 Enter
  拿新 prompt；視窗建議 ≥ 120x30（遠端 pty 尺寸）。
- 跨機器配對時 `--id` 取相同值即可（前綴不含平台資訊）。
- ConPTY 需 Windows 10 1809+；不支援時 status 會帶 error 回報。
- 實作備註（PtyProcess.cpp）：MinGW 標頭未宣告 ConPTY API → 執行期
  GetProcAddress 綁定；父行程 stdout 被重導時須以 STARTF_USESTDHANDLES+null
  阻止 legacy 握把複製；子程序結束後須主動 ClosePseudoConsole 解除 reader。
