# test-station — 產線測試自動化平台

跨平台（Windows / Ubuntu）測試裝置整合平台。每個 device driver 為**獨立 OS
process**，彼此與上層（GUI / 測試流程）只透過 **eCAL**（pub/sub + service）溝通，
不直接 import driver。

- 語言：C++17（單一語言、單一建置系統）
- GUI / Serial / TCP：Qt（QWidgets + QSerialPort + QTcpSocket）
- 通訊：eCAL 6，**兩平台統一使用 `ecal_c` C API**（原始碼零分支，
  只有 CMake 連結方式依平台分支）
- Payload：UTF-8 JSON（可用 eCAL Monitor 直接目視除錯）

## 專案結構

**每個裝置資料夾是一個獨立的 CMake 專案**（有自己的 `CMakeLists.txt` 與
`build/`），共用的建置邏輯集中在 `cmake/StationSetup.cmake`，
共用的程式碼在 `common/`：

```
test-station/
├── cmake/
│   └── StationSetup.cmake   # 共用: Qt/eCAL 偵測 + station_common 庫 (各專案 include)
├── common/                  # 跨裝置共用原始碼: ecal_c RAII 包裝、topic 命名規約
│   ├── EcalWrap.hpp/.cpp
│   └── Topics.hpp
├── tools/                   # [獨立專案] ecal_topic — topic echo/pub 除錯工具 (類 rostopic)
├── scripts/                 # 兩平台建置/E2E 驗證腳本
├── GPP3650/                 # [獨立專案] GPP-3650 電源供應器 (見 GPP3650/README.md)
│   ├── CMakeLists.txt
│   ├── node/  gui/
│   └── build/               # 各專案自己的 build 目錄 (gitignored)
└── TERMINAL/                # [獨立專案] 本機 shell (偽終端) 橋接 + 互動終端 (見 TERMINAL/README.md)
    ├── CMakeLists.txt
    └── node/  term/
```

新增裝置（eload / chamber / thermocouple / DUT / camera...）：建立
`<DEVICE>/CMakeLists.txt`，開頭
`include(${CMAKE_CURRENT_SOURCE_DIR}/../cmake/StationSetup.cmake)`
後即可使用 `station_common`、`QT_CORE_LIBS`、`QT_GUI_LIBS`。
裝置專屬的 topic 規約與說明放在 `<DEVICE>/README.md`。

## 通用設計約定

- Topic 前綴 = `<device_type>/<port>`，由 `--port` 自動推導（`COM4` →
  `gpp3650/COM4`；`/dev/ttyUSB0` → `gpp3650/ttyUSB0`；`--sim` →
  `gpp3650/SIM`），不另取邏輯名稱。格式 `<prefix>/cmd/*`（上層→node）、
  `<prefix>/meas`、`<prefix>/status`（node→上層）。
- 執行緒模型：eCAL 訂閱 callback 在 eCAL 內部執行緒觸發 → 只 `emit` Qt
  signal（跨執行緒自動 QueuedConnection）→ 回到主執行緒才操作
  QSerialPort/QTcpSocket/UI。硬體 I/O 天然序列化，命令不會交錯。
- 每個 node 提供 `--sim` 模擬模式，無硬體即可驗證整條 pub/sub 流程。

## 建置

### Windows（MinGW）

前置需求：

1. eCAL 6 官方安裝（預設 `C:\eCAL`，其他路徑用 `-DECAL_ROOT=` 指定）
2. **MinGW 版 Qt + Qt 配套的 MinGW toolchain**（用 aqtinstall 免帳號安裝）：

```powershell
pip install --user aqtinstall
python -m aqt install-qt   windows desktop 6.8.3 win64_mingw -m qtserialport -O C:\Qt
python -m aqt install-tool windows desktop tools_mingw1310 qt.tools.win64_mingw1310 -O C:\Qt
```

建置（**必須用 Qt 配套的 g++/mingw32-make**，理由見下方陷阱；
每個裝置專案獨立建置，以 GPP3650 為例，`tools` 同理）：

```powershell
cmake -S GPP3650 -B GPP3650\build -G "MinGW Makefiles" `
      -DCMAKE_PREFIX_PATH="C:/Qt/6.8.3/mingw_64" `
      -DECAL_ROOT="C:/eCAL" `
      -DCMAKE_CXX_COMPILER="C:/Qt/Tools/mingw1310_64/bin/g++.exe" `
      -DCMAKE_MAKE_PROGRAM="C:/Qt/Tools/mingw1310_64/bin/mingw32-make.exe"
cmake --build GPP3650\build -j
```

