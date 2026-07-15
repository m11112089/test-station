#include "node/PtyProcess.hpp"

#include <QMetaObject>

#ifdef _WIN32

// ---------------------------------------------------------------- Windows
// ConPTY (Windows 10 1809+): CreatePseudoConsole + CreateProcess 掛上
// PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE。子程序結束後 conhost 會關閉輸出管線,
// reader 執行緒的 ReadFile 因此解除阻塞 —— kill() 時則靠 ClosePseudoConsole
// 主動讓它解除 (ConPTY 已知行為: 不關 pty, ReadFile 會永遠卡住)。
//
// MinGW 13.1 的 Win32 標頭尚未宣告 ConPTY API (函式本身一直都在 kernel32),
// 這裡自行宣告並於執行期 GetProcAddress 綁定 —— 不引入任何額外相依。

#ifndef PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE
#define PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE 0x00020016
#endif

namespace {

using CreatePseudoConsoleFn = HRESULT(WINAPI*)(COORD, HANDLE, HANDLE, DWORD, void**);
using ClosePseudoConsoleFn  = void(WINAPI*)(void*);

CreatePseudoConsoleFn createPseudoConsole()
{
    static const auto fn = reinterpret_cast<CreatePseudoConsoleFn>(
        reinterpret_cast<void*>(GetProcAddress(GetModuleHandleW(L"kernel32.dll"),
                                               "CreatePseudoConsole")));
    return fn;
}

ClosePseudoConsoleFn closePseudoConsole()
{
    static const auto fn = reinterpret_cast<ClosePseudoConsoleFn>(
        reinterpret_cast<void*>(GetProcAddress(GetModuleHandleW(L"kernel32.dll"),
                                               "ClosePseudoConsole")));
    return fn;
}

} // namespace

PtyProcess::PtyProcess(QObject* parent)
    : QObject(parent)
{
}

PtyProcess::~PtyProcess()
{
    disconnect(); // 收尾期間不再發 output/finished
    if (m_running) {
        TerminateProcess(m_pi.hProcess, 1);
        if (m_hPC) {
            closePseudoConsole()(m_hPC);
            m_hPC = nullptr;
        }
    }
    if (m_reader.joinable()) {
        m_reader.join();
    }
    if (m_watcher.joinable()) {
        m_watcher.join();
    }
    cleanup();
}

