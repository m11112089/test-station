#include "gui/GuiWindow.hpp"
#include "common/Topics.hpp"

#include <QDateTime>
#include <QGridLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPushButton>
#include <QVBoxLayout>

namespace {
// 與 Gpp3650Serial 中的裝置規格一致 (GUI 不連結 node 的程式碼, 僅靠 topic 溝通)
constexpr double kMaxVoltage = 36.5;
constexpr double kMaxCurrent = 5.2;
const double     kCh3Levels[] = {1.8, 2.5, 3.3, 5.0};
} // namespace

GuiWindow::GuiWindow(QString deviceName, QWidget* parent)
    : QWidget(parent)
    , m_dev(std::move(deviceName))
{
    setWindowTitle(QStringLiteral("GPP-3650 eCAL 驗證平台  [%1]").arg(m_dev));

    m_pubOutput   = std::make_unique<ecalwrap::Publisher>(topics::cmdOutput(m_dev));
    m_pubSetpoint = std::make_unique<ecalwrap::Publisher>(topics::cmdSetpoint(m_dev));
    m_pubConfig   = std::make_unique<ecalwrap::Publisher>(topics::cmdConfig(m_dev));
    m_subMeas     = std::make_unique<ecalwrap::Subscriber>(
        topics::meas(m_dev),   [this](QByteArray b) { emit measReceived(std::move(b)); });
    m_subStatus   = std::make_unique<ecalwrap::Subscriber>(
        topics::status(m_dev), [this](QByteArray b) { emit statusReceived(std::move(b)); });

    connect(this, &GuiWindow::measReceived,   this, &GuiWindow::onMeas);
    connect(this, &GuiWindow::statusReceived, this, &GuiWindow::onStatus);

    auto* root = new QVBoxLayout(this);

    m_statusLabel = new QLabel(QStringLiteral("等待 node status..."));
    m_statusLabel->setStyleSheet(QStringLiteral(
        "background:#202020;color:#00ff00;font-family:Consolas;padding:6px;"));
    m_statusLabel->setWordWrap(true);
    root->addWidget(m_statusLabel);

    auto* chRow = new QHBoxLayout();
    for (int ch = 1; ch <= 3; ++ch) {
        chRow->addWidget(buildChannelBox(ch));
    }
    root->addLayout(chRow);

    // 全域控制 + 更新頻率
    auto* bottom = new QHBoxLayout();
    auto* allOn  = new QPushButton(QStringLiteral("全部輸出 (ALL ON)"));
    auto* allOff = new QPushButton(QStringLiteral("全部關閉 (ALL OFF)"));
    allOn->setStyleSheet(QStringLiteral("background:#2ecc71;color:white;font-weight:bold;"));
    allOff->setStyleSheet(QStringLiteral("background:#e74c3c;color:white;font-weight:bold;"));
    connect(allOn,  &QPushButton::clicked, this, [this] { sendOutputCmd(0, true); });
    connect(allOff, &QPushButton::clicked, this, [this] { sendOutputCmd(0, false); });
    bottom->addWidget(allOn);
    bottom->addWidget(allOff);
    bottom->addStretch();

    bottom->addWidget(new QLabel(QStringLiteral("更新頻率 (Hz):")));
    m_rateSpin = new QDoubleSpinBox();
    m_rateSpin->setRange(0.1, 10.0);
    m_rateSpin->setValue(1.0);
    m_rateSpin->setSingleStep(0.5);
    auto* rateApply = new QPushButton(QStringLiteral("套用"));
    connect(rateApply, &QPushButton::clicked, this, &GuiWindow::sendConfigCmd);
    bottom->addWidget(m_rateSpin);
    bottom->addWidget(rateApply);
    root->addLayout(bottom);
}