**執行不需要設定 PATH**：建置後 `station_deploy()` 會自動把所有相依 DLL
（Qt + platform plugin + MinGW runtime + eCAL）佈到 exe 旁——app 目錄的
DLL 搜尋優先權最高，因此在任何 shell 直接執行、或把整個 build 資料夾
複製到產線機器都可以直接跑。E2E 驗證：`powershell -File scripts\verify_sim_windows.ps1`。

> **陷阱一（CRT 混用 = heap corruption）**：Qt 官方 `win64_mingw` 組建是
> **MSVCRT** 基底；若用 UCRT 基底的 MinGW（如 WinLibs `C:\mingw64`
> GCC 15）編譯本專案，程式會以 `0xC0000374`（heap corruption）崩潰
> （實測 `QSerialPortInfo::availablePorts()` 即重現）。編譯器與執行時
> PATH 都必須用 Qt 配套的 `C:\Qt\Tools\mingw1310_64`。
> eCAL DLL 本身是 MSVC/UCRT 建置沒有關係——C API 邊界不跨堆轉移
> 記憶體所有權（實測 init/pub/sub/finalize 正常）。

> **陷阱二（MinGW × eCAL 連結）**：官方 `C:\eCAL\lib\ecal_core_c.lib` 是
> MSVC 格式，GNU `ld` 無法連結。CMake configure 時會自動對
> `ecal_core_c.dll` 執行 `gendef` + `dlltool` 產生
> `<build>/mingw_implib/libecal_core_c.a`，不需手動處理。
> 因為只用 **C API**（C ABI），跨編譯器呼叫 MSVC 建置的 DLL 是安全的——
> 這也是兩平台統一走 `ecal_c` 而非 C++ API 的原因（MSVC 的 C++ ABI 與
> MinGW 不相容）。

> **陷阱三（eCAL 內附 Qt）**：`C:\eCAL\bin` 內附的 Qt6 DLL 是 MSVC 版
> （給 eCAL Monitor 用的），且 `C:\eCAL\bin` 在**系統 PATH** 內——
> 沒有部署 DLL 的 exe 直接執行會載到它而以 `0xC0000139`
> （Entry Point Not Found）無聲退出。`station_deploy()` 把正確 DLL 佈到
> exe 旁即可根治（app 目錄優先權高於 PATH）。

### Ubuntu

```bash
# eCAL 官方 PPA
sudo add-apt-repository ppa:ecal/ecal-latest
sudo apt install ecal
sudo apt install libprotobuf-dev protobuf-compiler   # eCAL CMake config 隱含相依，PPA 套件僅含執行期 libprotobuf23
# Qt
sudo apt install qt6-base-dev libqt6serialport6-dev   # 或 Qt5: qtbase5-dev libqt5serialport5-dev
# 串口權限
sudo usermod -aG dialout $USER   # 重新登入生效

cmake -S GPP3650 -B GPP3650/build && cmake --build GPP3650/build -j
cmake -S tools   -B tools/build   && cmake --build tools/build -j

cmake -S . -B build
```

CMake 會先嘗試 `find_package(eCAL)`（target `eCAL::core_c`），
找不到再退回 `find_library(ecal_core_c)`。

> **WSL 驗證**：Ubuntu 平台的編譯驗證在 WSL2 內進行，build 目錄放在
> WSL 家目錄（`~/teststation-build`），**不會**在 repo 內留下第二個
> build 資料夾：
>
> ```powershell
> wsl bash /mnt/c/Users/kevin/Desktop/test-station/scripts/wsl_build.sh          # 只驗證編譯
> wsl bash /mnt/c/Users/kevin/Desktop/test-station/scripts/wsl_build.sh --run    # 編譯 + sim E2E
> ```
>
> 注意 WSL2 + usbipd 的 USB attach 是獨佔的，接實體串口裝置驗證時
> Windows 端程式無法同時使用該裝置。

## E2E 驗證（--sim，無需硬體）

兩平台皆已實測通過：sim node 啟動 → `status` 顯示 connected →
`cmd/setpoint`（CH1 5V/1A）+ `cmd/output`（ALL ON）→ `meas` 回報
CH1 ≈ 5.0 V / 0.5 A / 2.5 W（10 Ω 模擬負載）。

```powershell
# Windows
powershell -File scripts\verify_sim_windows.ps1
# Ubuntu (WSL)
wsl bash /mnt/c/Users/kevin/Desktop/test-station/scripts/wsl_build.sh --run
```
