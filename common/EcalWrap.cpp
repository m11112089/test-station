#include "common/EcalWrap.hpp"

#include <ecal_c/ecal.h>

namespace ecalwrap {

bool initialize(const std::string& unitName)
{
    // components 傳 NULL => eCAL 使用預設元件 (pub/sub/service/logging/timesync)
    return eCAL_Initialize(unitName.c_str(), nullptr, nullptr) == 0;
}

void finalize()
{
    eCAL_Finalize();
}

bool ok()
{
    return eCAL_Ok() != 0;
}

// ---------------------------------------------------------------- Publisher

Publisher::Publisher(const QString& topic)
{
    // 標註 payload 型別, eCAL Monitor 會顯示為 utf-8/json 便於目視
    eCAL_SDataTypeInformation dti{};
    dti.name              = "json";
    dti.encoding          = "utf-8";
    dti.descriptor        = nullptr;
    dti.descriptor_length = 0;

    // eCAL SHM 預設每個 publisher 只有 1 個 memfile buffer: 極短時間內連續
    // send 多筆時, 訂閱端只會看到最後一筆 (實測 UART 突發位元組流會掉訊息)。
    // 提高為 ring buffer 8 筆, 換取 burst 容忍度 (代價: 每個 pub 多幾個 memfile)。
    eCAL_Publisher_Configuration cfg = *eCAL_GetPublisherConfiguration();
    cfg.layer.shm.memfile_buffer_count = 8;

    const QByteArray topicUtf8 = topic.toUtf8();
    m_pub = eCAL_Publisher_New(topicUtf8.constData(), &dti, nullptr, &cfg);
}

Publisher::~Publisher()
{
    if (m_pub != nullptr) {
        eCAL_Publisher_Delete(m_pub);
        m_pub = nullptr;
    }
}

bool Publisher::send(const QByteArray& payload)
{
    if (m_pub == nullptr) {
        return false;
    }
    return eCAL_Publisher_Send(m_pub, payload.constData(),
                               static_cast<size_t>(payload.size()), nullptr) == 0;
}

// --------------------------------------------------------------- Subscriber

Subscriber::Subscriber(const QString& topic, Callback cb)
    : m_cb(std::move(cb))
{
    const QByteArray topicUtf8 = topic.toUtf8();
    m_sub = eCAL_Subscriber_New(topicUtf8.constData(), nullptr, nullptr, nullptr);
    if (m_sub != nullptr) {
        eCAL_Subscriber_SetReceiveCallback(m_sub, &Subscriber::receiveTrampoline, this);
    }
}

Subscriber::~Subscriber()
{
    if (m_sub != nullptr) {
        eCAL_Subscriber_RemoveReceiveCallback(m_sub);
        eCAL_Subscriber_Delete(m_sub);
        m_sub = nullptr;
    }
}

void Subscriber::receiveTrampoline(const eCAL_STopicId* /*topicId*/,
                                   const eCAL_SDataTypeInformation* /*dataTypeInfo*/,
                                   const eCAL_SReceiveCallbackData* data,
                                   void* userArgument)
{
    auto* self = static_cast<Subscriber*>(userArgument);
    if (self == nullptr || !self->m_cb || data == nullptr || data->buffer == nullptr) {
        return;
    }
    // 深拷貝 payload — 離開 callback 後 eCAL 的 buffer 即失效
    QByteArray payload(static_cast<const char*>(data->buffer),
                       static_cast<int>(data->buffer_size));
    self->m_cb(std::move(payload));
}

} // namespace ecalwrap
