//
// terminal_term — 互動終端 client, 只透過 eCAL 與 terminal_node 溝通
//
//   鍵盤輸入 -> 發佈 terminal/<id>/tx  (由 terminal_node 寫入偽終端 = 對它打字)
//   訂閱 terminal/<id>/rx -> 直接印出  (回顯/prompt/顏色由遠端 pty 提供)
//
// 遠端是偽終端 (ConPTY/forkpty), 所以體驗與本機 terminal 相同:
// 方向鍵/歷史/Tab 補完可用 (擴充鍵轉成 VT 序列送出), Ctrl+C 會轉送給
// 遠端程式 (中斷它, 不會結束 term), Esc 也原樣送出。
//
// 用法 (--id 與 terminal_node 相同即可配對; 兩邊都不帶參數 = terminal/shell):
//   terminal_term
//   terminal_term --id B
//
// 離開: Ctrl+Q 或 Ctrl+]  (Esc/Ctrl+C 都會送給遠端;
//       Ctrl+] 在中文輸入法模式下會被組字成全形 】, 此時用 Ctrl+Q)
//
#include "common/EcalWrap.hpp"
#include "common/Topics.hpp"

#include <QCommandLineParser>
#include <QCoreApplication>
#include <QTextStream>

#include <atomic>
#include <csignal>
#include <cstdio>
#include <memory>

#ifdef _WIN32
#include <conio.h>
#include <windows.h>
#else
#include <termios.h>
#include <unistd.h>
#include <sys/select.h>
#endif

namespace {

std::atomic<bool> g_running{true};
void onSignal(int /*sig*/) { g_running = false; }

#ifdef _WIN32
// Ctrl+C 不結束 term, 改轉送 0x03 給遠端程式 (由主迴圈送出)
std::atomic<bool> g_ctrlC{false};
BOOL WINAPI onConsoleCtrl(DWORD type)
{
    if (type == CTRL_C_EVENT) {
        g_ctrlC = true;
        return TRUE;
    }
    return FALSE;
}

// conio 擴充鍵 (0x00/0xE0 前綴) -> VT 序列 (ConPTY / pty 的輸入格式)
QByteArray vtFromExtKey(int code)
{
    switch (code) {
    case 72: return QByteArrayLiteral("\x1b[A");  // Up
    case 80: return QByteArrayLiteral("\x1b[B");  // Down
    case 77: return QByteArrayLiteral("\x1b[C");  // Right
    case 75: return QByteArrayLiteral("\x1b[D");  // Left
    case 71: return QByteArrayLiteral("\x1b[H");  // Home
    case 79: return QByteArrayLiteral("\x1b[F");  // End
    case 82: return QByteArrayLiteral("\x1b[2~"); // Insert
    case 83: return QByteArrayLiteral("\x1b[3~"); // Delete
    case 73: return QByteArrayLiteral("\x1b[5~"); // PgUp
    case 81: return QByteArrayLiteral("\x1b[6~"); // PgDn
    default: return {};
    }
}
#endif

// ------------------------------------------------------------ raw console
// 跨平台單鍵讀取: Windows 用 conio, POSIX 用 termios raw mode。
class RawConsole {
public:
    RawConsole()
    {
#ifdef _WIN32
        SetConsoleOutputCP(CP_UTF8); // ConPTY 的輸出串流是 UTF-8
        SetConsoleCP(CP_UTF8);
        // 讓本機 console 渲染遠端 pty 傳來的 VT 序列 (顏色/游標移動)
        const HANDLE hOut = GetStdHandle(STD_OUTPUT_HANDLE);
        DWORD mode = 0;
        if (GetConsoleMode(hOut, &mode)) {
            SetConsoleMode(hOut, mode | ENABLE_VIRTUAL_TERMINAL_PROCESSING);
        }
#else
        if (isatty(STDIN_FILENO)) {
            tcgetattr(STDIN_FILENO, &m_saved);
            termios raw = m_saved;
            // ISIG/IXON/ICRNL 也關掉: Ctrl+C/Ctrl+Z/Ctrl+S 與 Enter 的 CR
            // 都原樣入列, 轉送給遠端 pty 處理 (跟真 terminal client 一樣)
            raw.c_lflag &= ~static_cast<tcflag_t>(ICANON | ECHO | ISIG | IEXTEN);
            raw.c_iflag &= ~static_cast<tcflag_t>(ICRNL | IXON);
            raw.c_cc[VMIN]  = 0;
            raw.c_cc[VTIME] = 0;
            tcsetattr(STDIN_FILENO, TCSANOW, &raw);
            m_restored = false;
        }
#endif
    }
    ~RawConsole()
    {
#ifndef _WIN32
        if (!m_restored) {
            tcsetattr(STDIN_FILENO, TCSANOW, &m_saved);
        }
#endif
    }

