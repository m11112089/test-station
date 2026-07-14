#include "node/NodeApp.hpp"
#include "common/Topics.hpp"

#include <QDateTime>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

#include <QtGlobal>

#include <algorithm>

namespace {
constexpr double kMinRateHz         = 0.1;
constexpr double kMaxRateHz         = 10.0;
constexpr int    kReconnectDelayMs  = 3000;
constexpr int    kStatusPeriodMs    = 2000; // status 心跳

qint64 nowMs()
{
    return QDateTime::currentMSecsSinceEpoch();
}
} // namespace

NodeApp::NodeApp(Options opt, QObject* parent)
    : QObject(parent)
    , m_opt(std::move(opt))
{
    m_psu.setSimulated(m_opt.sim);

    // eCAL 執行緒 -> 主執行緒 marshal (AutoConnection 自動變 QueuedConnection)
    connect(this, &NodeApp::outputCmdReceived,   this, &NodeApp::handleOutputCmd);
    connect(this, &NodeApp::setpointCmdReceived, this, &NodeApp::handleSetpointCmd);
    connect(this, &NodeApp::configCmdReceived,   this, &NodeApp::handleConfigCmd);

    connect(&m_pollTimer,      &QTimer::timeout, this, &NodeApp::pollOnce);
    connect(&m_reconnectTimer, &QTimer::timeout, this, &NodeApp::tryConnect);
    connect(&m_statusTimer,    &QTimer::timeout, this, [this] { publishStatus(); });
    m_reconnectTimer.setInterval(kReconnectDelayMs);
    m_statusTimer.setInterval(kStatusPeriodMs);
}

bool NodeApp::start()
{
    const QString& dev = m_opt.deviceName;
    m_pubMeas    = std::make_unique<ecalwrap::Publisher>(topics::meas(dev));
    m_pubStatus  = std::make_unique<ecalwrap::Publisher>(topics::status(dev));
    m_subOutput  = std::make_unique<ecalwrap::Subscriber>(
        topics::cmdOutput(dev),   [this](QByteArray b) { emit outputCmdReceived(std::move(b)); });
    m_subSetpoint = std::make_unique<ecalwrap::Subscriber>(
        topics::cmdSetpoint(dev), [this](QByteArray b) { emit setpointCmdReceived(std::move(b)); });
    m_subConfig  = std::make_unique<ecalwrap::Subscriber>(
        topics::cmdConfig(dev),   [this](QByteArray b) { emit configCmdReceived(std::move(b)); });

    tryConnect();
    applyRate(m_opt.rateHz);
    m_statusTimer.start();
    return true;
}

void NodeApp::tryConnect()
{
    if (m_psu.isOpen()) {
        m_reconnectTimer.stop();
        return;
    }
    if (m_psu.open(m_opt.port, m_opt.baud)) {
        m_reconnectTimer.stop();
        qInfo().noquote() << "connected:" << m_psu.idn();
        publishStatus();
    } else {
        qWarning().noquote() << "connect failed:" << m_psu.lastError();
        publishStatus(m_psu.lastError());
        m_reconnectTimer.start();
    }
}

void NodeApp::applyRate(double rateHz)
{
    m_opt.rateHz = std::clamp(rateHz, kMinRateHz, kMaxRateHz);
    m_pollTimer.start(static_cast<int>(1000.0 / m_opt.rateHz));
}

void NodeApp::onSerialFault(const QString& context)
{
    qWarning().noquote() << context << "->" << m_psu.lastError();
    m_psu.close();
    publishStatus(QStringLiteral("%1: %2").arg(context, m_psu.lastError()));
    m_reconnectTimer.start();
}

void NodeApp::pollOnce()
{
    if (!m_psu.isOpen()) {
        return; // 等 reconnect timer
    }

    QJsonArray channels;
    for (int ch = 1; ch <= Gpp3650Serial::kChannelCount; ++ch) {
        const auto meas = m_psu.readChannel(ch);
        if (!meas.valid) {
            onSerialFault(QStringLiteral("MEAS ch%1").arg(ch));
            return;
        }
        QJsonObject obj{{QStringLiteral("ch"), ch},
                        {QStringLiteral("voltage"), meas.voltage}};
        if (ch != 3) { // CH3 無電流/功率回讀
            obj.insert(QStringLiteral("current"), meas.current);
            obj.insert(QStringLiteral("power"),   meas.power);
        }
        channels.append(obj);
    }

    const QJsonObject frame{{QStringLiteral("device"), m_opt.deviceName},
                            {QStringLiteral("seq"), static_cast<double>(m_seq++)},
                            {QStringLiteral("timestamp_ms"), static_cast<double>(nowMs())},
                            {QStringLiteral("channels"), channels}};
    m_pubMeas->send(QJsonDocument(frame).toJson(QJsonDocument::Compact));
}

