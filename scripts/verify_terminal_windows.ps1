# Windows E2E 驗證: terminal_node (eCAL 上的平台 shell, 偽終端)
#   1. status -> tx 打字 -> rx 收 shell 回顯+輸出
#   2. 第三方工具走 shell: tx 送 "ping ..." 指令行, rx 收輸出
# 由 repo 根目錄執行: powershell -File scripts\verify_terminal_windows.ps1
#
# 不需設定 PATH: station_deploy() 已把所有相依 DLL 佈到 exe 旁。

$tmp = Join-Path $env:TEMP "terminal_e2e"
New-Item -ItemType Directory -Force $tmp | Out-Null

Write-Output "=== 1a. status (預設 terminal/shell, Windows shell = powershell) ==="
$node = Start-Process ".\TERMINAL\build\terminal_node.exe" -NoNewWindow -PassThru
Write-Output "node pid=$($node.Id)"
.\tools\build\ecal_topic.exe echo terminal/shell/status --count 1 --timeout 15000
Write-Output "rc=$LASTEXITCODE"

Write-Output "=== 1b. tx 'echo hello_e2e_123' -> rx (回顯+輸出) ==="
$rx = Start-Process ".\tools\build\ecal_topic.exe" `
        -ArgumentList "echo","terminal/shell/rx","--count","2","--timeout","15000" `
        -NoNewWindow -PassThru -RedirectStandardOutput "$tmp\rx.txt"
Start-Sleep -Seconds 3   # 等 rx 訂閱端配對
.\tools\build\ecal_topic.exe pub terminal/shell/tx "echo hello_e2e_123" --newline cr   # pty: Enter = CR
$rx.WaitForExit()
Get-Content "$tmp\rx.txt"

Write-Output "=== 2. 第三方工具走 shell: tx 'ping -n 1 127.0.0.1' -> rx ==="
$rx2 = Start-Process ".\tools\build\ecal_topic.exe" `
        -ArgumentList "echo","terminal/shell/rx","--count","5","--timeout","15000" `
        -NoNewWindow -PassThru -RedirectStandardOutput "$tmp\rx2.txt"
Start-Sleep -Seconds 3
.\tools\build\ecal_topic.exe pub terminal/shell/tx "ping -n 1 127.0.0.1" --newline cr
$rx2.WaitForExit()
Get-Content "$tmp\rx2.txt"

Stop-Process -Id $node.Id -Force -ErrorAction SilentlyContinue
