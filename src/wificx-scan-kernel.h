#pragma once

#include "wificx-scan.h"

/* Include after WDF, WiFiCx, and mcu.h. Use from the serialized PASSIVE_LEVEL
   station worker with an initialized WiFiCx device and its live MCU rings. */
NTSTATUS Mt7922PublishWificxIndication(WDFDEVICE Device, uint16_t Message,
    const uint8_t *Buffer, size_t Bytes);
void Mt7922SaveWificxConnectDebug(MT7922_MCU_STATION *Station);
void Mt7922PollWificxSharedAdvertRequest(MT7922_MCU_STATION *Station);
NTSTATUS Mt7922BindWificxScan(MT7922_WDI_SCAN *Scan, WDFDEVICE Device,
    MT7922_MCU_STATION *Station, const struct _TLV_CONTEXT *Tlv);
void Mt7922HandleWificxScan(MT7922_WDI_SCAN *Scan, WIFIREQUEST Request,
    uint8_t BandIndex, uint8_t BssIndex);

/* Call from the station worker after completing the WiFiCx task-start request.
   The caller supplies IEs and cipher selection for the chosen Windows profile. */
NTSTATUS Mt7922CompleteWificxJoin(MT7922_MCU_STATION *Station, WDFDEVICE Device,
    const struct _WDI_MESSAGE_HEADER *Header, const struct _TLV_CONTEXT *Tlv,
    const MT7922_STATION_JOIN_REQUEST *Join, bool Wpa2PersonalCcmp,
    MT7922_STATION_RECEIVE Receive, void *Context);
void Mt7922HandleWificxCcmpKeys(MT7922_MCU_STATION *Station, WIFIREQUEST Request,
    const struct _TLV_CONTEXT *Tlv);

void Mt7922HandleWificxConnect(MT7922_MCU_STATION *Station, WDFDEVICE Device,
    WIFIREQUEST Request, const struct _TLV_CONTEXT *Tlv, const MT7922_WDI_SCAN *Scan,
    uint16_t Wcid, MT7922_STATION_RECEIVE Receive, void *Context);


void Mt7922HandleWificxEapolPrivacy(MT7922_MCU_STATION *Station, WIFIREQUEST Request,
    const struct _TLV_CONTEXT *Tlv);
