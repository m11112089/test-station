#!/usr/bin/env bash
# WSL/Ubuntu 平台編譯驗證 (build 目錄放 WSL 家目錄, 不污染 repo)
#
#   wsl bash /mnt/c/Users/kevin/Desktop/test-station/scripts/wsl_build.sh          # 只驗證編譯
#   wsl bash /mnt/c/Users/kevin/Desktop/test-station/scripts/wsl_build.sh --run    # 編譯 + sim E2E
set -e
SRC="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$HOME/teststation-build"

cmake -S "$SRC/GPP3650" -B "$BUILD/gpp3650" > /dev/null
cmake --build "$BUILD/gpp3650" -j"$(nproc)" | tail -3
cmake -S "$SRC/TERMINAL" -B "$BUILD/terminal" > /dev/null
cmake --build "$BUILD/terminal" -j"$(nproc)" | tail -2
cmake -S "$SRC/tools" -B "$BUILD/tools" > /dev/null
cmake --build "$BUILD/tools" -j"$(nproc)" | tail -2
echo "=== BUILD OK ==="

[ "$1" = "--run" ] || exit 0
set +e   # E2E 段自行以 rc= 回報, 不因單步失敗中斷

# ---- 選配: sim E2E (node -> status -> setpoint/output -> meas) ----
"$BUILD/gpp3650/gpp3650_node" --sim > /tmp/node.log 2>&1 &
NODE_PID=$!
echo "node pid=$NODE_PID"

echo "=== 1. status ==="
"$BUILD/tools/ecal_topic" echo gpp3650/SIM/status --count 1 --timeout 15000
echo "rc=$?"

echo "=== 2. set CH1 5V/1A + ALL ON ==="
"$BUILD/tools/ecal_topic" pub gpp3650/SIM/cmd/setpoint '{"ch":1,"voltage":5.0,"current":1.0}'
"$BUILD/tools/ecal_topic" pub gpp3650/SIM/cmd/output '{"ch":0,"on":true}'

echo "=== 3. meas x2 ==="
"$BUILD/tools/ecal_topic" echo gpp3650/SIM/meas --count 2 --timeout 15000
echo "rc=$?"

kill $NODE_PID 2>/dev/null || true
tail -3 /tmp/node.log

# ---- TERMINAL E2E: 對 shell (bash on pty) 打字, pty 回顯即 loopback ----
"$BUILD/terminal/terminal_node" > /tmp/terminal_node.log 2>&1 &
TERM_PID=$!
sleep 2   # 等 node 的 pub/sub 完成 eCAL 註冊配對
echo "=== 4. terminal shell (pub tx -> bash 回顯/輸出 -> echo rx) ==="
# --delay 5000: 給同時啟動的 rx 訂閱者足夠的 eCAL 配對時間, 否則回顯回來沒人收
"$BUILD/tools/ecal_topic" pub terminal/shell/tx 'echo hello-terminal-e2e' --newline cr --delay 5000 &
PUB_PID=$!
"$BUILD/tools/ecal_topic" echo terminal/shell/rx --count 1 --timeout 15000
echo "rc=$?"
wait $PUB_PID 2>/dev/null
kill $TERM_PID 2>/dev/null || true
exit 0
