#include "node/TerminalNode.hpp"
#include "common/Topics.hpp"

#include <QDateTime>
#include <QDebug>
#include <QJsonDocument>
#include <QJsonObject>

namespace {
constexpr int kRespawnDelayMs = 3000; // 同串口橋接的重連間隔
constexpr int kStatusPeriodMs = 2000;
constexpr int kPtyCols = 120; // 偽終端尺寸 (固定; 遠端沒有視窗可調)
constexpr int kPtyRows = 30;
} // namespace

TerminalNode::TerminalNode(Options opt, QObject* parent)
    : QObject(parent)
    , m_opt(std::move(opt))
{
    connect(this, &TerminalNode::txReceived, this, &TerminalNode::handleTx);
    connect(&m_pty, &PtyProcess::output,   this, &TerminalNode::onOutput);
    connect(&m_pty, &PtyProcess::finished, this, &TerminalNode::onFinished);
    connect(&m_respawnTimer, &QTimer::timeout, this, &TerminalNode::tryStart);
    connect(&m_statusTimer,  &QTimer::timeout, this, [this] { publishStatus(); });
    m_respawnTimer.setInterval(kRespawnDelayMs);
    m_respawnTimer.setSingleShot(true);
    m_statusTimer.setInterval(kStatusPeriodMs);
}

bool TerminalNode::start()
{
    m_pubRx     = std::make_unique<ecalwrap::Publisher>(topics::rawRx(m_opt.prefix));
    m_pubStatus = std::make_unique<ecalwrap::Publisher>(topics::status(m_opt.prefix));
    m_subTx     = std::make_unique<ecalwrap::Subscriber>(
        topics::rawTx(m_opt.prefix), [this](QByteArray b) { emit txReceived(std::move(b)); });

    tryStart();
    m_statusTimer.start();
    return true;
}

void TerminalNode::tryStart()
{
    if (m_pty.isRunning()) {
        return;
    }
    if (!m_pty.start(m_opt.command, kPtyCols, kPtyRows)) {
        const QString msg = QStringLiteral("failed to start \"%1\": %2")
                                .arg(m_opt.command, m_pty.errorString());
        qWarning().noquote() << msg;
        publishStatus(msg);
        m_respawnTimer.start();
        return;
    }
    qInfo().noquote() << QStringLiteral("spawned \"%1\" pid=%2 on pty %3x%4, topic prefix = %5")
                             .arg(m_opt.command).arg(m_pty.pid())
                             .arg(kPtyCols).arg(kPtyRows).arg(m_opt.prefix);
    publishStatus();
}

void TerminalNode::handleTx(const QByteArray& payload)
{
    if (payload.isEmpty()) {
        return;
    }
    if (!m_pty.isRunning()) {
        publishStatus(QStringLiteral("tx dropped: shell not running"));
        return;
    }
    m_pty.write(payload);
    m_txBytes += static_cast<quint64>(payload.size());
}

void TerminalNode::onOutput(const QByteArray& data)
{
    if (data.isEmpty()) {
        return;
    }
    m_rxBytes += static_cast<quint64>(data.size());
    m_pubRx->send(data);
}

void TerminalNode::onFinished(int exitCode, bool crashed)
{
    m_lastExitCode = exitCode;
    m_hasExited    = true;

    const QString msg = crashed
        ? QStringLiteral("shell killed by signal, code %1").arg(exitCode)
        : QStringLiteral("shell exited, code %1").arg(exitCode);
    qInfo().noquote() << msg;
    publishStatus(crashed ? msg : QString());

    m_respawnTimer.start(); // 同串口斷線重連: 3 秒後給一個新 shell
}

void TerminalNode::publishStatus(const QString& error)
{
    const bool running = m_pty.isRunning();
    const QJsonObject status{{QStringLiteral("device"), m_opt.prefix},
                             {QStringLiteral("connected"), running},
                             {QStringLiteral("command"), m_opt.command},
                             {QStringLiteral("pid"),
                              running ? static_cast<double>(m_pty.pid()) : 0.0},
                             {QStringLiteral("exit_code"),
                              m_hasExited ? QJsonValue(m_lastExitCode) : QJsonValue()},
                             {QStringLiteral("tx_bytes"), static_cast<double>(m_txBytes)},
                             {QStringLiteral("rx_bytes"), static_cast<double>(m_rxBytes)},
                             {QStringLiteral("error"), error},
                             {QStringLiteral("timestamp_ms"),
                              static_cast<double>(QDateTime::currentMSecsSinceEpoch())}};
    m_pubStatus->send(QJsonDocument(status).toJson(QJsonDocument::Compact));
}
