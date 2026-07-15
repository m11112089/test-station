#pragma once
//
// terminal_node — 本機 terminal (平台 shell) <-> eCAL 橋接
//
//   訂閱 terminal/<id>/tx     : payload 原封不動寫入 terminal 輸入 (= 對它打字)
//   terminal 輸出 (含 VT 序列): 原封不動發佈到 terminal/<id>/rx
//   terminal/<id>/status      : 執行狀態 / pid / exit code / tx-rx 計數 (JSON, 2s 心跳)
//
// shell 跑在「偽終端」上 (Windows ConPTY / POSIX forkpty, 見 PtyProcess.hpp):
// 這是一條持續的互動 session, 使用者透過 terminal_term 操作起來
// 與本機 terminal 相同 —— 回顯、prompt、行編輯、Ctrl+C、顏色都自然運作。
// 對橋接層而言仍是透明 byte 串流, 與串口橋接 (uart) 同一套模型。
//
// 測試流程要呼叫第三方工具 (iperf 等) 就對 tx 送指令行, 要 exit code 就
// 送 `echo %errorlevel%` / `echo $?` —— node 本身沒有執行語意, 只是 shell。
//
// shell 結束 (使用者打 exit / 崩潰): 3 秒後重啟, 同串口斷線重連語意。
//
#include "common/EcalWrap.hpp"
#include "node/PtyProcess.hpp"

#include <QObject>
#include <QString>
#include <QTimer>

#include <memory>

class TerminalNode : public QObject {
    Q_OBJECT
public:
    struct Options {
        QString prefix;  // topic 前綴, 由 main 依 --id 推導 (預設 terminal/shell)
        QString command; // 平台 shell, 由 main 決定 (cmd / bash)
    };

    explicit TerminalNode(Options opt, QObject* parent = nullptr);

    bool start();

signals:
    void txReceived(QByteArray payload); // 由 eCAL 執行緒 emit, 自動 queue 回主執行緒

private slots:
    void handleTx(const QByteArray& payload);
    void onOutput(const QByteArray& data);
    void onFinished(int exitCode, bool crashed);
    void tryStart();

private:
    void publishStatus(const QString& error = QString());

    Options    m_opt;
    PtyProcess m_pty;

    std::unique_ptr<ecalwrap::Publisher>  m_pubRx;
    std::unique_ptr<ecalwrap::Publisher>  m_pubStatus;
    std::unique_ptr<ecalwrap::Subscriber> m_subTx;

    QTimer  m_respawnTimer;
    QTimer  m_statusTimer;
    quint64 m_txBytes = 0;
    quint64 m_rxBytes = 0;
    int     m_lastExitCode = 0;
    bool    m_hasExited = false;
};