bool PtyProcess::start(const QString& command, int cols, int rows)
{
    if (m_running) {
        return false;
    }
    m_error.clear();

    if (!createPseudoConsole() || !closePseudoConsole()) {
        m_error = QStringLiteral("ConPTY not available (needs Windows 10 1809+)");
        return false;
    }

    HANDLE inRead = nullptr, outWrite = nullptr;
    if (!CreatePipe(&inRead, &m_inWrite, nullptr, 0) ||
        !CreatePipe(&m_outRead, &outWrite, nullptr, 0)) {
        m_error = QStringLiteral("CreatePipe failed (%1)").arg(GetLastError());
        return false;
    }

    COORD size;
    size.X = static_cast<SHORT>(cols);
    size.Y = static_cast<SHORT>(rows);
    const HRESULT hr = createPseudoConsole()(size, inRead, outWrite, 0, &m_hPC);
    // ConPTY 已複製這兩個 handle; 我方複本必須關閉, 子程序結束時
    // 輸出管線才會真正 EOF
    CloseHandle(inRead);
    CloseHandle(outWrite);
    if (FAILED(hr)) {
        m_error = QStringLiteral("CreatePseudoConsole failed (0x%1)").arg(quint32(hr), 0, 16);
        cleanup();
        return false;
    }

    SIZE_T bytes = 0;
    InitializeProcThreadAttributeList(nullptr, 1, 0, &bytes);
    auto* attrList =
        reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(HeapAlloc(GetProcessHeap(), 0, bytes));
    if (!attrList || !InitializeProcThreadAttributeList(attrList, 1, 0, &bytes) ||
        !UpdateProcThreadAttribute(attrList, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                                   m_hPC, sizeof(m_hPC), nullptr, nullptr)) {
        m_error = QStringLiteral("ProcThreadAttribute setup failed (%1)").arg(GetLastError());
        if (attrList) {
            HeapFree(GetProcessHeap(), 0, attrList);
        }
        cleanup();
        return false;
    }

    STARTUPINFOEXW si{};
    si.StartupInfo.cb = sizeof(si);
    si.lpAttributeList = attrList;
    // ConPTY 陷阱: 若本 process 的標準握把被重導成 pipe (被 supervisor/腳本
    // 啟動時常見), CreateProcess 的 legacy 行為會把它們複製給子程序、蓋掉
    // pseudoconsole 的握把 —— 子程序輸出就流回我們的 stdout 而不是 pty。
    // 比照 Windows Terminal ConptyConnection: USESTDHANDLES + null 阻止複製,
    // 子程序改從自己的 console (= pty) 取得標準握把。
    si.StartupInfo.dwFlags    = STARTF_USESTDHANDLES;
    si.StartupInfo.hStdInput  = nullptr;
    si.StartupInfo.hStdOutput = nullptr;
    si.StartupInfo.hStdError  = nullptr;

    std::wstring cmdLine = command.toStdWString(); // CreateProcessW 需要可寫緩衝
    PROCESS_INFORMATION pi{};
    const BOOL ok = CreateProcessW(nullptr, cmdLine.data(), nullptr, nullptr, FALSE,
                                   EXTENDED_STARTUPINFO_PRESENT, nullptr, nullptr,
                                   &si.StartupInfo, &pi);
    DeleteProcThreadAttributeList(attrList);
    HeapFree(GetProcessHeap(), 0, attrList);

    if (!ok) {
        m_error = QStringLiteral("CreateProcess failed (%1)").arg(GetLastError());
        cleanup();
        return false;
    }

    m_pi      = pi;
    m_pid     = pi.dwProcessId;
    m_running = true;

    m_reader = std::thread([this] {
        char  buf[4096];
        DWORD n = 0;
        for (;;) {
            if (!ReadFile(m_outRead, buf, sizeof(buf), &n, nullptr) || n == 0) {
                break; // EOF / broken pipe = 子程序結束 (或 kill 關閉了 pty)
            }
            emit output(QByteArray(buf, static_cast<int>(n))); // queued 回主執行緒
        }
        WaitForSingleObject(m_pi.hProcess, 5000);
        DWORD code = 1;
        GetExitCodeProcess(m_pi.hProcess, &code);
        QMetaObject::invokeMethod(
            this,
            [this, code] {
                if (m_reader.joinable()) {
                    m_reader.join(); // 兩個執行緒都已在收尾, 只是收乾淨 thread 物件
                }
                if (m_watcher.joinable()) {
                    m_watcher.join();
                }
                cleanup();
                emit finished(static_cast<int>(code), false);
            },
            Qt::QueuedConnection);
    });

    // 子程序結束後 conhost 不會自己關輸出管線 (reader 的 ReadFile 會永遠卡住):
    // watcher 等 process handle signaled 後回主執行緒關 pty, 讓 reader EOF。
    m_watcher = std::thread([this] {
        WaitForSingleObject(m_pi.hProcess, INFINITE);
        Sleep(200); // 給 conhost 一點時間把最後的輸出翻譯寫進管線
        QMetaObject::invokeMethod(
            this,
            [this] {
                if (m_hPC) {
                    closePseudoConsole()(m_hPC);
                    m_hPC = nullptr;
                }
            },
            Qt::QueuedConnection);
    });
    return true;
}

void PtyProcess::write(const QByteArray& data)
{
    if (!m_running || data.isEmpty()) {
        return;
    }
    DWORD written = 0;
    WriteFile(m_inWrite, data.constData(), static_cast<DWORD>(data.size()), &written, nullptr);
}

void PtyProcess::kill()
{
    if (!m_running) {
        return;
    }
    TerminateProcess(m_pi.hProcess, 1);
    if (m_hPC) { // 解除 reader 的 ReadFile 阻塞; finished 仍由 reader 路徑發出
        closePseudoConsole()(m_hPC);
        m_hPC = nullptr;
    }
}

