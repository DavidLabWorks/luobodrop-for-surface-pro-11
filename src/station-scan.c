/* SPDX-License-Identifier: BSD-3-Clause-Clear */
/* Adapted from the Connac2 scan path in mt76_connac_mcu.c (MediaTek Inc.). */
#include "station-scan.h"

#include <string.h>

#define MCU_RX_HEADER_BYTES 36U
#define MCU_RX_EVENT_OFFSET 28U
#define MCU_RX_EVENT_TYPE 7U
#define MCU_TX_FORMAT (2U << 23)
#define MCU_TX_QUEUE (0x20U << 25)
#define MCU_TX_LONG_COMMAND 0x80010000U

#define SCAN_CHANNELS_PER_LIST 32U
#define SCAN_FUNCTION_SPLIT (1U << 5)
#define SCAN_CHANNEL_LIST 4U
#define SCAN_DONE_MIN_BYTES 20U

#pragma pack(push, 1)
typedef struct {
    uint8_t Length[4];
    uint8_t Data[MT7922_SCAN_MAX_SSID_BYTES];
} SCAN_SSID;

typedef struct {
    uint8_t Sequence;
    uint8_t BssIndex;
    uint8_t Active;
    uint8_t SsidType;
    uint8_t SsidCount;
    uint8_t ProbeCount;
    uint8_t Function;
    uint8_t Version;
    SCAN_SSID Ssids[MT7922_SCAN_MAX_SSIDS];
    uint8_t ProbeDelay[2];
    uint8_t Dwell[2];
    uint8_t Timeout[2];
    uint8_t ChannelType;
    uint8_t ChannelCount;
    MT7922_SCAN_CHANNEL Channels[SCAN_CHANNELS_PER_LIST];
    uint8_t IeBytes[2];
    uint8_t InformationElements[MT7922_SCAN_MAX_IE_BYTES];
    uint8_t ExtendedChannelCount;
    uint8_t ExtendedSsidCount;
    uint8_t MinimumDwell[2];
    MT7922_SCAN_CHANNEL ExtendedChannels[SCAN_CHANNELS_PER_LIST];
    SCAN_SSID ExtendedSsids[6];
    uint8_t Bssid[6];
    uint8_t RandomMac[6];
    uint8_t Reserved[63];
    uint8_t SsidTypeExtension;
} SCAN_REQUEST;
#pragma pack(pop)

_Static_assert(sizeof(SCAN_REQUEST) == MT7922_SCAN_REQUEST_BYTES, "Connac2 scan request size");
_Static_assert(offsetof(SCAN_REQUEST, ExtendedChannelCount) == 826U, "Connac2 extension offset");
_Static_assert(offsetof(SCAN_REQUEST, SsidTypeExtension) == 1185U, "Connac2 SSID type offset");

