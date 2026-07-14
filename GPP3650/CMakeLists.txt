# =====================================================================
# GPP-3650 三通道可程式電源供應器 — 獨立 CMake 專案
#   gpp3650_node — device driver node (headless, 獨立 process)
#   gpp3650_gui  — 驗證用 GUI (topic 方向與 node 相反)
#
# 建置 (在本資料夾內):
#   cmake -S . -B build [平台參數見 repo 根目錄 README.md]
#   cmake --build build -j
# =====================================================================
cmake_minimum_required(VERSION 3.16)
project(gpp3650 LANGUAGES CXX)

option(BUILD_GUI "Build the eCAL validation GUI" ON)

# 共用設定: Qt/eCAL 偵測 + station_common (common/ 之 ecal_c 包裝)
include("${CMAKE_CURRENT_SOURCE_DIR}/../cmake/StationSetup.cmake")

add_executable(gpp3650_node
    node/main.cpp
    node/NodeApp.cpp
    node/NodeApp.hpp
    node/Gpp3650Serial.cpp
    node/Gpp3650Serial.hpp
)
target_include_directories(gpp3650_node PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(gpp3650_node PRIVATE station_common)
station_deploy(gpp3650_node)   # Windows: 建置後把 Qt/eCAL DLL 佈到 exe 旁

if(BUILD_GUI)
    add_executable(gpp3650_gui
        gui/main.cpp
        gui/GuiWindow.cpp
        gui/GuiWindow.hpp
    )
    target_include_directories(gpp3650_gui PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    target_link_libraries(gpp3650_gui PRIVATE station_common ${QT_GUI_LIBS})
    station_deploy(gpp3650_gui)
endif()
