# =====================================================================
# 平台通用除錯工具 — 獨立 CMake 專案
#   ecal_topic — topic echo/pub (類 rostopic), 驗證任何裝置 node 用
# =====================================================================
cmake_minimum_required(VERSION 3.16)
project(station_tools LANGUAGES CXX)

include("${CMAKE_CURRENT_SOURCE_DIR}/../cmake/StationSetup.cmake")

add_executable(ecal_topic ecal_topic.cpp)
target_link_libraries(ecal_topic PRIVATE station_common)
station_deploy(ecal_topic)
