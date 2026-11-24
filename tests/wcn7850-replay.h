/* Synthetic WMI envelope carrying an unchanged captured 802.11 action frame. */
#include "../src/wcn7850-mgmt.h"

static void CheckWcn7850RadioMeasurement(void)
{
    uint8_t event[80 + 32] = {0};
    WCN7850_MGMT_FRAME frame;
    AWDL_ACTION action;
    WriteLe32(event, 0x7001);
    WriteLe32(event + 4, (44u << 16) | 68u);
    WriteLe32(event + 24, 29); /* MAC header plus category, action, token and repetitions. */
    WriteLe32(event + 76, (17u << 16) | 32u);
    event[80] = 0xd0;
    event[104] = 5;
    assert(Wcn7850ReadValidMgmtFrame(event, sizeof(event), &frame));
    assert(frame.length == 29 && frame.bytes[24] == 5 && frame.bytes[25] == 0);
    assert(AwdlDecodeAction(frame.bytes, frame.length, &action) == AwdlOther);
}

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
    assert(Wcn7850ReadValidMgmtFrame(event, 80 + padded, &frame));
    assert(frame.length == length && frame.status == 0 && frame.frequency_mhz == 5220);
    assert(memcmp(frame.bytes, bytes, length) == 0);
    assert(AwdlDecodeAction(frame.bytes, frame.length, &action) == expected);

    const uint32_t errors[] = {0x01, 0x08, 0x10, 0x20};
    for (size_t i = 0; i < sizeof(errors) / sizeof(errors[0]); ++i) {
        WriteLe32(event + 28, errors[i]);
        assert(Wcn7850ReadMgmtEvent(event, 80 + padded, &frame));
        assert(frame.status == errors[i]);
        assert(!Wcn7850ReadValidMgmtFrame(event, 80 + padded, &frame));
        assert(!frame.bytes && !frame.length);
    }
    WriteLe32(event + 28, 0);
    WriteLe32(event + 24, 23);
    assert(!Wcn7850ReadValidMgmtFrame(event, 80 + padded, &frame));
    WriteLe32(event + 24, (uint32_t)length);
    event[80] = 0x08; /* Data frame reported in a management event. */
    assert(!Wcn7850ReadValidMgmtFrame(event, 80 + padded, &frame));
    event[80] = bytes[0];

    /* Verify the reader also accepts the byte array before the fixed header. */
    uint8_t fixed[72];
    memcpy(fixed, event + 4, sizeof(fixed));
    memmove(event + 4, event + 76, 4 + padded);
    memcpy(event + 8 + padded, fixed, sizeof(fixed));
    assert(Wcn7850ReadValidMgmtFrame(event, 80 + padded, &frame));
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