    // 讀一個鍵; 無輸入時等待至多 timeoutMs 後回 -1
    int getKey(int timeoutMs)
    {
#ifdef _WIN32
        if (_kbhit()) {
            return _getch();
        }
        Sleep(static_cast<DWORD>(timeoutMs));
        return _kbhit() ? _getch() : -1;
#else
        fd_set fds;
        FD_ZERO(&fds);
        FD_SET(STDIN_FILENO, &fds);
        timeval tv{0, timeoutMs * 1000};
        if (select(STDIN_FILENO + 1, &fds, nullptr, nullptr, &tv) > 0) {
            unsigned char c = 0;
            if (read(STDIN_FILENO, &c, 1) == 1) {
                return c;
            }
        }
        return -1;
#endif
    }

private:
#ifndef _WIN32
    termios m_saved{};
    bool    m_restored = true;
#endif
};

} // namespace

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("terminal_term"));

    QCommandLineParser parser;
    parser.setApplicationDescription(
        QStringLiteral("Interactive terminal over eCAL (pairs with terminal_node)"));
    parser.addHelpOption();
    parser.addOption({QStringLiteral("id"),
                      QStringLiteral("Topic suffix of the target terminal_node "
                                     "(default \"shell\")"),
                      QStringLiteral("id"), QStringLiteral("shell")});
    parser.process(app);

    const QString prefix = topics::terminalPrefix(parser.value(QStringLiteral("id")).trimmed());
    const QByteArray newline("\r"); // 真 terminal 的 Enter = CR, 由遠端 pty 翻譯

    const std::string unitName =
        (QString(prefix).replace(QLatin1Char('/'), QLatin1Char('_'))
         + QStringLiteral("_term")).toStdString();
    if (!ecalwrap::initialize(unitName)) {
        std::fprintf(stderr, "error: eCAL initialize failed\n");
        return 1;
    }

    RawConsole console;

    ecalwrap::Publisher pubTx(topics::rawTx(prefix));
    // rx callback 在 eCAL 執行緒直接寫 stdout (原始位元組, 不經 Qt 事件迴圈)
    ecalwrap::Subscriber subRx(topics::rawRx(prefix), [](QByteArray data) {
        std::fwrite(data.constData(), 1, static_cast<size_t>(data.size()), stdout);
        std::fflush(stdout);
    });

    std::printf("terminal_term: topic prefix \"%s\" | Ctrl+Q 或 Ctrl+] 離開 "
                "(Esc/Ctrl+C 會送給遠端)\n",
                prefix.toUtf8().constData());

    // 進入 alternate screen buffer 並清空: ConPTY/pty 的輸出用「絕對座標」
    // 重繪 (ESC[7;1H 等), 直接倒進使用者原本的畫面會蓋在舊內容上、看起來
    // 像輸出消失。乾淨畫布讓座標系與遠端 pty 一致 (telnet/ssh 同法),
    // 離開時還原原畫面。剛接上時遠端不會重繪歷史 —— 按一下 Enter 拿新 prompt。
    std::printf("\x1b[?1049h\x1b[2J\x1b[H");
    std::fflush(stdout);

#ifdef _WIN32
    SetConsoleCtrlHandler(onConsoleCtrl, TRUE); // Ctrl+C -> 轉送遠端
#else
    std::signal(SIGINT, onSignal); // 鍵盤 Ctrl+C 已被 raw mode 攔下, 這裡只擋 kill -INT
#endif

    const auto appendKey = [&newline](QByteArray& out, int key) {
        if (key == '\r' || key == '\n') {
            out += newline;
        } else {
            out.append(static_cast<char>(key));
        }
    };

    while (g_running && ecalwrap::ok()) {
#ifdef _WIN32
        if (g_ctrlC.exchange(false)) {
            pubTx.send(QByteArrayLiteral("\x03")); // Ctrl+C -> 遠端 pty 轉成中斷
        }
#endif
        int key = console.getKey(10);
        if (key < 0) {
            continue;
        }
        // 把「此刻已到達」的按鍵合併成單一訊息再送 (貼上/管線輸入時
        // 不會變成一鍵一訊息 —— 也降低 eCAL burst 掉訊息的機率)
        QByteArray out;
        while (key >= 0) {
            if (key == 29 || key == 17) { // Ctrl+] / Ctrl+Q 離開; 其餘 (含 Esc) 原樣送遠端
                // Ctrl+Q 是給中文輸入法用的: IME 中文模式下 ] 會被組字成全形 】,
                // 產生不了控制字元 29; Ctrl+字母 組合不經 IME, 永遠可靠
                g_running = false;
                break;
            }
#ifdef _WIN32
            if (key == 0 || key == 0xE0) { // conio 擴充鍵: 下一個 byte 是鍵碼
                out += vtFromExtKey(console.getKey(0));
            } else {
                appendKey(out, key);
            }
#else
            appendKey(out, key);
#endif
            if (out.size() >= 256) {
                break;
            }
            key = console.getKey(0);
        }
        if (!out.isEmpty()) {
            pubTx.send(out); // 回顯由遠端 pty 送回 rx, 不做本地 echo
        }
    }

    std::printf("\x1b[?1049l"); // 離開 alternate screen, 還原使用者原本的畫面
    std::printf("bye\n");
    ecalwrap::finalize();
    return 0;
}
