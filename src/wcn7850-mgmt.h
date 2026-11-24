#pragma once
#include <stddef.h>
#include <stdint.h>

typedef struct {
    const uint8_t *bytes;
    size_t length;
    uint32_t status;
    uint32_t frequency_mhz;
} WCN7850_MGMT_FRAME;

/* Input starts at the WMI event ID, after transport framing. A successful
   view borrows the event buffer. The caller must check firmware status. */
int Wcn7850ReadMgmtEvent(const uint8_t *event, size_t length, WCN7850_MGMT_FRAME *frame);

/* Receive-path view: require a management header and exclude firmware
   CRC, decrypt, MIC and key-cache-miss errors. On failure the view is empty. */
int Wcn7850ReadValidMgmtFrame(const uint8_t *event, size_t length, WCN7850_MGMT_FRAME *frame);
