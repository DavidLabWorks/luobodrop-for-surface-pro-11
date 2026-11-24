/* Synthetic WMI envelope carrying an unchanged captured 802.11 action frame. */
#include "../src/wcn7850-mgmt.h"

static void ReplayWcn7850Event(const uint8_t *bytes, size_t length, AWDL_ACTION_KIND expected)
{
    uint8_t event[80 + FRAME_CAPACITY] = {0};
    size_t padded = (length + 3u) & ~(size_t)3u;
    WCN7850_MGMT_FRAME frame;
    AWDL_ACTION action;
    assert(padded <= FRAME_CAPACITY);
    WriteLe32(event, 0x7001);
    WriteLe32(event + 4, (44u << 16) | 68u);
    WriteLe32(event + 8, 44);
    WriteLe32(event + 24, (uint32_t)length);
    WriteLe32(event + 72, 5220);
    WriteLe32(event + 76, (17u << 16) | (uint32_t)padded);
    memcpy(event + 80, bytes, length);
    assert(Wcn7850ReadMgmtEvent(event, 80 + padded, &frame));
    assert(frame.length == length && frame.status == 0 && frame.frequency_mhz == 5220);
    assert(memcmp(frame.bytes, bytes, length) == 0);
    assert(AwdlDecodeAction(frame.bytes, frame.length, &action) == expected);

    /* Verify the reader also accepts the byte array before the fixed header. */
    uint8_t fixed[72];
    memcpy(fixed, event + 4, sizeof(fixed));
    memmove(event + 4, event + 76, 4 + padded);
    memcpy(event + 8 + padded, fixed, sizeof(fixed));
    assert(Wcn7850ReadMgmtEvent(event, 80 + padded, &frame));
    assert(frame.length == length && memcmp(frame.bytes, bytes, length) == 0);
    assert(AwdlDecodeAction(frame.bytes, frame.length, &action) == expected);
    assert(!Wcn7850ReadMgmtEvent(event, 8 + padded, &frame));
    assert(!Wcn7850ReadMgmtEvent(event, 79 + padded, &frame));
    assert(!frame.bytes && !frame.length);
    WriteLe32(event + 12 + padded + 16, (uint32_t)padded + 1u);
    assert(!Wcn7850ReadMgmtEvent(event, 80 + padded, &frame));
    WriteLe32(event + 12 + padded + 16, (uint32_t)length);
    WriteLe32(event, 0x7002);
    assert(!Wcn7850ReadMgmtEvent(event, 80 + padded, &frame));
}
