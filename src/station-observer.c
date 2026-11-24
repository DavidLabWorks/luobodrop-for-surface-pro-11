/* SPDX-License-Identifier: BSD-3-Clause-Clear */
/* Receive-only AWDL observation while the station stays on the router channel. */
#include "station-observer.h"
#include <string.h>

static const uint8_t AwdlBssid[6] = {0x00, 0x25, 0x00, 0xff, 0x94, 0x73};

static MT7922_OBSERVER_PEER *FindPeer(MT7922_STATION_OBSERVER *Observer,
    const uint8_t Address[6])
{
    for (uint32_t i = 0; i < Observer->PeerCount; ++i) {
        if (memcmp(Observer->Peers[i].Address, Address, 6) == 0) {
            return &Observer->Peers[i];
        }
    }
    if (Observer->PeerCount >= MT7922_OBSERVER_PEERS) {
        return NULL;
    }
    MT7922_OBSERVER_PEER *peer = &Observer->Peers[Observer->PeerCount++];
    memset(peer, 0, sizeof(*peer));
    memcpy(peer->Address, Address, 6);
    return peer;
}

static bool IsAwdlData(const MT7922_RX_FRAME *Frame, const uint8_t Local[6],
    const uint8_t **Sender, bool *ToUs)
{
    if (Frame->Length < 24) {
        return false;
    }
    const uint8_t *bytes = Frame->Bytes;
    const uint16_t fc = (uint16_t)(bytes[0] | ((uint16_t)bytes[1] << 8));
    const uint8_t subtype = (uint8_t)((fc >> 4) & 0xfU);
    if (((fc >> 2) & 3U) != 2U || (fc & 0x0300U) != 0 || (subtype != 0 && subtype != 8) ||
        memcmp(bytes + 16, AwdlBssid, sizeof(AwdlBssid)) != 0) {
        return false;
    }
    *Sender = bytes + 10;
    *ToUs = memcmp(bytes + 4, Local, 6) == 0;
    return true;
}

void Mt7922ObserverStart(MT7922_STATION_OBSERVER *Observer, uint8_t Channel, uint8_t Band,
    const uint8_t Local[6])
{
    memset(Observer, 0, sizeof(*Observer));
    Observer->Running = true;
    Observer->Channel = Channel;
    Observer->Band = Band;
    memcpy(Observer->Local, Local, sizeof(Observer->Local));
}

void Mt7922ObserverStop(MT7922_STATION_OBSERVER *Observer)
{
    Observer->Running = false;
}

MT7922_OBSERVE_STATUS Mt7922ObserverIngest(MT7922_STATION_OBSERVER *Observer,
    MT7922_RX_STATUS Status, const MT7922_RX_FRAME *Frame)
{
    if (!Observer->Running) {
        return Mt7922ObserveIgnored;
    }
    if (Status == Mt7922RxMalformed) {
        ++Observer->Invalid;
        return Mt7922ObserveInvalid;
    }
    if (Status != Mt7922RxRawFrame) {
        return Mt7922ObserveIgnored;
    }
    ++Observer->Records;

    AWDL_ACTION action;
    const AWDL_ACTION_KIND kind = AwdlDecodeAction(Frame->Bytes, Frame->Length, &action);
    if (kind == AwdlMalformed) {
        ++Observer->Invalid;
        return Mt7922ObserveInvalid;
    }
    if (kind == AwdlPsf || kind == AwdlMif) {
        const bool psf = kind == AwdlPsf;
        if (psf) {
            ++Observer->Psf;
        } else {
            ++Observer->Mif;
        }
        if ((action.Sender[0] & 1U) != 0) {
            return psf ? Mt7922ObservePsf : Mt7922ObserveMif;
        }
        MT7922_OBSERVER_PEER *peer = FindPeer(Observer, action.Sender);
        if (peer == NULL) {
            return psf ? Mt7922ObservePsf : Mt7922ObserveMif;
        }
        if (psf) {
            ++peer->Psf;
        } else {
            ++peer->Mif;
        }
        peer->DeviceClass = action.DeviceClass;
        peer->Ios = action.DeviceClass == 2;
        peer->LastChannel = Frame->Channel;
        if (action.Sync.Master != NULL) {
            memcpy(peer->Master, action.Sync.Master, sizeof(peer->Master));
            peer->WindowTu = action.Sync.WindowTu;
            peer->NextWindowSequence = action.Sync.NextWindowSequence;
            peer->NextChannel = action.Sync.NextChannel;
            peer->MasterChannel = action.Sync.MasterChannel;
            peer->WindowsPerSlot = action.Sync.WindowsPerSlot;
        }
        if (action.Sequence.Count == MT7922_OBSERVER_SLOTS) {
            peer->SlotCount = action.Sequence.Count;
            memcpy(peer->Slots, action.Sequence.Channel, sizeof(peer->Slots));
            memcpy(peer->OperatingClass, action.Sequence.OperatingClass,
                sizeof(peer->OperatingClass));
        }
        return psf ? Mt7922ObservePsf : Mt7922ObserveMif;
    }

    const uint8_t *sender;
    bool toUs;
    if (!IsAwdlData(Frame, Observer->Local, &sender, &toUs)) {
        return Mt7922ObserveIgnored;
    }
    ++Observer->Data;
    Observer->DataToUs += toUs ? 1U : 0U;
    if ((sender[0] & 1U) == 0) {
        MT7922_OBSERVER_PEER *peer = FindPeer(Observer, sender);
        if (peer != NULL) {
            ++peer->Data;
            peer->LastChannel = Frame->Channel;
        }
    }
    return Mt7922ObserveData;
}

void Mt7922BuildStationSnifferEnable(uint8_t Command[MT7922_OBSERVER_ENABLE_BYTES], bool Enable)
{
    memset(Command, 0, MT7922_OBSERVER_ENABLE_BYTES);
    Command[6] = 8; /* SNIFFER enable TLV. */
    Command[8] = Enable ? 1U : 0U;
}

bool Mt7922BuildStationSnifferConfig(uint8_t Command[MT7922_OBSERVER_CONFIG_BYTES],
    uint8_t Channel, uint8_t Band, bool DropErrors)
{
    if (Channel == 0 || Band < 1 || Band > 3) {
        return false;
    }
    /* TLV 1: aid, band, bandwidth, control/center channel, SCA/B, drop errors.
       A 20 MHz station leaves the control and center channel equal. */
    memset(Command, 0, MT7922_OBSERVER_CONFIG_BYTES);
    Command[4] = 1;
    Command[6] = 16;
    Command[10] = Band;
    Command[12] = Channel;
    Command[14] = Channel;
    Command[16] = DropErrors ? 1U : 0U;
    return true;
}
