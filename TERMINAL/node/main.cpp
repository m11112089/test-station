//
// terminal_node — 本機 terminal (平台 shell) <-> eCAL 橋接 node
// (headless, 獨立 process; shell 跑在偽終端上, 詳見 TerminalNode.hpp / PtyProcess.hpp)
//
// node 就是「一個掛在 eCAL 上的 shell」: 要跑 iperf 等第三方工具,
// 對 tx 送指令行即可 (跟真人打字一樣), 不需要任何執行參數。
//
// 用法:
//   terminal_node              # Windows=powershell / Linux=bash, topic 前綴 terminal/shell
//   terminal_node --id B       # 第二個實例, topic 前綴 terminal/B
//
#include "node/TerminalNode.hpp"
#include "common/EcalWrap.hpp"
#include "common/Topics.hpp"

#include <QCommandLineParser>
#include <QCoreApplication>
#include <QTextStream>

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("terminal_node"));

    QCommandLineParser parser;
    parser.setApplicationDescription(
        QStringLiteral("Local shell terminal <-> eCAL bridge node"));
    parser.addHelpOption();
    parser.addOption({QStringLiteral("id"),
                      QStringLiteral("Topic suffix, only needed to tell multiple "
                                     "instances apart (default \"shell\")"),
                      QStringLiteral("id"), QStringLiteral("shell")});
    parser.process(app);

    TerminalNode::Options opt;
    opt.prefix = topics::terminalPrefix(parser.value(QStringLiteral("id")).trimmed());
#ifdef _WIN32
    // powershell 而非 cmd: ls/pwd/cat 等 Unix 習慣指令原生可用 (alias),
    // 測試腳本跨平台寫起來較一致 (使用者要求)
    opt.command = QStringLiteral("powershell");
#else
    opt.command = QStringLiteral("bash");
#endif

    const std::string unitName =
        (QString(opt.prefix).replace(QLatin1Char('/'), QLatin1Char('_'))
         + QStringLiteral("_node")).toStdString();
    if (!ecalwrap::initialize(unitName)) {
        QTextStream(stderr) << "error: eCAL initialize failed\n";
        return 1;
    }

    TerminalNode node(opt);
    node.start();

    QObject::connect(&app, &QCoreApplication::aboutToQuit, [] { ecalwrap::finalize(); });
    return app.exec();
}
