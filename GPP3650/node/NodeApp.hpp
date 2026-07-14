#pragma once
//
// GPP-3650 device driver node 組裝層:
//   - 訂閱 <dev>/cmd/output, <dev>/cmd/setpoint, <dev>/cmd/config
//   - 以設定頻率 (預設 1 Hz) 輪詢三通道量測並發佈 <dev>/meas
//   - 連線狀態 / 錯誤發佈 <dev>/status, 斷線自動重連
//
// 執行緒模型:
//   eCAL 訂閱 callback 在 eCAL 內部執行緒觸發 -> 只 emit Qt signal,
//   由 Qt 事件迴圈自動 queue 回主執行緒後才碰 QSerialPort。
//   串口交易只發生在主執行緒。
//
#include "node/Gpp3650Serial.hpp"
#include "common/EcalWrap.hpp"

#include <QObject>
#include <QString>
#include <QTimer>

#include <memory>

class NodeApp : public QObject {
    Q_OBJECT
public:
    struct Options {
        QString deviceName;   // topic 前綴, 由 main 依 port 推導 (gpp3650/COM4, gpp3650/ttyUSB0, gpp3650/SIM)
        QString port;         // e.g. COM3 / /dev/ttyUSB0
        int     baud   = 115200;
        double  rateHz = 1.0;
        bool    sim    = false;
    };

    explicit NodeApp(Options opt, QObject* parent = nullptr);

    bool start();

signals:
    // 由 eCAL 執行緒 emit, AutoConnection 會自動 queue 回主執行緒
    void outputCmdReceived(QByteArray payload);
    void setpointCmdReceived(QByteArray payload);
    void configCmdReceived(QByteArray payload);

private slots:
    void handleOutputCmd(const QByteArray& payload);
    void handleSetpointCmd(const QByteArray& payload);
    void handleConfigCmd(const QByteArray& payload);
    void pollOnce();
    void tryConnect();

private:
    void applyRate(double rateHz);
    void onSerialFault(const QString& context);
    void publishStatus(const QString& error = QString());

    Options       m_opt;
    Gpp3650Serial m_psu;

    std::unique_ptr<ecalwrap::Publisher>  m_pubMeas;
    std::unique_ptr<ecalwrap::Publisher>  m_pubStatus;
    std::unique_ptr<ecalwrap::Subscriber> m_subOutput;
    std::unique_ptr<ecalwrap::Subscriber> m_subSetpoint;
    std::unique_ptr<ecalwrap::Subscriber> m_subConfig;

    QTimer  m_pollTimer;
    QTimer  m_reconnectTimer;
    QTimer  m_statusTimer;
    quint64 m_seq = 0;
};
