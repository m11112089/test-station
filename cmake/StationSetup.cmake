# =====================================================================
# StationSetup.cmake — 所有裝置專案共用的建置設定
#
# 每個裝置資料夾是「獨立的 CMake 專案」, 在自己的 CMakeLists.txt 中:
#
#     cmake_minimum_required(VERSION 3.16)
#     project(<device> LANGUAGES CXX)
#     include(${CMAKE_CURRENT_SOURCE_DIR}/../cmake/StationSetup.cmake)
#     add_executable(<device>_node ...)
#     target_link_libraries(<device>_node PRIVATE station_common)
#
# 本檔提供:
#   - C++17 / AUTOMOC
#   - Qt 偵測 (QT_CORE_LIBS / QT_GUI_LIBS)
#   - eCAL C API 偵測與連結 (ECAL_C_TARGET); Windows MinGW 下自動以
#     gendef + dlltool 產生 import lib
#   - station_common 靜態庫 (common/ 之 ecal_c 包裝與 topic 命名)
# =====================================================================
include_guard(GLOBAL)

get_filename_component(STATION_ROOT "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

# ---------------------------------------------------------------------
# Qt (跨平台 GUI / Serial / TCP; Qt6 優先, Qt5.12+ 備援)
# ---------------------------------------------------------------------
find_package(Qt6 QUIET COMPONENTS Core SerialPort Widgets)
if(Qt6_FOUND)
    set(QT_CORE_LIBS Qt6::Core Qt6::SerialPort)
    set(QT_GUI_LIBS  Qt6::Widgets)
    message(STATUS "Using Qt6: ${Qt6_DIR}")
else()
    find_package(Qt5 5.12 REQUIRED COMPONENTS Core SerialPort Widgets)
    set(QT_CORE_LIBS Qt5::Core Qt5::SerialPort)
    set(QT_GUI_LIBS  Qt5::Widgets)
    message(STATUS "Using Qt5: ${Qt5_DIR}")
endif()

# ---------------------------------------------------------------------
# eCAL C API (ecal_c)
# 兩平台統一走 C API, 原始碼零分支; 只有「連結方式」依平台分支:
#   - Windows + MinGW: 官方 .lib 為 MSVC 格式, GNU ld 連不動
#                      -> configure 時自動以 gendef + dlltool 產生
#                         MinGW 相容的 import lib (libecal_core_c.a)
#   - Windows + MSVC : 直接連 ecal_core_c.lib
#   - Linux (Ubuntu) : 官方套件為 GCC 建置, find_package / find_library 即可
# ---------------------------------------------------------------------
if(WIN32)
    set(ECAL_ROOT "C:/eCAL" CACHE PATH "eCAL install root")
    set(ECAL_C_INCLUDE_DIR "${ECAL_ROOT}/include")
    if(NOT EXISTS "${ECAL_C_INCLUDE_DIR}/ecal_c/ecal.h")
        message(FATAL_ERROR "找不到 ${ECAL_C_INCLUDE_DIR}/ecal_c/ecal.h, 請以 -DECAL_ROOT= 指定 eCAL 安裝路徑")
    endif()

    if(MINGW)
        set(ECAL_C_DLL "${ECAL_ROOT}/bin/ecal_core_c.dll")
        set(ECAL_C_IMPLIB_DIR "${CMAKE_BINARY_DIR}/mingw_implib")
        set(ECAL_C_LIBRARY "${ECAL_C_IMPLIB_DIR}/libecal_core_c.a")
        if(NOT EXISTS "${ECAL_C_LIBRARY}")
            find_program(GENDEF_EXE  gendef  REQUIRED)
            find_program(DLLTOOL_EXE dlltool REQUIRED)
            file(MAKE_DIRECTORY "${ECAL_C_IMPLIB_DIR}")
            message(STATUS "Generating MinGW import lib for ${ECAL_C_DLL}")
            execute_process(
                COMMAND "${GENDEF_EXE}" "${ECAL_C_DLL}"
                WORKING_DIRECTORY "${ECAL_C_IMPLIB_DIR}"
                RESULT_VARIABLE _gendef_rc)
            execute_process(
                COMMAND "${DLLTOOL_EXE}" -d ecal_core_c.def -D ecal_core_c.dll -l "${ECAL_C_LIBRARY}"
                WORKING_DIRECTORY "${ECAL_C_IMPLIB_DIR}"
                RESULT_VARIABLE _dlltool_rc)
            if(NOT _gendef_rc EQUAL 0 OR NOT _dlltool_rc EQUAL 0)
                message(FATAL_ERROR "gendef/dlltool 產生 MinGW import lib 失敗 (gendef=${_gendef_rc}, dlltool=${_dlltool_rc})")
            endif()
        endif()
    else() # MSVC
        set(ECAL_C_LIBRARY "${ECAL_ROOT}/lib/ecal_core_c.lib")
    endif()

    add_library(ecal_c_api UNKNOWN IMPORTED)
    set_target_properties(ecal_c_api PROPERTIES
        IMPORTED_LOCATION "${ECAL_C_LIBRARY}"
        INTERFACE_INCLUDE_DIRECTORIES "${ECAL_C_INCLUDE_DIR}")
    set(ECAL_C_TARGET ecal_c_api)
else()
    # Ubuntu: 先試官方 CMake config, 找不到再退回 find_library
    find_package(eCAL CONFIG QUIET)
    if(TARGET eCAL::core_c)
        set(ECAL_C_TARGET eCAL::core_c)
        message(STATUS "Using eCAL::core_c from find_package(eCAL)")
    else()
        find_path(ECAL_C_INCLUDE_DIR ecal_c/ecal.h
                  PATHS /usr/include /usr/local/include REQUIRED)
        find_library(ECAL_C_LIBRARY NAMES ecal_core_c ecal_c REQUIRED)
        add_library(ecal_c_api UNKNOWN IMPORTED)
        set_target_properties(ecal_c_api PROPERTIES
            IMPORTED_LOCATION "${ECAL_C_LIBRARY}"
            INTERFACE_INCLUDE_DIRECTORIES "${ECAL_C_INCLUDE_DIR}")
        set(ECAL_C_TARGET ecal_c_api)
    endif()
endif()

# ---------------------------------------------------------------------
# station_common — 跨裝置共用程式庫 (eCAL 包裝 / topic 命名)
# ---------------------------------------------------------------------
add_library(station_common STATIC
    "${STATION_ROOT}/common/EcalWrap.cpp"
    "${STATION_ROOT}/common/EcalWrap.hpp"
    "${STATION_ROOT}/common/Topics.hpp"
)
target_include_directories(station_common PUBLIC "${STATION_ROOT}")
target_link_libraries(station_common PUBLIC ${QT_CORE_LIBS} ${ECAL_C_TARGET})

# ---------------------------------------------------------------------
# station_deploy(<target>) — Windows: 建置後把所有執行相依 DLL 複製到
# exe 旁, 讓 exe 在任何 PATH 環境都能直接執行 (app 目錄搜尋優先權最高,
# 不會誤載 C:\eCAL\bin 內的 MSVC Qt DLL 或 UCRT 版 MinGW runtime)。
#   - windeployqt --compiler-runtime: Qt DLL + platform plugin + MinGW runtime
#   - eCAL: ecal_core_c / ecal_core 及其 MSVC runtime
# Linux 上為 no-op (系統庫由 apt 管理)。
# ---------------------------------------------------------------------
if(WIN32 AND MINGW)
    if(Qt6_FOUND)
        get_target_property(_qmake_exe Qt6::qmake IMPORTED_LOCATION)
    else()
        get_target_property(_qmake_exe Qt5::qmake IMPORTED_LOCATION)
    endif()
    get_filename_component(_qt_bin_dir "${_qmake_exe}" DIRECTORY)
    find_program(WINDEPLOYQT_EXE windeployqt HINTS "${_qt_bin_dir}" REQUIRED)

    set(STATION_ECAL_RUNTIME_DLLS
        "${ECAL_ROOT}/bin/ecal_core_c.dll"
        "${ECAL_ROOT}/bin/ecal_core.dll")
    foreach(_dll msvcp140.dll msvcp140_1.dll msvcp140_2.dll
                 msvcp140_atomic_wait.dll msvcp140_codecvt_ids.dll
                 vcruntime140.dll vcruntime140_1.dll concrt140.dll)
        if(EXISTS "${ECAL_ROOT}/bin/${_dll}")
            list(APPEND STATION_ECAL_RUNTIME_DLLS "${ECAL_ROOT}/bin/${_dll}")
        endif()
    endforeach()
endif()

function(station_deploy tgt)
    if(NOT (WIN32 AND MINGW))
        return()
    endif()
    add_custom_command(TARGET ${tgt} POST_BUILD
        COMMAND "${WINDEPLOYQT_EXE}" --compiler-runtime --no-translations
                --no-system-d3d-compiler --no-opengl-sw
                "$<TARGET_FILE:${tgt}>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                ${STATION_ECAL_RUNTIME_DLLS}
                "$<TARGET_FILE_DIR:${tgt}>"
        COMMENT "Deploying Qt/eCAL runtime beside ${tgt}"
        VERBATIM)
endfunction()