void PtyProcess::cleanup()
{
    if (m_hPC) {
        closePseudoConsole()(m_hPC);
        m_hPC = nullptr;
    }
    if (m_inWrite) {
        CloseHandle(m_inWrite);
        m_inWrite = nullptr;
    }
    if (m_outRead) {
        CloseHandle(m_outRead);
        m_outRead = nullptr;
    }
    if (m_pi.hProcess) {
        CloseHandle(m_pi.hProcess);
        CloseHandle(m_pi.hThread);
        m_pi = PROCESS_INFORMATION{};
    }
    m_running = false;
}

#else

// ------------------------------------------------------------------ POSIX
// forkpty: 子程序的 stdin/stdout/stderr 接上 pty slave 並成為 session leader,
// 指令交給 /bin/sh -c 執行。master fd 用 QSocketNotifier 事件驅動,
// EOF/EIO = 子程序結束。

#include <QSocketNotifier>

#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <pty.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>

PtyProcess::PtyProcess(QObject* parent)
    : QObject(parent)
{
}

PtyProcess::~PtyProcess()
{
    disconnect(); // 收尾期間不再發 output/finished
    if (m_running && m_pid > 0) {
        ::kill(static_cast<pid_t>(m_pid), SIGKILL);
        ::waitpid(static_cast<pid_t>(m_pid), nullptr, 0);
    }
    cleanup();
}

bool PtyProcess::start(const QString& command, int cols, int rows)
{
    if (m_running) {
        return false;
    }
    m_error.clear();

    winsize ws{};
    ws.ws_col = static_cast<unsigned short>(cols);
    ws.ws_row = static_cast<unsigned short>(rows);

    const QByteArray cmd = command.toUtf8();
    const pid_t pid = ::forkpty(&m_masterFd, nullptr, nullptr, &ws);
    if (pid < 0) {
        m_error = QStringLiteral("forkpty failed: %1").arg(QString::fromUtf8(std::strerror(errno)));
        return false;
    }
    if (pid == 0) { // child: 在 pty slave 上執行指令; exec 失敗以 127 結束 (同 shell 慣例)
        ::execl("/bin/sh", "sh", "-c", cmd.constData(), static_cast<char*>(nullptr));
        _exit(127);
    }

    m_pid     = pid;
    m_running = true;
    ::fcntl(m_masterFd, F_SETFL, ::fcntl(m_masterFd, F_GETFL) | O_NONBLOCK);
    m_notifier = new QSocketNotifier(m_masterFd, QSocketNotifier::Read, this);
    connect(m_notifier, &QSocketNotifier::activated, this, [this] { onMasterReadable(); });
    return true;
}

void PtyProcess::onMasterReadable()
{
    char buf[4096];
    for (;;) {
        const ssize_t n = ::read(m_masterFd, buf, sizeof(buf));
        if (n > 0) {
            emit output(QByteArray(buf, static_cast<int>(n)));
            continue;
        }
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            return; // 暫時沒資料
        }
        break; // 0 = EOF, EIO = slave 端全關 → 子程序結束
    }

    m_notifier->setEnabled(false);
    int  status  = 0;
    int  code    = 1;
    bool crashed = false;
    if (::waitpid(static_cast<pid_t>(m_pid), &status, 0) > 0) {
        if (WIFEXITED(status)) {
            code = WEXITSTATUS(status);
        } else if (WIFSIGNALED(status)) {
            code    = 128 + WTERMSIG(status);
            crashed = true;
        }
    }
    cleanup();
    emit finished(code, crashed);
}

void PtyProcess::write(const QByteArray& data)
{
    if (!m_running || data.isEmpty()) {
        return;
    }
    const char* p    = data.constData();
    qsizetype   left = data.size();
    while (left > 0) {
        const ssize_t n = ::write(m_masterFd, p, static_cast<size_t>(left));
        if (n <= 0) {
            return;
        }
        p += n;
        left -= n;
    }
}

void PtyProcess::kill()
{
    if (!m_running || m_pid <= 0) {
        return;
    }
    ::kill(static_cast<pid_t>(m_pid), SIGKILL); // finished 由 master EOF 路徑發出
}

void PtyProcess::cleanup()
{
    delete m_notifier;
    m_notifier = nullptr;
    if (m_masterFd >= 0) {
        ::close(m_masterFd);
        m_masterFd = -1;
    }
    m_running = false;
}

#endif
