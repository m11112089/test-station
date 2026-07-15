#pragma once
//
// PtyProcess — 跨平台「偽終端 + 子程序」包裝 (Windows ConPTY / POSIX forkpty)
//
// 為什麼不用 QProcess(pipe): pipe 下子程序偵測不到 terminal,
// 沒有回顯、沒有 prompt、行編輯/方向鍵/Ctrl+C/顏色都不工作。
// 偽終端讓子程序以為自己接著真的 console —— 使用者體驗與本機 terminal 相同,
// 對橋接層而言仍是透明的雙向 byte 串流 (VT escape 序列原樣通過)。
//
// 附帶好處:
//   - stdout 走 terminal → 行緩衝, iperf 等工具即時輸出 (pipe 下會整塊緩衝)
//   - Windows ConPTY 的輸出串流是 UTF-8 (pipe 下 cmd.exe 是 OEM CP950)
//
// 執行緒模型: Windows 用背景執行緒 blocking read, 以 queued signal 回主執行緒;
// POSIX 用 QSocketNotifier 事件驅動。所有 signal 都在主執行緒收到。
//
#include <QByteArray>
#include <QObject>
#include <QString>

#ifdef _WIN32
#include <windows.h>
#include <string>
#include <thread>
#else
#include <sys/types.h>
class QSocketNotifier;
#endif

class PtyProcess : public QObject {
    Q_OBJECT
public:
    explicit PtyProcess(QObject* parent = nullptr);
    ~PtyProcess() override;

    // 啟動 command (完整指令列, Windows 交給 CreateProcess 原生解析,
    // POSIX 交給 /bin/sh -c)。失敗回 false 並設定 errorString。
    bool start(const QString& command, int cols, int rows);

    bool    isRunning() const { return m_running; }
    qint64  pid() const       { return m_pid; }
    QString errorString() const { return m_error; }

    // 寫入子程序的 terminal 輸入 (等同使用者對著它打字)
    void write(const QByteArray& data);

    // 強制終止子程序 (kill -9 / TerminateProcess); finished() 仍會發出
    void kill();

signals:
    void output(QByteArray data);          // 子程序 terminal 輸出 (含 VT 序列)
    void finished(int exitCode, bool crashed); // 子程序結束 (POSIX: 被 signal 殺 = crashed)

private:
    void cleanup(); // 關閉 pty / handle, 不觸發 signal

    bool    m_running = false;
    qint64  m_pid = 0;
    QString m_error;

#ifdef _WIN32
    // HPCON: MinGW 13.1 標頭未宣告 ConPTY 型別, 以 void* 持有 (見 .cpp 的動態綁定)
    void*               m_hPC = nullptr;
    HANDLE              m_inWrite = nullptr;  // 我們 -> 子程序 terminal 輸入
    HANDLE              m_outRead = nullptr;  // 子程序 terminal 輸出 -> 我們
    PROCESS_INFORMATION m_pi{};
    std::thread         m_reader;
    std::thread         m_watcher; // 子程序結束時關 pty, 解除 reader 的 ReadFile
#else
    void onMasterReadable();

    int              m_masterFd = -1;
    QSocketNotifier* m_notifier = nullptr;
#endif
};
