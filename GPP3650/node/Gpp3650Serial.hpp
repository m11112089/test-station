#pragma once
//
// GW Instek GPP-3650 三通道可程式電源供應器 SCPI 驅動 (RS-232/USB CDC, 115200 8N1, '\n' 結尾)
//
// 型號規格 (依 GPP-3060/6030/3650 手冊):
//   CH1/CH2: Vset 0 ~ 36.500 V, Iset 0 ~ 5.2000 A (獨立輸出)
//   CH3    : 固定檔位 1.8 / 2.5 / 3.3 / 5.0 V, 5 A, 不可設電流、無電流回讀
//
// 使用的指令 (與既有 Python 版一致):
//   *IDN?                     回應需含 "GPP"
//   VSET<ch>:<v> / ISET<ch>:<i>
//   :OUTPut<ch>:STATe ON|OFF
//   :ALLOUTON / :ALLOUTOFF
//   :MEASure<ch>:ALL?         回應 "v,i,p" (可能帶 V/A/W 單位字尾)
//
// 執行緒模型: 所有方法皆為阻塞式, 只允許在擁有 QSerialPort 的執行緒 (主執行緒) 呼叫。
//
#include <QObject>
#include <QSerialPort>
#include <QString>

#include <array>

class Gpp3650Serial : public QObject {
    Q_OBJECT
public:
    static constexpr int    kChannelCount = 3;
    static constexpr double kMaxVoltage   = 36.5;   // CH1/CH2 Vset 上限
    static constexpr double kMaxCurrent   = 5.2;    // CH1/CH2 Iset 上限
    static constexpr int    kTimeoutMs    = 1000;
    // CH3 固定檔位
    static const std::array<double, 4>& ch3Voltages();

    struct ChannelMeas {
        double voltage = 0.0;
        double current = 0.0;
        double power   = 0.0;
        bool   valid   = false;
    };

    explicit Gpp3650Serial(QObject* parent = nullptr);

    // 模擬模式: 不開串口, 以內部狀態回應, 供無硬體驗證 GUI/topic 流程
    void setSimulated(bool sim) { m_sim = sim; }
    bool isSimulated() const { return m_sim; }

    bool open(const QString& portName, int baud = 115200);
    void close();
    bool isOpen() const;

    QString idn() const { return m_idn; }
    QString lastError() const { return m_lastError; }

    bool setOutput(int ch, bool on);          // ch: 1..3
    bool setAllOutputs(bool on);
    bool setVoltage(int ch, double volts);    // 自動 clamp; CH3 snap 到固定檔位
    bool setCurrent(int ch, double amps);     // CH3 不支援 -> 忽略並回 true
    ChannelMeas readChannel(int ch);

    static double clampVoltage(int ch, double volts);
    static double clampCurrent(double amps);

private:
    bool writeLine(const QString& cmd);
    QString query(const QString& cmd);

    QSerialPort m_port;
    QString     m_idn;
    QString     m_lastError;
    bool        m_sim = false;
    bool        m_simOpen = false;

    // 模擬狀態 (index 0 = CH1)
    std::array<bool,   kChannelCount> m_simOn{};
    std::array<double, kChannelCount> m_simV{0.0, 0.0, 5.0};
    std::array<double, kChannelCount> m_simI{1.0, 1.0, 5.0};
};
