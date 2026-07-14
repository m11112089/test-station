#pragma once
//
// 驗證用 GUI — 僅透過 eCAL 與 gpp3650_node 溝通, 不直接碰串口。
// Topic 方向與 node 相反:
//   發佈: <dev>/cmd/output, <dev>/cmd/setpoint, <dev>/cmd/config
//   訂閱: <dev>/meas, <dev>/status
//
#include "common/EcalWrap.hpp"

#include <QComboBox>
#include <QDoubleSpinBox>
#include <QLabel>
#include <QWidget>

#include <array>
#include <memory>

class GuiWindow : public QWidget {
    Q_OBJECT
public:
    explicit GuiWindow(QString deviceName, QWidget* parent = nullptr);

signals:
    // 由 eCAL 執行緒 emit, AutoConnection 自動 queue 回 GUI 執行緒
    void measReceived(QByteArray payload);
    void statusReceived(QByteArray payload);

private slots:
    void onMeas(const QByteArray& payload);
    void onStatus(const QByteArray& payload);

private:
    void sendOutputCmd(int ch, bool on);
    void sendSetpointCmd(int ch);
    void sendConfigCmd();
    QWidget* buildChannelBox(int ch);

    QString m_dev;

    std::unique_ptr<ecalwrap::Publisher>  m_pubOutput;
    std::unique_ptr<ecalwrap::Publisher>  m_pubSetpoint;
    std::unique_ptr<ecalwrap::Publisher>  m_pubConfig;
    std::unique_ptr<ecalwrap::Subscriber> m_subMeas;
    std::unique_ptr<ecalwrap::Subscriber> m_subStatus;

    QLabel* m_statusLabel = nullptr;
    QDoubleSpinBox* m_rateSpin = nullptr;

    // index 0 = CH1
    std::array<QLabel*, 3>         m_readout{};
    std::array<QDoubleSpinBox*, 2> m_vSpin{};   // CH1/CH2
    std::array<QDoubleSpinBox*, 2> m_iSpin{};   // CH1/CH2
    QComboBox* m_ch3Combo = nullptr;
};