bool Mt7922BuildScanRequest(uint8_t *Buffer, size_t Bytes, const MT7922_SCAN_PARAMETERS *Parameters)
{
    if (Buffer == NULL || Bytes < sizeof(SCAN_REQUEST) || Parameters == NULL ||
        Parameters->Sequence > 0x7fU || Parameters->BandIndex > 1 ||
        Parameters->SsidCount > MT7922_SCAN_MAX_SSIDS ||
        Parameters->ChannelCount > MT7922_SCAN_MAX_CHANNELS ||
        Parameters->InformationElementBytes > MT7922_SCAN_MAX_IE_BYTES ||
        (Parameters->SsidCount != 0 && (Parameters->Ssids == NULL || !Parameters->Active)) ||
        (Parameters->ChannelCount != 0 && Parameters->Channels == NULL) ||
        (Parameters->InformationElementBytes != 0 && Parameters->InformationElements == NULL)) {
        return false;
    }

    SCAN_REQUEST request = {0};
    request.Sequence = (uint8_t)(Parameters->Sequence | (Parameters->BandIndex << 7));
    request.BssIndex = Parameters->BssIndex;
    request.Active = Parameters->Active;
    request.ProbeCount = Parameters->Active ? 2 : 0;
    request.Function = SCAN_FUNCTION_SPLIT; /* Firmware supplies dwell times for Connac2. */
    request.Version = 1;
    if (Parameters->Bssid != NULL) {
        memcpy(request.Bssid, Parameters->Bssid, sizeof(request.Bssid));
    } else {
        memset(request.Bssid, 0xff, sizeof(request.Bssid));
    }
    request.Dwell[0] = (uint8_t)Parameters->DwellMilliseconds;
    request.Dwell[1] = (uint8_t)(Parameters->DwellMilliseconds >> 8);
    memcpy(request.MinimumDwell, request.Dwell, sizeof(request.Dwell));
    request.Timeout[0] = (uint8_t)Parameters->TimeoutMilliseconds;
    request.Timeout[1] = (uint8_t)(Parameters->TimeoutMilliseconds >> 8);

    for (size_t i = 0; i < Parameters->SsidCount; ++i) {
        const MT7922_SCAN_SSID *ssid = &Parameters->Ssids[i];
        if (ssid->Length > sizeof(ssid->Data)) {
            return false;
        }
        if (ssid->Length != 0) {
            SCAN_SSID *output = &request.Ssids[request.SsidCount++];
            output->Length[0] = ssid->Length;
            memcpy(output->Data, ssid->Data, ssid->Length);
        }
    }
    request.SsidType = request.SsidCount != 0 ? (1U << 2) : (1U << 0);
    request.SsidTypeExtension = request.SsidCount != 0;

    if (Parameters->ChannelCount != 0) {
        request.ChannelType = SCAN_CHANNEL_LIST;
        const size_t mainCount = Parameters->ChannelCount < SCAN_CHANNELS_PER_LIST
            ? Parameters->ChannelCount : SCAN_CHANNELS_PER_LIST;
        request.ChannelCount = (uint8_t)mainCount;
        request.ExtendedChannelCount = (uint8_t)(Parameters->ChannelCount - mainCount);
        for (size_t i = 0; i < Parameters->ChannelCount; ++i) {
            const MT7922_SCAN_CHANNEL channel = Parameters->Channels[i];
            if (channel.Band < 1 || channel.Band > 3 || channel.Number == 0) {
                return false;
            }
            if (i < mainCount) {
                request.Channels[i] = channel;
            } else {
                request.ExtendedChannels[i - mainCount] = channel;
            }
        }
    }

    if (Parameters->InformationElementBytes != 0) {
        request.IeBytes[0] = (uint8_t)Parameters->InformationElementBytes;
        request.IeBytes[1] = (uint8_t)(Parameters->InformationElementBytes >> 8);
        memcpy(request.InformationElements, Parameters->InformationElements,
            Parameters->InformationElementBytes);
    }
    memcpy(Buffer, &request, sizeof(request));
    return true;
}

bool Mt7922LimitStationScan(uint8_t *Buffer, const uint8_t *Input,
    const MT7922_STATION_RADIO_PROFILE *Radio)
{
    SCAN_REQUEST request;
    memcpy(&request, Input, sizeof(request));
    if ((request.ChannelType != 0 && request.ChannelType != SCAN_CHANNEL_LIST) ||
        request.ChannelCount > SCAN_CHANNELS_PER_LIST ||
        request.ExtendedChannelCount > SCAN_CHANNELS_PER_LIST ||
        ((request.ChannelType == SCAN_CHANNEL_LIST) != (request.ChannelCount != 0)) ||
        (request.ExtendedChannelCount != 0 && request.ChannelCount != SCAN_CHANNELS_PER_LIST)) {
        return false;
    }
    bool allow[256] = {0};
    for (size_t i = 0; i < request.ChannelCount; ++i) {
        if (request.Channels[i].Band == 2) {
            allow[request.Channels[i].Number] = true;
        }
    }
    for (size_t i = 0; i < request.ExtendedChannelCount; ++i) {
        if (request.ExtendedChannels[i].Band == 2) {
            allow[request.ExtendedChannels[i].Number] = true;
        }
    }
    const bool all = request.ChannelType == 0;
    request.ChannelCount = request.ExtendedChannelCount = 0;
    memset(request.Channels, 0, sizeof(request.Channels));
    memset(request.ExtendedChannels, 0, sizeof(request.ExtendedChannels));
    for (size_t i = 0; i < Radio->ChannelCount; ++i) {
        const uint8_t number = Radio->Channels[i].Number;
        if (all || allow[number]) {
            request.Channels[request.ChannelCount++] = (MT7922_SCAN_CHANNEL){2, number};
        }
    }
    if (request.ChannelCount == 0) {
        return false;
    }
    request.ChannelType = SCAN_CHANNEL_LIST;
    memcpy(Buffer, &request, sizeof(request));
    return true;
}

