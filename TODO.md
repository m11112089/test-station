# TODO

## BTM-4208SD — 待補的實機驗證

邏輯都已實作並有單元測試涵蓋,但**沒在實機上跑過**。多半需要人為破壞或換設定才測得到。

- [ ] 測試中途拔掉 USB 轉接線 → 確認狀態轉 `reconnecting`,插回去會自己恢復
- [ ] 拔掉 3.5 mm 訊號線(port 還在但沒資料)→ 確認 `--data-timeout` 會觸發
- [ ] 面板切到 `°F` → 確認 payload 的 `unit` 跟著變,且 node 不做換算
- [ ] 負溫度與其他小數位數(D10 / D9 欄位)→ 目前只有單元測試,沒有實機讀值
- [ ] 兩台 BTM-4208SD 同時接在同一台電腦 → 確認依序啟動時會分配到不同 COM

驗過的項目直接把這行刪掉,不用改成「已驗證」——狀態記在這裡,不要散進文件。

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
- [ ] 報告的 `bands`(區段底色)目前恆空;`mark` 只畫成垂直線。
      要把成對的 mark 變成區段(例如 power-on 期間)還沒做。
- [ ] 長時間測試的報告大小沒試過:12 小時 × 12 channel 約 30 萬點。
      撐不住再談抽稀,別先做。

## 還沒實作的 node

- [ ] **PFR-100L** — 手冊已在 `docs/devices/PFR-100L/`
- [ ] **Serial / SSH DUT** + `src/teststation/duts/` 的 DUT profile
- [ ] **Main-Control** — 測試計畫 runner 與 `teststation.control` client API
