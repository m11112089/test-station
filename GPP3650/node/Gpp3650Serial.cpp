#include "node/Gpp3650Serial.hpp"

#include <QElapsedTimer>
#include <QRandomGenerator>

#include <algorithm>
#include <cmath>

namespace {
constexpr double kSimLoadOhms = 10.0; // 模擬模式假設 10 歐姆電阻性負載
} // namespace

const std::array<double, 4>& Gpp3650Serial::ch3Voltages()
{
    static const std::array<double, 4> kLevels{1.8, 2.5, 3.3, 5.0};
    return kLevels;
}

Gpp3650Serial::Gpp3650Serial(QObject* parent)
    : QObject(parent)
{
}

bool Gpp3650Serial::open(const QString& portName, int baud)
{
    m_lastError.clear();

    if (m_sim) {
        m_idn = QStringLiteral("GW INSTEK,GPP-3650,SIMULATED,V0.00");
        m_simOpen = true;
        return true;
    }

    if (m_port.isOpen()) {
        m_port.close();
    }
    m_port.setPortName(portName);
    m_port.setBaudRate(baud);
    m_port.setDataBits(QSerialPort::Data8);
    m_port.setParity(QSerialPort::NoParity);
    m_port.setStopBits(QSerialPort::OneStop);
    m_port.setFlowControl(QSerialPort::NoFlowControl);

    if (!m_port.open(QIODevice::ReadWrite)) {
        m_lastError = QStringLiteral("open %1 failed: %2").arg(portName, m_port.errorString());
        return false;
    }
    m_port.clear();

    const QString idn = query(QStringLiteral("*IDN?"));
    if (!idn.contains(QStringLiteral("GPP"))) {
        m_lastError = QStringLiteral("device did not identify as GPP (*IDN? => '%1')").arg(idn);
        m_port.close();
        return false;
    }
    m_idn = idn;
    return true;
}

void Gpp3650Serial::close()
{
    if (m_sim) {
        m_simOpen = false;
        return;
    }
    if (m_port.isOpen()) {
        m_port.close();
    }
}

bool Gpp3650Serial::isOpen() const
{
    return m_sim ? m_simOpen : m_port.isOpen();
}

double Gpp3650Serial::clampVoltage(int ch, double volts)
{
    if (ch == 3) {
        // snap 到最接近的固定檔位
        const auto& levels = ch3Voltages();
        double best = levels.front();
        for (double level : levels) {
            if (std::fabs(volts - level) < std::fabs(volts - best)) {
                best = level;
            }
        }
        return best;
    }
    return std::clamp(volts, 0.0, kMaxVoltage);
}

double Gpp3650Serial::clampCurrent(double amps)
{
    return std::clamp(amps, 0.0, kMaxCurrent);
}

bool Gpp3650Serial::setOutput(int ch, bool on)
{
    if (ch < 1 || ch > kChannelCount) {
        m_lastError = QStringLiteral("invalid channel %1").arg(ch);
        return false;
    }
    if (m_sim) {
        m_simOn[static_cast<size_t>(ch - 1)] = on;
        return true;
    }
    return writeLine(QStringLiteral(":OUTPut%1:STATe %2")
                         .arg(ch)
                         .arg(on ? QStringLiteral("ON") : QStringLiteral("OFF")));
}

bool Gpp3650Serial::setAllOutputs(bool on)
{
    if (m_sim) {
        m_simOn.fill(on);
        return true;
    }
    return writeLine(on ? QStringLiteral(":ALLOUTON") : QStringLiteral(":ALLOUTOFF"));
}

bool Gpp3650Serial::setVoltage(int ch, double volts)
{
    if (ch < 1 || ch > kChannelCount) {
        m_lastError = QStringLiteral("invalid channel %1").arg(ch);
        return false;
    }
    const double v = clampVoltage(ch, volts);
    if (m_sim) {
        m_simV[static_cast<size_t>(ch - 1)] = v;
        return true;
    }
    const int decimals = (ch == 3) ? 1 : 3;
    return writeLine(QStringLiteral("VSET%1:%2").arg(ch).arg(QString::number(v, 'f', decimals)));
}

