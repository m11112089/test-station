# RSI3000E — INTERFACE

Liscotech RSI3000E,**DUT**(不是測試機台)。一個 process 同時橋接兩個介面:
serial console(命令)與 TCP(影像)。

Service 名稱:`RSI3000/<port>`,`<port>` 是 console 的 COM,例如 `RSI3000/COM6`。

## 命令

送出的都是 console 命令:node 排進佇列,一次寫一條(補 CRLF),寫完要等提示符 `>`
回來(300 ms 保底)才送下一條。回覆的 `OK` 表示**已排進佇列**,裝置真正的回應看 `raw`。
console 沒接上時一律 `ERR`,不先收下來晚點再送;斷線時佇列裡沒送出的命令會丟掉。

| method | 參數 | console 命令 | setting | 回覆範例 |
|---|---|---|---|---|
| `send` | `<text>`(吃掉整行) | 原樣送出 | | `OK queued: help` |
| `sensor_on` | — | `csp 1` | | `OK queued: csp 1` |
| `sensor_off` | — | `csp 0` | | `OK queued: csp 0` |
| `set_frames` | `<count>` ≥ 1 | `scnf <count>` | ✓ | `OK queued: scnf 3` |
| `set_exposure` | `<exposure>` µs | `sce l <hex ticks>` | ✓ | `OK queued: sce l 5F5E100 (1e+06 us)` |
| `set_frame_rate` | `<fps>` | `scft * <hex ticks>` | ✓ | `OK queued: scft * 5F5E100 (1 fps)` |
| `capture` | — | `tfc` | | `OK queued: tfc` |
| `describe` / `set_channel` / `reconnect` | | core 共通 method | | |

tick = 10 ns:曝光 = µs × 100,間隔 = 1e8 / fps。只送 low32,所以兩者上限都是
0xFFFFFFFF tick ≈ 42.9 秒 —— 超過就 `ERR`,不截斷。換算在 node 裡做,
所以 GUI 與測試計畫送同一行命令會得到同一個值。

## Topic

| topic | 內容 |
|---|---|
| `RSI3000/<port>/raw` | console 的輸出,一行一則(提示符沒有換行,安靜下來時單獨送出) |
| `RSI3000/<port>/image_R` / `image_Gr` / `image_Gb` / `image_B` | 一個通道的影像(二進位,見下) |
| `RSI3000/<port>/status` | 跟著 **console** 走:`ok` / `reconnecting` / `error: <msg>` |
| `RSI3000/<port>/activity` | service 呼叫、`TX`(寫進 console 的命令)、`IMG`(收到一張)、錯誤 |

影像 topic 不是 JSON、每則約 6 MB,所以**站台 UI 的數值表不訂、Log 的清單上也不出現**
(`core.topics.is_image`)。影像執行緒獨立於 console:console 斷線時影像照收。

### 影像格式

TCP 一個連線 = 一張:4096×3072 raster RGGB mosaic,12-bit 緊密打包(2 sample / 3 bytes,
lsb),共 18,874,368 bytes;大小不對整張丟掉。依列 / 行奇偶拆成四個 2048×1536 平面,
**值是右對齊的 12-bit 原值(0–4095),沒有任何位移或縮放**。

每則訊息 = 24-byte header + 平面(uint16 little-endian),header 與原本 C++ 版的
`img::FrameHeader` 相同:

| 欄位(uint32 LE) | 值 |
|---|---|
| magic | `0x474D4945`(記憶體裡讀起來是 `EIMG`) |
| width / height | 2048 / 1536 |
| bits_valid(低 16 位) | 12 |
| flags(高 16 位) | `0x0001` = 不是 `tfc` 拍的(見下) |
| channel | 0 = R、1 = Gr、2 = Gb、3 = B |
| frame_index | node 啟動後從 0 起算 |

C++ 版把 offset 12 讀成一個 uint32 的 `bits_valid`;這裡拆成兩個 uint16,版面與大小沒變,
C++ 版的訂閱端也不讀這個欄位,所以仍相容。

### 不是 `tfc` 拍的影像

裝置除了拍攝,還會自己送影像:**每次 `csp 1`(開 sensor)都會送一張開機 DMA 自我測試的影像**,
幾乎全黑(四通道約 340 DN,約等於黑電平)。實機上重開 sensor 三次,三次都有。

