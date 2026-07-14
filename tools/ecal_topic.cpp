//
// ecal_topic — 平台通用的 topic 除錯 / 驗證工具 (類似 rostopic echo/pub)
//
// 用法:
//   ecal_topic echo <topic> [--count N] [--timeout ms]
//       訂閱 topic, 將每筆 payload 印到 stdout (一行一筆);
//       收滿 N 筆 (預設 3) 或逾時 (預設 10000ms) 即結束。
//       exit code: 0 = 有收到至少 count 筆, 1 = 逾時。
//
//   ecal_topic pub <topic> <payload> [--delay ms]
//       發佈一筆 payload (預設等待 --delay 2000ms 讓 eCAL 完成 pub/sub 配對)。
//
#include "common/EcalWrap.hpp"

#include <QCoreApplication>
#include <QCommandLineParser>
#include <QTextStream>
#include <QTimer>

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("ecal_topic"));

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("eCAL topic echo/pub debug tool"));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("mode"), QStringLiteral("echo | pub"));
    parser.addPositionalArgument(QStringLiteral("topic"), QStringLiteral("topic name"));
    parser.addPositionalArgument(QStringLiteral("payload"), QStringLiteral("payload (pub mode)"));
    parser.addOption({QStringLiteral("count"),
                      QStringLiteral("echo: stop after N messages (default 3)"),
                      QStringLiteral("n"), QStringLiteral("3")});
    parser.addOption({QStringLiteral("timeout"),
                      QStringLiteral("echo: give up after ms (default 10000)"),
                      QStringLiteral("ms"), QStringLiteral("10000")});
    parser.addOption({QStringLiteral("delay"),
                      QStringLiteral("pub: wait ms for pub/sub matching before send (default 2000)"),
                      QStringLiteral("ms"), QStringLiteral("2000")});
    parser.process(app);

    const QStringList args = parser.positionalArguments();
    if (args.size() < 2) {
        parser.showHelp(1);
    }
    const QString mode  = args.at(0);
    const QString topic = args.at(1);

    QTextStream out(stdout);

    if (!ecalwrap::initialize("ecal_topic")) {
        QTextStream(stderr) << "error: eCAL initialize failed\n";
        return 1;
    }
    QObject::connect(&app, &QCoreApplication::aboutToQuit, [] { ecalwrap::finalize(); });

    if (mode == QStringLiteral("echo")) {
        const int wantCount = parser.value(QStringLiteral("count")).toInt();
        const int timeoutMs = parser.value(QStringLiteral("timeout")).toInt();

        auto* received = new int(0);
        // callback 在 eCAL 執行緒: 用 invokeMethod queue 回主執行緒再輸出
        auto sub = std::make_unique<ecalwrap::Subscriber>(topic, [&app, &out, received, wantCount](QByteArray payload) {
            QMetaObject::invokeMethod(&app, [&app, &out, received, wantCount, payload] {
                out << payload << "\n";
                out.flush();
                if (++(*received) >= wantCount) {
                    app.exit(0);
                }
            }, Qt::QueuedConnection);
        });

        QTimer::singleShot(timeoutMs, &app, [&app, received, wantCount] {
            app.exit(*received >= wantCount ? 0 : 1);
        });
        return app.exec();
    }

    if (mode == QStringLiteral("pub")) {
        if (args.size() < 3) {
            QTextStream(stderr) << "error: pub mode requires <payload>\n";
            return 1;
        }
        const QByteArray payload = args.at(2).toUtf8();
        const int delayMs = parser.value(QStringLiteral("delay")).toInt();

        auto pub = std::make_unique<ecalwrap::Publisher>(topic);
        QTimer::singleShot(delayMs, &app, [&app, &pub, payload, &out, topic] {
            const bool ok = pub->send(payload);
            out << (ok ? QStringLiteral("sent to %1\n").arg(topic)
                       : QStringLiteral("send failed\n"));
            out.flush();
            // 給 eCAL 一點時間把資料送出去再收攤
            QTimer::singleShot(300, &app, [&app] { app.exit(0); });
        });
        return app.exec();
    }

    QTextStream(stderr) << "error: unknown mode '" << mode << "' (echo|pub)\n";
    return 1;
}
