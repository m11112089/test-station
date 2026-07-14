#pragma once
//
// Topic 命名規約: <device_type>/<port_id>/<direction>/<subject>
// port_id 由串口名稱推導 (不另取邏輯名稱):
//   Windows "COM4"        -> COM4
//   Ubuntu  "/dev/ttyUSB0" -> ttyUSB0   (取路徑最後一段)
//   模擬模式 (--sim 無 --port) -> SIM
// 例: gpp3650/COM4/meas, gpp3650/ttyUSB0/cmd/setpoint
//
// 注意: Windows 的 COM 編號與 Linux 的 ttyUSBx 會隨插拔/重開機改變;
// Linux 要穩定識別可改傳 /dev/serial/by-id/... (仍取最後一段)。
//
// Node 訂閱 (GUI / 測試流程發佈):
//   <dev>/cmd/output    {"ch":0..3, "on":true}            ch=0 代表 ALL
//   <dev>/cmd/setpoint  {"ch":1..3, "voltage":5.0, "current":1.0}
//                       voltage / current 皆為選填, 只套用有出現的欄位
//                       CH3 只接受 voltage, 且會 snap 到 1.8/2.5/3.3/5.0
//   <dev>/cmd/config    {"rate_hz":1.0}                   量測發佈頻率 0.1~10Hz
//
// Node 發佈 (GUI / 測試流程訂閱):
//   <dev>/meas          {"device":"gpp3650","seq":n,"timestamp_ms":...,
//                        "channels":[{"ch":1,"voltage":..,"current":..,"power":..},
//                                    {"ch":2,...},
//                                    {"ch":3,"voltage":..}]}
//   <dev>/status        {"device":"...","connected":bool,"port":"COM3","idn":"...",
//                        "sim":bool,"rate_hz":1.0,"error":"","timestamp_ms":...}
//
// Payload 一律為 UTF-8 JSON, 方便用 eCAL Monitor 直接目視除錯。
//
#include <QString>

namespace topics {

// 由串口名稱推導 port 識別: "COM4"->"COM4", "/dev/ttyUSB0"->"ttyUSB0", 空(模擬)->"SIM"
inline QString portId(const QString& port)
{
    if (port.isEmpty()) {
        return QStringLiteral("SIM");
    }
    const int slash = port.lastIndexOf(QLatin1Char('/'));
    return (slash >= 0) ? port.mid(slash + 1) : port;
}

// 完整 topic 前綴, 例: devicePrefix("gpp3650", "COM4") -> "gpp3650/COM4"
inline QString devicePrefix(const QString& deviceType, const QString& port)
{
    return deviceType + QLatin1Char('/') + portId(port);
}

inline QString cmdOutput(const QString& dev)   { return dev + QStringLiteral("/cmd/output"); }
inline QString cmdSetpoint(const QString& dev) { return dev + QStringLiteral("/cmd/setpoint"); }
inline QString cmdConfig(const QString& dev)   { return dev + QStringLiteral("/cmd/config"); }
inline QString meas(const QString& dev)        { return dev + QStringLiteral("/meas"); }
inline QString status(const QString& dev)      { return dev + QStringLiteral("/status"); }

} // namespace topics
