#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "rx.h"

#define MT7922_OBSERVER_PEERS 4U
#define MT7922_OBSERVER_SLOTS 16U
#define MT7922_OBSERVER_ENABLE_BYTES 12U
#define MT7922_OBSERVER_CONFIG_BYTES 20U

typedef struct {
    uint8_t Address[6];
    uint32_t Psf;
    uint32_t Mif;
    uint32_t Data;
    uint8_t DeviceClass;
    bool Ios;
    uint8_t Master[6];
    uint16_t WindowTu;
    uint16_t NextWindowSequence;
    uint8_t NextChannel;
    uint8_t MasterChannel;
    uint8_t WindowsPerSlot;
    uint8_t LastChannel;
    uint8_t SlotCount;
    uint8_t Slots[MT7922_OBSERVER_SLOTS];
    uint8_t OperatingClass[MT7922_OBSERVER_SLOTS];
} MT7922_OBSERVER_PEER;

/* Receive-only AWDL view of the station's channel. The radio stays with the
   router; nothing here transmits or retunes. */
typedef struct {
    bool Running;
    uint8_t Channel;
    uint8_t Band;
    uint8_t Local[6];
    uint32_t Records;
    uint32_t Invalid;
    uint32_t Psf;
    uint32_t Mif;
    uint32_t Data;
    uint32_t DataToUs;
    uint32_t PeerCount;
    MT7922_OBSERVER_PEER Peers[MT7922_OBSERVER_PEERS];
} MT7922_STATION_OBSERVER;

typedef enum _MT7922_OBSERVE_STATUS {
    Mt7922ObserveIgnored,
    Mt7922ObserveInvalid,
    Mt7922ObservePsf,
    Mt7922ObserveMif,
    Mt7922ObserveData
} MT7922_OBSERVE_STATUS;

void Mt7922ObserverStart(MT7922_STATION_OBSERVER *Observer, uint8_t Channel, uint8_t Band,
    const uint8_t Local[6]);
void Mt7922ObserverStop(MT7922_STATION_OBSERVER *Observer);
/* The station passes its already decoded record and retains polling ownership. */
MT7922_OBSERVE_STATUS Mt7922ObserverIngest(MT7922_STATION_OBSERVER *Observer,
    MT7922_RX_STATUS Status, const MT7922_RX_FRAME *Frame);

/* Connac2 SNIFFER TLVs, matching mt7921_mcu_set_sniffer and
   mt7921_mcu_config_sniffer. The station link stays 20 MHz. */
void Mt7922BuildStationSnifferEnable(uint8_t Command[MT7922_OBSERVER_ENABLE_BYTES], bool Enable);
bool Mt7922BuildStationSnifferConfig(uint8_t Command[MT7922_OBSERVER_CONFIG_BYTES],
    uint8_t Channel, uint8_t Band, bool DropErrors);
