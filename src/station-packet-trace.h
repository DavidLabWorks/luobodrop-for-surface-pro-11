#pragma once

#include "station-port.h"

typedef enum {
    Mt7922TraceTxQueued = 1,
    Mt7922TraceTxSubmit,
    Mt7922TraceTxDmaObserved,
    Mt7922TraceTxFree,
    Mt7922TraceTxFailed,
    Mt7922TraceRxDmaObserved,
    Mt7922TraceRxStaged,
    Mt7922TraceRxPosted
} MT7922_STATION_TRACE_STAGE;

enum {
    MT7922_CONTROL_RUNNING = 0x01,
    MT7922_CONTROL_RADIO_ENABLED = 0x02,
    MT7922_CONTROL_STATION_READY = 0x04,
    MT7922_CONTROL_SCAN_RADIO_BOUND = 0x08,
    MT7922_CONTROL_PORT_VALID = 0x10,
    MT7922_CONTROL_SCAN_ACTIVE = 0x20,
    MT7922_CONTROL_SCAN_CANCELLING = 0x40
};

void Mt7922RegisterStationPacketTrace(void);
void Mt7922UnregisterStationPacketTrace(void);
bool Mt7922StationPacketTraceEnabled(void);
void Mt7922TraceStationControl(const char *Stage, uint16_t Message, uint32_t TransactionId,
    uint16_t PortId, int32_t Status, uint32_t Flags, uint32_t Detail);
void Mt7922SetStationPacketTraceTargets(const uint8_t Targets[8]);
bool Mt7922MatchStationPacketTrace(const uint8_t *Frame, size_t Bytes, bool Receive,
    MT7922_STATION_TCP_HEADER *Packet);
void Mt7922WriteStationPacketTrace(const MT7922_STATION_TCP_HEADER *Packet,
    MT7922_STATION_TRACE_STAGE Stage, int32_t Status);
void Mt7922TraceStationFrame(const uint8_t *Frame, size_t Bytes, bool Receive,
    MT7922_STATION_TRACE_STAGE Stage);
