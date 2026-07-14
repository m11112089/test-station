//
// gpp3650_gui — eCAL 驗證平台 (獨立 process, 只透過 eCAL 與 node 溝通)
//
// 用法 (--port 指定要對應的 node, 與 node 的 --port 相同即可):
//   gpp3650_gui                        # 對應模擬 node (gpp3650/SIM)
//   gpp3650_gui --port COM4            # 對應 gpp3650_node --port COM4
//   gpp3650_gui --port /dev/ttyUSB0    # Ubuntu
//
#include "gui/GuiWindow.hpp"
#include "common/EcalWrap.hpp"
#include "common/Topics.hpp"

#include <QApplication>
#include <QCommandLineParser>
#include <QMessageBox>

int main(int argc, char* argv[])
{
    QApplication app(argc, argv);
    QApplication::setApplicationName(QStringLiteral("gpp3650_gui"));

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("GPP-3650 eCAL validation GUI"));
    parser.addHelpOption();
    parser.addOption({QStringLiteral("port"),
                      QStringLiteral("Serial port of the target node (e.g. COM4, /dev/ttyUSB0); omit for SIM"),
                      QStringLiteral("port")});
    parser.process(app);

    // 與 node 相同規則推導 topic 前綴
    const QString deviceName =
        topics::devicePrefix(QStringLiteral("gpp3650"), parser.value(QStringLiteral("port")));
    qInfo().noquote() << "raw port arg =" << parser.value(QStringLiteral("port"))
                      << ", topic prefix =" << deviceName;
    const std::string unitName =
        (QString(deviceName).replace(QLatin1Char('/'), QLatin1Char('_'))
         + QStringLiteral("_gui")).toStdString();
    if (!ecalwrap::initialize(unitName)) {
        QMessageBox::critical(nullptr, QStringLiteral("gpp3650_gui"),
                              QStringLiteral("eCAL initialize failed"));
        return 1;
    }

    GuiWindow window(deviceName);
    window.resize(900, 420);
    window.show();

    QObject::connect(&app, &QApplication::aboutToQuit, [] { ecalwrap::finalize(); });
    return app.exec();
}
