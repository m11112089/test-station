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
cmake -S "$SRC/tools" -B "$BUILD/tools" > /dev/null
cmake --build "$BUILD/tools" -j"$(nproc)" | tail -2
echo "=== BUILD OK ==="

[ "$1" = "--run" ] || exit 0

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
exit 0
