#include <ntddk.h>
#include <TraceLoggingProvider.h>
#include "station-packet-trace.h"

TRACELOGGING_DEFINE_PROVIDER(StationPacketProvider, "MT7922.StationPacket",
    (0x79ed2cd0, 0xf984, 0x4bb5, 0xb2, 0x16, 0xde, 0xef, 0xa8, 0x7b, 0x1e, 0xd9));

/* The single supported adapter is sampled by its existing station worker. */
static volatile LONG64 StationPacketTargets;
#define STATION_PACKET_TRACE_LEVEL 4U /* Informational. */

void Mt7922RegisterStationPacketTrace(void)
{
    (void)TraceLoggingRegister(StationPacketProvider);
}

void Mt7922UnregisterStationPacketTrace(void)
{
    TraceLoggingUnregister(StationPacketProvider);
}

bool Mt7922StationPacketTraceEnabled(void)
{
    return TraceLoggingProviderEnabled(StationPacketProvider, STATION_PACKET_TRACE_LEVEL, 0) != FALSE;
}

void Mt7922TraceStationControl(const char *Stage, uint16_t Message, uint32_t TransactionId,
    uint16_t PortId, int32_t Status, uint32_t Flags, uint32_t Detail)
{
    TraceLoggingWrite(StationPacketProvider, "StationControl",
        TraceLoggingLevel(STATION_PACKET_TRACE_LEVEL),
        TraceLoggingString(Stage, "Stage"),
        TraceLoggingHexUInt16(Message, "WdiMessage"),
        TraceLoggingUInt32(TransactionId, "TransactionId"),
        TraceLoggingUInt16(PortId, "PortId"),
        TraceLoggingHexInt32(Status, "Status"),
        TraceLoggingHexUInt32(Flags, "Flags"),
        TraceLoggingUInt32(Detail, "Detail"));
}

void Mt7922SetStationPacketTraceTargets(const uint8_t Targets[8])
{
    uint64_t packed = 0;
    for (unsigned i = 0; i < 8; ++i) {
        packed = (packed << 8) | Targets[i];
    }
    InterlockedExchange64(&StationPacketTargets, (LONG64)packed);
    TraceLoggingWrite(StationPacketProvider, "ProbeTargets",
        TraceLoggingLevel(STATION_PACKET_TRACE_LEVEL),
        TraceLoggingHexUInt32((uint32_t)(packed >> 32), "RouterIpv4"),
        TraceLoggingHexUInt32((uint32_t)packed, "HttpsIpv4"));
}

bool Mt7922MatchStationPacketTrace(const uint8_t *Frame, size_t Bytes, bool Receive,
    MT7922_STATION_TCP_HEADER *Packet)
{
    if (!Mt7922StationPacketTraceEnabled()) {
        return false;
    }
    const uint64_t packed = (uint64_t)InterlockedCompareExchange64(&StationPacketTargets, 0, 0);
    if (packed == 0 || !Mt7922ReadStationTcpHeader(Frame, Bytes, Receive, Packet)) {
        return false;
    }
    const uint32_t remoteIp = Receive ? Packet->SourceIpv4 : Packet->DestinationIpv4;
    const uint16_t remotePort = Receive ? Packet->SourcePort : Packet->DestinationPort;
    const uint32_t target = remotePort == 80 ? (uint32_t)(packed >> 32) :
        remotePort == 443 ? (uint32_t)packed : 0;
    return target != 0 && remoteIp == target;
}

void Mt7922WriteStationPacketTrace(const MT7922_STATION_TCP_HEADER *Packet,
    MT7922_STATION_TRACE_STAGE Stage, int32_t Status)
{
    TraceLoggingWrite(StationPacketProvider, "StationPacket",
        TraceLoggingLevel(STATION_PACKET_TRACE_LEVEL),
        TraceLoggingUInt8((uint8_t)Stage, "Stage"),
        TraceLoggingHexInt32(Status, "Status"),
        TraceLoggingHexUInt32(Packet->SourceIpv4, "SourceIpv4"),
        TraceLoggingHexUInt32(Packet->DestinationIpv4, "DestinationIpv4"),
        TraceLoggingUInt16(Packet->SourcePort, "SourcePort"),
        TraceLoggingUInt16(Packet->DestinationPort, "DestinationPort"),
        TraceLoggingUInt16(Packet->Ipv4Id, "Ipv4Id"),
        TraceLoggingUInt32(Packet->Sequence, "TcpSequence"),
        TraceLoggingUInt32(Packet->Acknowledgment, "TcpAcknowledgment"),
        TraceLoggingHexUInt8(Packet->Flags, "TcpFlags"),
        TraceLoggingUInt16(Packet->PayloadBytes, "TcpPayloadBytes"),
        TraceLoggingUInt16(Packet->WlanSequence, "WlanSequence"));
}

void Mt7922TraceStationFrame(const uint8_t *Frame, size_t Bytes, bool Receive,
    MT7922_STATION_TRACE_STAGE Stage)
{
    MT7922_STATION_TCP_HEADER packet;
    if (Mt7922MatchStationPacketTrace(Frame, Bytes, Receive, &packet)) {
        Mt7922WriteStationPacketTrace(&packet, Stage, 0);
    }
}
