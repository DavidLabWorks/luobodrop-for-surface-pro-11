#include "wcn7850-mgmt.h"

/* ath12k v6.16 wmi.h: command ID, TLV tags and mgmt_rx_params layout. */
#define MGMT_RX_EVENT 0x7001u
#define TAG_MGMT_RX_HDR 44u
#define TAG_ARRAY_BYTE 17u
#define MGMT_PARAMS_BYTES 68u
#define RX_ERR_CRC 0x01u
#define RX_ERR_DECRYPT 0x08u
#define RX_ERR_MIC 0x10u
#define RX_ERR_KEY_CACHE_MISS 0x20u
#define HTC_HEADER_BYTES 8u
#define HTC_MAX_MESSAGE_BYTES 4096u
#define HTC_LAST_ENDPOINT 8u
#define HTC_TRAILER_PRESENT 0x02u
#define HTC_RECORD_HEADER_BYTES 4u

static uint32_t Read32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
           (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

int Wcn7850ReadMgmtEvent(const uint8_t *event, size_t length, WCN7850_MGMT_FRAME *frame)
{
    const uint8_t *params = NULL, *bytes = NULL;
    size_t capacity = 0;
    if (!frame)
        return 0;
    *frame = (WCN7850_MGMT_FRAME){0};
    if (!event || length < 4 || (Read32(event) & 0xffffffu) != MGMT_RX_EVENT)
        return 0;

    for (size_t offset = 4; offset < length;) {
        if (length - offset < 4)
            return 0;
        uint32_t header = Read32(event + offset);
        size_t size = header & 0xffffu;
        uint32_t tag = header >> 16;
        offset += 4;
        if (size > length - offset)
            return 0;
        if (tag == TAG_MGMT_RX_HDR) {
            if (params || size < MGMT_PARAMS_BYTES)
                return 0;
            params = event + offset;
        } else if (tag == TAG_ARRAY_BYTE && !bytes) {
            bytes = event + offset;
            capacity = size;
        }
        offset += size;
    }

    if (!params || !bytes)
        return 0;
    uint32_t frame_length = Read32(params + 16);
    if (!frame_length || frame_length > capacity)
        return 0;
    frame->bytes = bytes;
    frame->length = frame_length;
    frame->status = Read32(params + 20);
    frame->frequency_mhz = Read32(params + 64);
    return 1;
}

int Wcn7850ReadValidMgmtFrame(const uint8_t *event, size_t length, WCN7850_MGMT_FRAME *frame)
{
    if (!Wcn7850ReadMgmtEvent(event, length, frame))
        return 0;
    if ((frame->status & (RX_ERR_CRC | RX_ERR_DECRYPT | RX_ERR_MIC | RX_ERR_KEY_CACHE_MISS)) ||
        frame->length < 24 || (frame->bytes[0] & 0x0fu) != 0) {
        *frame = (WCN7850_MGMT_FRAME){0};
        return 0;
    }
    return 1;
}

int Wcn7850ReadHtcMgmtFrame(const uint8_t *buffer, size_t received, uint8_t wmi_endpoint,
                            WCN7850_MGMT_FRAME *frame)
{
    if (!frame)
        return 0;
    *frame = (WCN7850_MGMT_FRAME){0};
    if (!buffer || received < HTC_HEADER_BYTES || !wmi_endpoint ||
        wmi_endpoint > HTC_LAST_ENDPOINT || buffer[0] != wmi_endpoint)
        return 0;

    size_t payload = (size_t)buffer[2] | (size_t)buffer[3] << 8;
    if (payload > received - HTC_HEADER_BYTES || payload > HTC_MAX_MESSAGE_BYTES - HTC_HEADER_BYTES)
        return 0;
    const uint8_t *event = buffer + HTC_HEADER_BYTES;
    size_t event_length = payload;
    if (buffer[1] & HTC_TRAILER_PRESENT) {
        size_t trailer = buffer[4];
        if (trailer < HTC_RECORD_HEADER_BYTES || trailer > payload)
            return 0;
        event_length -= trailer;
        for (size_t pos = event_length; pos < payload;) {
            if (payload - pos < HTC_RECORD_HEADER_BYTES)
                return 0;
            size_t size = HTC_RECORD_HEADER_BYTES + event[pos + 1];
            if (size > payload - pos)
                return 0;
            pos += size;
        }
    }
    return Wcn7850ReadValidMgmtFrame(event, event_length, frame);
}