bool Mt7922BuildScanCancel(uint8_t *Buffer, size_t Bytes, uint8_t Sequence)
{
    if (Buffer == NULL || Bytes < MT7922_SCAN_CANCEL_BYTES || Sequence > 0x7fU) {
        return false;
    }
    const uint8_t request[MT7922_SCAN_CANCEL_BYTES] = {Sequence, 0, 0, 0};
    memcpy(Buffer, request, sizeof(request));
    return true;
}

bool Mt7922IsScanDone(uint8_t Event, const uint8_t *Payload, size_t Bytes,
    uint8_t Sequence, uint8_t BandIndex)
{
    return Event == MT7922_SCAN_DONE_EVENT && Payload != NULL && Bytes >= SCAN_DONE_MIN_BYTES &&
        Sequence <= 0x7fU && BandIndex <= 1 && Payload[0] == (Sequence | (BandIndex << 7));
}


size_t Mt7922FrameScanCommand(uint8_t *Buffer, size_t Bytes, uint8_t Command,
    uint8_t McuSequence, const uint8_t *Payload, size_t PayloadBytes)
{
    const size_t required = Command == MT7922_SCAN_START_COMMAND ? MT7922_SCAN_REQUEST_BYTES :
        Command == MT7922_SCAN_CANCEL_COMMAND ? MT7922_SCAN_CANCEL_BYTES : 0;
    return required != 0 && PayloadBytes == required ?
        Mt7922FrameLegacyMcuCommand(Buffer, Bytes, Command, 0, 1, McuSequence, Payload, PayloadBytes) : 0;
}

size_t Mt7922FrameLegacyMcuCommand(uint8_t *Buffer, size_t Bytes, uint8_t Command,
    uint8_t Extension, uint8_t SetQuery, uint8_t Sequence, const uint8_t *Payload, size_t PayloadBytes)
{
    const size_t header = MT7922_SCAN_TX_HEADER_BYTES;
    if (Buffer == NULL || Bytes < header || PayloadBytes > Bytes - header ||
        PayloadBytes > UINT16_MAX - header || (Payload == NULL && PayloadBytes != 0) ||
        Sequence == 0 || Sequence > 15) {
        return 0;
    }
    const size_t total = header + PayloadBytes;
    const uint32_t descriptor = (uint32_t)total | MCU_TX_FORMAT | MCU_TX_QUEUE;
    const uint32_t longCommand = MCU_TX_LONG_COMMAND;
    const size_t mcuBytes = total - 32;
    memset(Buffer, 0, MT7922_SCAN_TX_HEADER_BYTES);
    for (unsigned i = 0; i < 4; ++i) {
        Buffer[i] = (uint8_t)(descriptor >> (8U * i));
        Buffer[4 + i] = (uint8_t)(longCommand >> (8U * i));
    }
    Buffer[32] = (uint8_t)mcuBytes;
    Buffer[33] = (uint8_t)(mcuBytes >> 8);
    Buffer[35] = 0x80; /* WM port and queue zero. */
    Buffer[36] = Command;
    Buffer[37] = 0xa0; /* MCU packet ID. */
    Buffer[38] = SetQuery;
    Buffer[39] = Sequence;
    Buffer[41] = Extension;
    Buffer[43] = Extension != 0;
    if (PayloadBytes != 0) {
        memcpy(Buffer + header, Payload, PayloadBytes);
    }
    return total;
}

bool Mt7922MatchScanRecord(const uint8_t *Record, size_t Bytes,
    uint8_t Sequence, uint8_t BandIndex)
{
    if (Record == NULL || Bytes < MCU_RX_HEADER_BYTES ||
        (Record[3] >> 3) != MCU_RX_EVENT_TYPE || (Record[2] & 0x0fU) == 1U) {
        return false;
    }
    const size_t recordBytes = Record[0] | ((size_t)Record[1] << 8);
    return recordBytes >= MCU_RX_HEADER_BYTES && recordBytes <= Bytes &&
        Mt7922IsScanDone(Record[MCU_RX_EVENT_OFFSET], Record + MCU_RX_HEADER_BYTES,
            recordBytes - MCU_RX_HEADER_BYTES, Sequence, BandIndex);
}
