# =====================================================================
# TERMINAL — 本機 process (shell / 第三方 CLI 工具) 橋接 — 獨立 CMake 專案
#   terminal_node — process stdin/stdout <-> eCAL 橋接 driver
#                   (headless, 獨立 process; 測試流程呼叫 iperf 等工具用)
#   terminal_term — 互動終端 (minicom 風格, 只走 eCAL)
#
# 建置 (在本資料夾內):
#   cmake -S . -B build [平台參數見 repo 根目錄 README.md]
#   cmake --build build -j
# =====================================================================
cmake_minimum_required(VERSION 3.16)
project(terminal LANGUAGES CXX)

# 共用設定: Qt/eCAL 偵測 + station_common (common/ 之 ecal_c 包裝)
include("${CMAKE_CURRENT_SOURCE_DIR}/../cmake/StationSetup.cmake")

add_executable(terminal_node
    node/main.cpp
    node/TerminalNode.cpp
    node/TerminalNode.hpp
    node/PtyProcess.cpp
    node/PtyProcess.hpp
)
target_include_directories(terminal_node PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(terminal_node PRIVATE station_common)
if(WIN32)
    # ConPTY (CreatePseudoConsole) 宣告需要 Win10 SDK 等級; API 在 kernel32, 無額外相依
    target_compile_definitions(terminal_node PRIVATE _WIN32_WINNT=0x0A00)
else()
    # forkpty: glibc 2.34 起併入 libc, 舊版在 libutil (皆為系統 C 庫, 無額外套件)
    find_library(UTIL_LIBRARY util)
    if(UTIL_LIBRARY)
        target_link_libraries(terminal_node PRIVATE ${UTIL_LIBRARY})
    endif()
endif()
station_deploy(terminal_node)   # Windows: 建置後把 Qt/eCAL DLL 佈到 exe 旁

add_executable(terminal_term
    term/main.cpp
)
target_include_directories(terminal_term PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(terminal_term PRIVATE station_common)
station_deploy(terminal_term)
