//
// gpp3650_node — GPP-3650 device driver node (headless, 獨立 process)
//
// 用法:
//   gpp3650_node --port COM3                # Windows,  topic 前綴 gpp3650/COM3
//   gpp3650_node --port /dev/ttyUSB0        # Ubuntu,   topic 前綴 gpp3650/ttyUSB0
//   gpp3650_node --sim                      # 模擬模式, topic 前綴 gpp3650/SIM
//   gpp3650_node --list-ports               # 列出可用串口
//
// 多台同型裝置: 各自指定不同 --port 即可, topic 前綴自動以 port 區分。
//
#include "node/NodeApp.hpp"
#include "common/EcalWrap.hpp"
#include "common/Topics.hpp"

#include <QCommandLineParser>
#include <QCoreApplication>
#include <QSerialPortInfo>
#include <QTextStream>

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("gpp3650_node"));

    QCommandLineParser parser;
    parser.setApplicationDescription(
        QStringLiteral("GW Instek GPP-3650 power supply eCAL driver node"));
    parser.addHelpOption();
    parser.addOption({QStringLiteral("port"),
                      QStringLiteral("Serial port (e.g. COM3, /dev/ttyUSB0)"),
                      QStringLiteral("port")});
    parser.addOption({QStringLiteral("baud"),
                      QStringLiteral("Baud rate (default 115200)"),
                      QStringLiteral("baud"), QStringLiteral("115200")});
    parser.addOption({QStringLiteral("rate"),
                      QStringLiteral("Initial measurement publish rate in Hz (default 1)"),
                      QStringLiteral("hz"), QStringLiteral("1.0")});
    parser.addOption({QStringLiteral("sim"),
                      QStringLiteral("Simulation mode, no hardware required")});
    parser.addOption({QStringLiteral("list-ports"),
                      QStringLiteral("List available serial ports and exit")});
    parser.process(app);

    if (parser.isSet(QStringLiteral("list-ports"))) {
        QTextStream out(stdout);
        const auto ports = QSerialPortInfo::availablePorts();
        for (const auto& info : ports) {
            out << info.portName() << "\t" << info.description() << "\n";
        }
        return 0;
    }

    NodeApp::Options opt;
    opt.port   = parser.value(QStringLiteral("port"));
    opt.baud   = parser.value(QStringLiteral("baud")).toInt();
    opt.rateHz = parser.value(QStringLiteral("rate")).toDouble();
    opt.sim    = parser.isSet(QStringLiteral("sim"));

    if (opt.port.isEmpty() && !opt.sim) {
        QTextStream(stderr) << "error: --port is required (or use --sim)\n";
        return 1;
    }

    // topic 前綴由 port 推導: gpp3650/COM4, gpp3650/ttyUSB0, gpp3650/SIM
    opt.deviceName = topics::devicePrefix(QStringLiteral("gpp3650"), opt.sim ? QString() : opt.port);

    // eCAL unit name 不用 '/', 換成 '_'
    const std::string unitName =
        (QString(opt.deviceName).replace(QLatin1Char('/'), QLatin1Char('_'))
         + QStringLiteral("_node")).toStdString();
    if (!ecalwrap::initialize(unitName)) {
        QTextStream(stderr) << "error: eCAL initialize failed\n";
        return 1;
    }

    NodeApp node(opt);
    node.start();

    QObject::connect(&app, &QCoreApplication::aboutToQuit, [] { ecalwrap::finalize(); });
    return app.exec();
}