node 依送進 console 的命令判斷每張是不是拍的:每寫出一次 `tfc`,接下來 `scnf` 張算拍的;
`csp` 歸零;還沒送過 `scnf` 時,`tfc` 之後到下一次 `csp` 前都算。不是拍的照樣發佈,
但 header 帶 `flags = 0x0001`,`activity` 的 `IMG` 也會註明。GUI 照樣顯示,但標題標「非拍攝」、
log 記一行,**AE 不拿它來算** —— 不然一張黑的自我測試會讓 AE 把曝光加倍,下一張就爆亮。

## 啟動

```
uv run python -m teststation.nodes.rsi3000.node --port COM6
uv run python -m teststation.nodes.rsi3000.gui                  # 專用畫面,站上只有一台時自動找到
uv run python -m teststation.nodes.rsi3000.gui --node RSI3000/COM6
```

| 選項 | 預設 | 說明 |
|---|---|---|
| `--port` | **必填** | console 的 COM。console 沒有無害的辨識方式,所以不掃描 |
| `--baud` | 115200 | |
| `--tcp-host` | `0.0.0.0` | 影像監聽位址 |
| `--tcp-port` | 5001 | 影像監聽 port |

**重複啟動時新的接手**:影像 port 被佔用,而且佔著的是另一個 RSI3000 node(命令列帶著
`teststation.nodes.rsi3000.node`),就把它整棵行程收掉,再接手 port 與 COM。
佔著的如果是別的程式(例如 C++ 版的 `rsi3000_bridge`),一律不碰,照樣報錯並說出是誰。

## GUI

RSI3000E 是 DUT,畫面是它自己的(`gui.py`),**不跟站台 UI 共用**。
命令一樣走 service、資料一樣走 topic,所以站台 UI 的「命令」分頁也能操作同一台。

- 2×2 即時顯示四通道(縮小 4 倍,`>> 4`);Auto-Contrast 改用各通道當下全幅 min/max 拉伸,
  **只動顯示,統計永遠讀原始 DN**
- 「RGB 疊圖」:把同一張的四通道疊成一張彩色影像(R、G = (Gr + Gb) / 2、B,縮小 2 倍)。
  每個 Bayer quad 剛好對應一個像素,不內插;**不做白平衡**,顏色是 sensor 原始的樣子。
  開 Auto-Contrast 時三色各自拉伸,效果近似粗略的自動白平衡。ROI 在這個畫面上一樣可以拖曳
- 「影像處理」區塊(在「顯示」下面):可以開關的處理,每列 3 個按鈕。
  演算法在 `isp.py`,畫面處理的順序固定為 RGB → AWB →(之後:CCM …)→ 轉 8-bit 顯示;
  只作用在 RGB 疊圖的按鈕,RGB 疊圖關著時反灰
  - **AE**:**會改裝置的曝光**,不只是畫面,所以四通道畫面下也能開。每湊齊一張就依它算下一次的曝光
    (現在 × 目標 1500 DN / **沒飽和的像素**的平均,一次最多 2 倍;±5% 內不動;
    大半張都飽和、超過 20% 時優先減半 —— 燈、反光這類小亮區本來就會飽和,不讓它把整張拖暗。
    開 AE 時會先把欄位上的曝光送一次,讓起點跟裝置一致),
    再送 `set_exposure` —— 跟手動設定是同一條命令。開著時曝光欄位只顯示 AE 送出的值、不能設定,
    「套用設定」只送張數與間隔。沒有扣黑電平,收斂點會比目標略暗。
    **曝光只在 `tfc` 時套用**(實機:`scnf 10` 一輪中途送 `sce`,曝光減了 128 倍亮度卻沒變),
    所以 AE 以「這一輪實際用的曝光」為基準、一輪只送一次,新值下一次 `tfc` 才生效
  - **AWB**:gray world,把 R、B 的平均拉到跟 G 一樣;三色任一飽和(4095)的像素不算。
    增益顯示在按鈕下方。AWB 開時 Auto-Contrast 改成三色共用一個範圍,不然會把白平衡洗掉
  - 新增一項(例如 CCM):`isp.Options` 加欄位、`isp.compose_rgb` 實作,
    再在 `gui.py` 的 `PROCESSING` 加一行,按鈕就會自己排進去
- 全幅與 ROI 的 min / max / mean / std(DN);ROI 可在影像上拖曳或輸入
- sensor 開關、張數 / 曝光(us / ms / s)/ 間隔、拍攝、自由命令列;console 輸出與命令回覆在下方
- **不存檔**:原本 C++ 版的檔名前綴、輸出目錄、TIFF 存檔、EMVA dn 標籤都拿掉了
