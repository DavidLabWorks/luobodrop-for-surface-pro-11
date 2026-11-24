#pragma once

#include "station-port.h"

/* Include after netadaptercx.h. Both queue callbacks are serialized by NetAdapterCx. */
typedef struct {
    size_t Bytes;
    MT7922_PRIVACY Privacy;
    bool Eapol;
    uint8_t Data[MT7922_STATION_RAW_MAX];
} MT7922_STATION_BUFFER;

/* Takes at most one Native 802.11 packet. A full destination leaves the host ring untouched. */
bool Mt7922TakeNetTxData(const NET_RING_COLLECTION *Rings, const NET_EXTENSION *VirtualAddress,
    const NET_EXTENSION *ExemptionAction, MT7922_STATION_BUFFER *Destination);
/* Posts one packet to the host only after copying it to a supplied receive buffer. */
bool Mt7922PutNetRxData(const NET_RING_COLLECTION *Rings, const NET_EXTENSION *VirtualAddress,
    const MT7922_STATION_BUFFER *Source);
void Mt7922CancelNetRing(const NET_RING_COLLECTION *Rings, bool Receive);