QWidget* GuiWindow::buildChannelBox(int ch)
{
    auto* box  = new QGroupBox(QStringLiteral("CH%1").arg(ch));
    auto* grid = new QGridLayout(box);
    const int idx = ch - 1;

    grid->addWidget(new QLabel(QStringLiteral("電壓 (V):")), 0, 0);
    if (ch == 3) {
        m_ch3Combo = new QComboBox();
        for (double level : kCh3Levels) {
            m_ch3Combo->addItem(QString::number(level, 'f', 1));
        }
        m_ch3Combo->setCurrentIndex(3); // 5.0V
        grid->addWidget(m_ch3Combo, 0, 1);
        grid->addWidget(new QLabel(QStringLiteral("電流: 固定 5A")), 1, 0, 1, 2);
    } else {
        auto* vSpin = new QDoubleSpinBox();
        vSpin->setRange(0.0, kMaxVoltage);
        vSpin->setDecimals(3);
        vSpin->setValue(5.0);
        m_vSpin[static_cast<size_t>(idx)] = vSpin;
        grid->addWidget(vSpin, 0, 1);

        grid->addWidget(new QLabel(QStringLiteral("電流 (A):")), 1, 0);
        auto* iSpin = new QDoubleSpinBox();
        iSpin->setRange(0.0, kMaxCurrent);
        iSpin->setDecimals(4);
        iSpin->setValue(1.0);
        m_iSpin[static_cast<size_t>(idx)] = iSpin;
        grid->addWidget(iSpin, 1, 1);
    }

    auto* applyBtn = new QPushButton(QStringLiteral("套用設定"));
    connect(applyBtn, &QPushButton::clicked, this, [this, ch] { sendSetpointCmd(ch); });
    grid->addWidget(applyBtn, 2, 0, 1, 2);

    auto* onBtn  = new QPushButton(QStringLiteral("⚡ ON"));
    auto* offBtn = new QPushButton(QStringLiteral("OFF"));
    onBtn->setStyleSheet(QStringLiteral("background:#f1c40f;"));
    connect(onBtn,  &QPushButton::clicked, this, [this, ch] { sendOutputCmd(ch, true); });
    connect(offBtn, &QPushButton::clicked, this, [this, ch] { sendOutputCmd(ch, false); });
    grid->addWidget(onBtn, 3, 0);
    grid->addWidget(offBtn, 3, 1);

    auto* readout = new QLabel(QStringLiteral("--.--- V"));
    readout->setStyleSheet(QStringLiteral(
        "font-family:Consolas;font-size:12pt;font-weight:bold;color:#2980b9;"));
    readout->setMinimumHeight(48);
    m_readout[static_cast<size_t>(idx)] = readout;
    grid->addWidget(readout, 4, 0, 1, 2);

    return box;
}

// -------------------------------------------------------------- 發佈命令

void GuiWindow::sendOutputCmd(int ch, bool on)
{
    const QJsonObject cmd{{QStringLiteral("ch"), ch}, {QStringLiteral("on"), on}};
    m_pubOutput->send(QJsonDocument(cmd).toJson(QJsonDocument::Compact));
}

void GuiWindow::sendSetpointCmd(int ch)
{
    QJsonObject cmd{{QStringLiteral("ch"), ch}};
    if (ch == 3) {
        cmd.insert(QStringLiteral("voltage"), m_ch3Combo->currentText().toDouble());
    } else {
        const auto idx = static_cast<size_t>(ch - 1);
        cmd.insert(QStringLiteral("voltage"), m_vSpin[idx]->value());
        cmd.insert(QStringLiteral("current"), m_iSpin[idx]->value());
    }
    m_pubSetpoint->send(QJsonDocument(cmd).toJson(QJsonDocument::Compact));
}

void GuiWindow::sendConfigCmd()
{
    const QJsonObject cmd{{QStringLiteral("rate_hz"), m_rateSpin->value()}};
    m_pubConfig->send(QJsonDocument(cmd).toJson(QJsonDocument::Compact));
}

// -------------------------------------------------------------- 接收更新

void GuiWindow::onMeas(const QByteArray& payload)
{
    const QJsonObject frame = QJsonDocument::fromJson(payload).object();
    const QJsonArray channels = frame.value(QStringLiteral("channels")).toArray();
    for (const auto& entry : channels) {
        const QJsonObject obj = entry.toObject();
        const int ch = obj.value(QStringLiteral("ch")).toInt(-1);
        if (ch < 1 || ch > 3) {
            continue;
        }
        const double v = obj.value(QStringLiteral("voltage")).toDouble();
        QString text = QStringLiteral("%1 V").arg(v, 0, 'f', 3);
        if (obj.contains(QStringLiteral("current"))) {
            text += QStringLiteral("\n%1 A\n%2 W")
                        .arg(obj.value(QStringLiteral("current")).toDouble(), 0, 'f', 4)
                        .arg(obj.value(QStringLiteral("power")).toDouble(), 0, 'f', 3);
        }
        m_readout[static_cast<size_t>(ch - 1)]->setText(text);
    }
}

void GuiWindow::onStatus(const QByteArray& payload)
{
    const QJsonObject st = QJsonDocument::fromJson(payload).object();
    const bool connected  = st.value(QStringLiteral("connected")).toBool();
    const QString error   = st.value(QStringLiteral("error")).toString();
    QString text = QStringLiteral("[%1] %2  port=%3  rate=%4Hz  %5")
                       .arg(QDateTime::currentDateTime().toString(QStringLiteral("HH:mm:ss")),
                            connected ? QStringLiteral("CONNECTED") : QStringLiteral("DISCONNECTED"),
                            st.value(QStringLiteral("port")).toString(),
                            QString::number(st.value(QStringLiteral("rate_hz")).toDouble()),
                            st.value(QStringLiteral("idn")).toString());
    if (st.value(QStringLiteral("sim")).toBool()) {
        text += QStringLiteral("  [SIM]");
    }
    if (!error.isEmpty()) {
        text += QStringLiteral("\nERROR: ") + error;
    }
    m_statusLabel->setStyleSheet(
        connected
            ? QStringLiteral("background:#202020;color:#00ff00;font-family:Consolas;padding:6px;")
            : QStringLiteral("background:#202020;color:#ff5555;font-family:Consolas;padding:6px;"));
    m_statusLabel->setText(text);
}
