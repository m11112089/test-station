# TODO

## BTM-4208SD — 待補的實機驗證

邏輯都已實作並有單元測試涵蓋,但**沒在實機上跑過**。多半需要人為破壞或換設定才測得到。

- [ ] 測試中途拔掉 USB 轉接線 → 確認狀態轉 `reconnecting`,插回去會自己恢復
- [ ] 拔掉 3.5 mm 訊號線(port 還在但沒資料)→ 確認 `--data-timeout` 會觸發
- [ ] 面板切到 `°F` → 確認 payload 的 `unit` 跟著變,且 node 不做換算
- [ ] 負溫度與其他小數位數(D10 / D9 欄位)→ 目前只有單元測試,沒有實機讀值
- [ ] 兩台 BTM-4208SD 同時接在同一台電腦 → 確認依序啟動時會分配到不同 COM

驗過的項目直接把這行刪掉,不用改成「已驗證」——狀態記在這裡,不要散進文件。

## PFR-100L — 待補的實機驗證

2026-10-28 已接實機(COM7,USB-CDC)與 PEL-3031E 對測過,下面是還沒驗到的。

- [ ] USB-CDC 自動掃描與 activity 的 `via USB-CDC`。2026-10-28 已在 COM7(FW 01.35.20240312)上以
      `--port` 驗過連線、命令、100 W 鎖、量測,USB VID 確認是 `0x2184`
- [ ] RS232(Remote-IN + PSU-232,`F-29 = 1`):出廠 115200 與改過 `F-71` 搭配 `--baud` 各一次
- [ ] 送電中拔 USB → `reconnecting`,插回去自己恢復(Windows 要配回同一個 COM 號)

## PEL-3031E — 待補的實機驗證

2026-10-28 在 COM3(FW V1.03.002)上驗過:USB-CDC 連線、自動掃描、`*IDN?` 辨識、狀態查詢、
量測發佈、設定命令、L range 下 `set_current 10` 被拒收;接 PFR-100L(COM7)對測:
CC 0.5 A 兩台電流一致、CV 4 V 時 PFR 進限流 1 A、拉載中換模式會關 load(node 回覆會講)。

- [ ] **CP 崩潰**:Low range、CP 2 W、PFR 5 V 供電,10 次裡 8 次電壓掉到 ~0 V、電流卡在電源限流
      (限流 1 A、2 A 都一樣)。查 `:POWer:RESPonse`、H range、較高功率是否不同。查清楚前文件寫「先別用 CP」
- [ ] 對測第二輪出現過一次:CC 0.5 A `load_on` 後 PEL 量到 0 V / 0 A、PFR 也只有 0.002 A。
      之後兩輪與直接追蹤 node 收發都沒重現,原因不明。若再出現,同時記下面板顯示
- [ ] 拉載中拔 USB → `reconnecting`,插回去自己恢復;負載本身是否繼續拉載
- [ ] CR 模式要不要開(單位 mS / OHM 的處理)

## UI / launcher — 待補

- [ ] UI 的 DUT console 分頁:`raw` 窗格與 `send` 送出路徑沒辦法驗,要等 Serial / SSH
      DUT node。目前只驗過 `activity` 窗格與錯誤顯示(對著 BTM node,它沒有 `send`)。
- [ ] UI 被 `taskkill /F` 或工作管理員硬砍時會留下孤兒 node 佔住 COM。
      正常關閉已處理(反向順序,Log 先收到 `stop`)。若要根治,Windows 上得用 Job Object。
- [ ] `launch/burn_in_station.yaml` 目前只開了 `log` 與 `btm_4208sd` 兩行,
      其餘註解著等對應的 node 做好。

## UI 面板 — 待補

- [ ] 「測試計畫」與「Log」分頁按下去只會收到 `ERR`,要等 `Main-Control` 與 `Log` node。
      面板與命令字串已驗過,缺的是對面。
- [ ] `Main-Control` 的進度 topic 名稱還沒定案,UI 目前只顯示 service 的回覆。
- [ ] 實際字體與版面只在離屏渲染下看過(offscreen 平台沒有字型,字會變方框),
      要在桌面上跑 `uv run ui` 確認一次。

## Log — 待補

- [ ] `console.log` 要等 Serial / SSH DUT node 才會有內容。
- [ ] 長時間 run 的 `uv run visualization` 花多久沒量過。
- [ ] 報告的 `bands`(區段底色)與 `limits`(水平參考線)目前恆空。
      模板支援,但還沒有東西去填 —— 要標示 power-on 期間之類的再說。
- [ ] 長時間測試的報告大小沒試過:12 小時 × 12 channel 約 30 萬點。
      撐不住再談抽稀,別先做。

## 還沒實作的 node

- [ ] **Serial / SSH DUT** + `src/teststation/duts/` 的 DUT profile
- [ ] **Main-Control** — 測試計畫 runner 與 `teststation.control` client API
      (README 6 的 abort 只寫了「關閉所有 PFR 輸出」,實作時 PEL 也要 `load_off`)
