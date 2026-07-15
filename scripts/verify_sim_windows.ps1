# Windows E2E 驗證: sim node -> status -> setpoint/output 命令 -> meas
# 由 repo 根目錄執行: powershell -File scripts\verify_sim_windows.ps1
#
# 不需設定 PATH: station_deploy() 已把所有相依 DLL 佈到 exe 旁。

$node = Start-Process ".\GPP3650\build\gpp3650_node.exe" -ArgumentList "--sim" -NoNewWindow -PassThru
Write-Output "node pid=$($node.Id)"

Write-Output "=== 1. status ==="
.\tools\build\ecal_topic.exe echo gpp3650/SIM/status --count 1 --timeout 15000
Write-Output "rc=$LASTEXITCODE"

Write-Output "=== 2. set CH1 5V/1A + ALL ON ==="
.\tools\build\ecal_topic.exe pub gpp3650/SIM/cmd/setpoint '{\"ch\":1,\"voltage\":5.0,\"current\":1.0}'
.\tools\build\ecal_topic.exe pub gpp3650/SIM/cmd/output '{\"ch\":0,\"on\":true}'

Write-Output "=== 3. meas x2 ==="
.\tools\build\ecal_topic.exe echo gpp3650/SIM/meas --count 2 --timeout 15000
Write-Output "rc=$LASTEXITCODE"

Stop-Process -Id $node.Id -Force -ErrorAction SilentlyContinue
