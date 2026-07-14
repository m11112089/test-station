#pragma once
//
// ecal_c (eCAL 6 C API) 的最小 C++ RAII 包裝。
// 兩平台 (Windows MinGW / Ubuntu GCC) 統一使用 C API,
// 原始碼完全一致, 只有 CMake 的連結方式依平台分支。
//
#include <QByteArray>
#include <QString>
#include <functional>
#include <string>

// forward declaration, 避免把 ecal_c 標頭外洩給所有 include 這個檔的單元
struct eCAL_Publisher;
struct eCAL_Subscriber;
struct eCAL_STopicId;
struct eCAL_SDataTypeInformation;
struct eCAL_SReceiveCallbackData;

namespace ecalwrap {

// 行程層級的初始化 / 收尾 (整個 process 呼叫一次)
bool initialize(const std::string& unitName);
void finalize();
bool ok();

class Publisher {
public:
    explicit Publisher(const QString& topic);
    ~Publisher();
    Publisher(const Publisher&) = delete;
    Publisher& operator=(const Publisher&) = delete;

    bool send(const QByteArray& payload);

private:
    eCAL_Publisher* m_pub = nullptr;
};

class Subscriber {
public:
    // 注意: callback 會在 eCAL 內部執行緒被呼叫,
    // 使用端必須自行 marshal 回自己的執行緒 (本專案用 Qt signal 自動 queue)。
    using Callback = std::function<void(QByteArray)>;

    Subscriber(const QString& topic, Callback cb);
    ~Subscriber();
    Subscriber(const Subscriber&) = delete;
    Subscriber& operator=(const Subscriber&) = delete;

private:
    static void receiveTrampoline(const eCAL_STopicId* topicId,
                                  const eCAL_SDataTypeInformation* dataTypeInfo,
                                  const eCAL_SReceiveCallbackData* data,
                                  void* userArgument);
    Callback m_cb;
    eCAL_Subscriber* m_sub = nullptr;
};

} // namespace ecalwrap