void NodeApp::publishStatus(const QString& error)
{
    const QJsonObject status{{QStringLiteral("device"), m_opt.deviceName},
                             {QStringLiteral("connected"), m_psu.isOpen()},
                             {QStringLiteral("port"), m_opt.port},
                             {QStringLiteral("idn"), m_psu.idn()},
                             {QStringLiteral("sim"), m_opt.sim},
                             {QStringLiteral("rate_hz"), m_opt.rateHz},
                             {QStringLiteral("error"), error},
                             {QStringLiteral("timestamp_ms"), static_cast<double>(nowMs())}};
    m_pubStatus->send(QJsonDocument(status).toJson(QJsonDocument::Compact));
}

// ------------------------------------------------------------ cmd handlers

void NodeApp::handleOutputCmd(const QByteArray& payload)
{
    const QJsonObject obj = QJsonDocument::fromJson(payload).object();
    if (!obj.contains(QStringLiteral("ch")) || !obj.contains(QStringLiteral("on"))) {
        qWarning().noquote() << "cmd/output: missing 'ch' or 'on':" << payload;
        return;
    }
    const int  ch = obj.value(QStringLiteral("ch")).toInt(-1);
    const bool on = obj.value(QStringLiteral("on")).toBool();

    if (!m_psu.isOpen()) {
        publishStatus(QStringLiteral("cmd/output ignored: not connected"));
        return;
    }

    bool ok = false;
    if (ch == 0) {
        ok = m_psu.setAllOutputs(on);
    } else if (ch >= 1 && ch <= Gpp3650Serial::kChannelCount) {
        ok = m_psu.setOutput(ch, on);
    } else {
        qWarning().noquote() << "cmd/output: invalid ch" << ch;
        return;
    }
    if (!ok) {
        onSerialFault(QStringLiteral("cmd/output ch%1").arg(ch));
    }
}

void NodeApp::handleSetpointCmd(const QByteArray& payload)
{
    const QJsonObject obj = QJsonDocument::fromJson(payload).object();
    const int ch = obj.value(QStringLiteral("ch")).toInt(-1);
    if (ch < 1 || ch > Gpp3650Serial::kChannelCount) {
        qWarning().noquote() << "cmd/setpoint: invalid ch:" << payload;
        return;
    }
    if (!m_psu.isOpen()) {
        publishStatus(QStringLiteral("cmd/setpoint ignored: not connected"));
        return;
    }

    if (obj.contains(QStringLiteral("voltage"))) {
        const double vReq = obj.value(QStringLiteral("voltage")).toDouble();
        const double v    = Gpp3650Serial::clampVoltage(ch, vReq);
        if (!qFuzzyCompare(v + 1.0, vReq + 1.0)) {
            qWarning().noquote()
                << QStringLiteral("cmd/setpoint ch%1: voltage %2 clamped/snapped to %3")
                       .arg(ch).arg(vReq).arg(v);
        }
        if (!m_psu.setVoltage(ch, v)) {
            onSerialFault(QStringLiteral("VSET ch%1").arg(ch));
            return;
        }
    }
    if (obj.contains(QStringLiteral("current")) && ch != 3) {
        const double iReq = obj.value(QStringLiteral("current")).toDouble();
        if (!m_psu.setCurrent(ch, Gpp3650Serial::clampCurrent(iReq))) {
            onSerialFault(QStringLiteral("ISET ch%1").arg(ch));
            return;
        }
    }
}

void NodeApp::handleConfigCmd(const QByteArray& payload)
{
    const QJsonObject obj = QJsonDocument::fromJson(payload).object();
    if (obj.contains(QStringLiteral("rate_hz"))) {
        const double hz = obj.value(QStringLiteral("rate_hz")).toDouble(1.0);
        applyRate(hz);
        qInfo().noquote() << "update rate set to" << m_opt.rateHz << "Hz";
        publishStatus();
    }
}