bool Gpp3650Serial::setCurrent(int ch, double amps)
{
    if (ch < 1 || ch > kChannelCount) {
        m_lastError = QStringLiteral("invalid channel %1").arg(ch);
        return false;
    }
    if (ch == 3) {
        return true; // CH3 電流固定, 靜默忽略
    }
    const double i = clampCurrent(amps);
    if (m_sim) {
        m_simI[static_cast<size_t>(ch - 1)] = i;
        return true;
    }
    return writeLine(QStringLiteral("ISET%1:%2").arg(ch).arg(QString::number(i, 'f', 4)));
}

Gpp3650Serial::ChannelMeas Gpp3650Serial::readChannel(int ch)
{
    ChannelMeas meas;
    if (ch < 1 || ch > kChannelCount) {
        m_lastError = QStringLiteral("invalid channel %1").arg(ch);
        return meas;
    }

    if (m_sim) {
        const auto idx = static_cast<size_t>(ch - 1);
        if (m_simOn[idx]) {
            const double noise = (QRandomGenerator::global()->generateDouble() - 0.5) * 0.004;
            meas.voltage = m_simV[idx] + noise;
            if (ch != 3) {
                meas.current = std::min(m_simI[idx], meas.voltage / kSimLoadOhms);
                meas.power   = meas.voltage * meas.current;
            }
        }
        meas.valid = true;
        return meas;
    }

    const QString raw = query(QStringLiteral(":MEASure%1:ALL?").arg(ch));
    if (raw.isEmpty()) {
        return meas; // m_lastError 已由 query() 設定
    }

    // 回應形如 "5.001V,0.5001A,2.501W" 或無單位, 一律剝除單位字元
    QString cleaned = raw;
    cleaned.remove(QLatin1Char('V')).remove(QLatin1Char('A')).remove(QLatin1Char('W'));
    const QStringList parts = cleaned.split(QLatin1Char(','));

    bool okV = false;
    meas.voltage = (parts.size() >= 1) ? parts[0].trimmed().toDouble(&okV) : 0.0;
    if (parts.size() >= 3) { // CH3 只有電壓有意義
        meas.current = parts[1].trimmed().toDouble();
        meas.power   = parts[2].trimmed().toDouble();
    }
    meas.valid = okV;
    if (!okV) {
        m_lastError = QStringLiteral("cannot parse MEAS reply '%1'").arg(raw);
    }
    return meas;
}

// ------------------------------------------------------------- private I/O

bool Gpp3650Serial::writeLine(const QString& cmd)
{
    const QByteArray data = cmd.toLatin1() + '\n';
    if (m_port.write(data) != data.size() || !m_port.waitForBytesWritten(kTimeoutMs)) {
        m_lastError = QStringLiteral("write '%1' failed: %2").arg(cmd, m_port.errorString());
        return false;
    }
    return true;
}

QString Gpp3650Serial::query(const QString& cmd)
{
    if (!writeLine(cmd)) {
        return {};
    }
    QByteArray line;
    QElapsedTimer timer;
    timer.start();
    while (timer.elapsed() < kTimeoutMs) {
        const int remaining = kTimeoutMs - static_cast<int>(timer.elapsed());
        if (!m_port.waitForReadyRead(std::max(1, remaining))) {
            break;
        }
        line += m_port.readAll();
        if (line.contains('\n')) {
            break;
        }
    }
    const int nl = line.indexOf('\n');
    if (nl < 0) {
        m_lastError = QStringLiteral("timeout waiting reply for '%1'").arg(cmd);
        return {};
    }
    return QString::fromLatin1(line.left(nl)).trimmed();
}
