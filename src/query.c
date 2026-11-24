#include <windows.h>
#include <winioctl.h>
#include <cfgmgr32.h>
#include <wlanapi.h>
#include <objbase.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <initguid.h>
#include "probe.h"

static void PrintAwdlChannel(const MT7922_AWDL_RX_COUNTS *Counts)
{
    if (Counts->Channel != 0) {
        printf("Channel %lu: received=%lu invalid=%lu AWDL PSF=%lu MIF=%lu iOS=%lu\n",
            Counts->Channel, Counts->Descriptors, Counts->Invalid, Counts->Psf, Counts->Mif,
            Counts->Ios);
    }
}

static int HexDigit(wchar_t digit)
{
    if (digit >= L'0' && digit <= L'9') {
        return digit - L'0';
    }
    if (digit >= L'A' && digit <= L'F') {
        return digit - L'A' + 10;
    }
    if (digit >= L'a' && digit <= L'f') {
        return digit - L'a' + 10;
    }
    return -1;
}

static BOOL ParsePeerMac(const wchar_t *text, UCHAR *address)
{
    if (wcslen(text) != 3U * MT7922_AWDL_PEER_BYTES - 1U) {
        return FALSE;
    }
    UCHAR combined = 0;
    for (ULONG i = 0; i < MT7922_AWDL_PEER_BYTES; ++i) {
        const int high = HexDigit(text[i * 3]);
        const int low = HexDigit(text[i * 3 + 1]);
        if (high < 0 || low < 0 || (i + 1 < MT7922_AWDL_PEER_BYTES && text[i * 3 + 2] != L':')) {
            return FALSE;
        }
        address[i] = (UCHAR)((high << 4) | low);
        combined |= address[i];
    }
    return combined != 0 && (address[0] & 1U) == 0;
}

static int ProbeMcu(HANDLE device, const wchar_t *imagePath, DWORD request,
    const UCHAR *targetPeer, USHORT tcpPort)
{
    const BOOL watching = request == IOCTL_MT7922_AWDL_WATCH_PAIR;
    MT7922_AWDL_WATCH_INFO watch = {0};
    MT7922_MCU_PROBE_INFO info = {0};
    DWORD bytesRead;
    PUCHAR image = NULL;
    DWORD imageBytes = 0;
    if (imagePath != NULL) {
        HANDLE file = CreateFileW(imagePath, GENERIC_READ, FILE_SHARE_READ, NULL,
            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
        if (file == INVALID_HANDLE_VALUE) {
            fprintf(stderr, "Could not open the firmware image (Windows error %lu).\n", GetLastError());
            return 7;
        }

        LARGE_INTEGER size;
        if (!GetFileSizeEx(file, &size) || (size.QuadPart != MT7922_FACTORY_IMAGE_BYTES &&
            size.QuadPart != MT7922_REFERENCE_IMAGE_BYTES)) {
            CloseHandle(file);
            fputs("The firmware image size does not match an approved payload.\n", stderr);
            return 7;
        }
        DWORD expectedBytes = (DWORD)size.QuadPart;
        const DWORD targetBytes = watching ? MT7922_AWDL_WATCH_PEER_BYTES :
            tcpPort != 0 ? MT7922_AWDL_TCP_TARGET_BYTES :
            targetPeer != NULL ? MT7922_AWDL_PEER_BYTES : 0;
        image = malloc(expectedBytes + targetBytes);
        BOOL read = image != NULL && ReadFile(file, image, expectedBytes, &imageBytes, NULL);
        CloseHandle(file);
        if (!read || imageBytes != expectedBytes) {
            free(image);
            fputs("Could not read the complete firmware image.\n", stderr);
            return 7;
        }
        if (targetPeer != NULL) {
            const DWORD peerBytes = watching ? MT7922_AWDL_WATCH_PEER_BYTES : MT7922_AWDL_PEER_BYTES;
            memcpy(image + imageBytes, targetPeer, peerBytes);
            imageBytes += peerBytes;
        }
        if (tcpPort != 0) {
            image[imageBytes++] = (UCHAR)(tcpPort >> 8);
            image[imageBytes++] = (UCHAR)tcpPort;
        }
    }

    void *output = watching ? (void *)&watch : (void *)&info;
    const DWORD outputBytes = watching ? sizeof(watch) : sizeof(info);
    BOOL completed = DeviceIoControl(device, request, image, imageBytes, output,
        outputBytes, &bytesRead, NULL);
    DWORD error = GetLastError();
    free(image);
    if (!completed) {
        fprintf(stderr, "Could not request the MCU operation (Windows error %lu).\n", error);
        return 7;
    }
    if (watching) {
        if (bytesRead != sizeof(watch)) {
            fputs("The driver returned an unexpected passive watch report.\n", stderr);
            return 7;
        }
        printf("Passive watch: stage=%lu status=0x%08lx cleanup=0x%08lx\n",
            watch.Stage, watch.Status, watch.CleanupStatus);
        for (ULONG i = 0; i < 2; ++i) {
            const UCHAR *mac = targetPeer + i * MT7922_AWDL_PEER_BYTES;
            const MT7922_AWDL_WATCH_DIRECTION *from = &watch.From[i];
            printf("Watch %02x:%02x:%02x:%02x:%02x:%02x: sync=%lu data-to-other-peer=%lu IPv6=%lu "
                "TCP SYN=%lu SYN-ACK=%lu data-segments=%lu\n",
                mac[0], mac[1], mac[2], mac[3], mac[4], mac[5], from->SyncFrames, from->DataFrames,
                from->Ipv6Packets, from->TcpSyn, from->TcpSynAck, from->TcpPayload);
        }
        return watch.Status == 0 && watch.CleanupStatus == 0 &&
            watch.Stage == MT7922_MCU_COMPLETE ? 0 : 8;
    }
    if (bytesRead != sizeof(info) && bytesRead != MT7922_MCU_PROBE_V28_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V27_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V26_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V25_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V24_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V23_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V22_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V21_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V20_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V19_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V18_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V17_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V16_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V15_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V14_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V13_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V12_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V11_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V10_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V9_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V8_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V7_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V6_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V5_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V4_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V3_BYTES &&
        bytesRead != MT7922_MCU_PROBE_V2_BYTES && bytesRead != MT7922_MCU_PROBE_V1_BYTES) {
        fputs("The MCU probe response has an unexpected size.\n", stderr);
        return 7;
    }
    printf("MCU probe stage/status: %lu / 0x%08lx\n"
        "MCU cleanup status: 0x%08lx\n"
        "Wireless reset / boot ROM: 0x%08lx / 0x%08lx\n"
        "DMA config: 0x%08lx, TX/RX DMA index: %lu / %lu\n"
        "MCU event ID / sequence: %lu / %lu\n"
        "Patch semaphore get/release: 0x%08lx / 0x%08lx\n"
        "Blocked register/value: 0x%08lx / 0x%08lx\n"
        "Firmware download: %lu packets, %lu bytes, DMA index %lu\n"
        "Last command: 0x%02lx, firmware state after start: 0x%08lx\n"
        "Runtime RX DMA index: %lu, NIC bands: 0x%lx, spatial streams: %lu\n"
        "NIC bandwidth code: %lu, PHY flags: 0x%lx\n",
        info.Stage, info.Status, info.CleanupStatus, info.ResetRegister,
        info.BootRomState, info.DmaConfig, info.TxDmaIndex, info.RxDmaIndex,
        info.EventId, info.EventSequence, info.GetReply, info.ReleaseReply,
        info.BlockedRegister, info.BlockedValue, info.DownloadPackets, info.DownloadBytes,
        info.DownloadDmaIndex, info.LastCommand, info.FirmwareReady,
        info.RuntimeRxDmaIndex, info.NicBands, info.NicSpatialStreams,
        info.NicBandwidthCode, info.NicPhyFlags);
    if (bytesRead >= MT7922_MCU_PROBE_V2_BYTES) {
        printf("Last TX ring / host index / descriptor control: 0x%08lx / %lu / 0x%08lx\n"
            "WFDMA IRQ status / extension / scheduler / PCIe MAC power: "
            "0x%08lx / 0x%08lx / 0x%08lx / 0x%08lx\n",
            info.LastTxRing, info.LastTxHostIndex, info.LastTxDescriptorControl,
            info.DmaInterruptStatus, info.DmaGlobalExtension, info.DmaScheduler, info.PcieMacPower);
    }
    if (bytesRead >= MT7922_MCU_PROBE_V3_BYTES) {
        printf("ROM result command / status / response bytes: 0x%02lx / 0x%08lx / %lu\n"
            "Firmware ready via remap: 0x%08lx, download descriptors marked done: %lu\n",
            info.RomResultCommand, info.RomResultStatus, info.RomResultBytes,
            info.FirmwareReadyRemapped, info.DownloadDonePackets);
    }
    const BOOL transmit = request == IOCTL_MT7922_TRANSMIT_AWDL ||
        request == IOCTL_MT7922_TRANSMIT_AWDL_44 || request == IOCTL_MT7922_TRANSMIT_AWDL_149 ||
        request == IOCTL_MT7922_TRANSMIT_AWDL_149_WIDE;
    if ((request == IOCTL_MT7922_RECEIVE_AWDL || transmit) &&
        bytesRead >= MT7922_MCU_PROBE_V4_BYTES) {
        printf("Monitor command status: 0x%08lx, data RX DMA index: %lu\n",
            info.MonitorReply, info.DataRxDmaIndex);
        printf("NIC MAC: %02x:%02x:%02x:%02x:%02x:%02x\n", info.NicMac[0],
            info.NicMac[1], info.NicMac[2], info.NicMac[3], info.NicMac[4],
            info.NicMac[5]);
        for (ULONG i = 0; i < MT7922_AWDL_PROBE_CHANNELS; ++i) {
            PrintAwdlChannel(&info.Capture[i]);
        }
        if (!transmit && bytesRead >= MT7922_MCU_PROBE_V22_BYTES) {
            if (info.Capture149.Channel != 0 && info.Capture149Status != 0) {
                printf("Channel 149 configuration: status=0x%08lx firmware reply=0x%08lx\n",
                    info.Capture149Status, info.Capture149FirmwareReply);
            }
            PrintAwdlChannel(&info.Capture149);
            printf("iOS AWDL peers observed: %lu\n", info.SurveyPeerCount);
            for (ULONG i = 0; i < info.SurveyPeerCount && i < MT7922_AWDL_SURVEY_PEERS; ++i) {
                const MT7922_AWDL_SURVEY_PEER *peer = &info.SurveyPeers[i];
                printf("Peer %02x:%02x:%02x:%02x:%02x:%02x heard 6=%lu 44=%lu 149=%lu "
                    "last heard on=%u advertised next=%u slots:", peer->Address[0], peer->Address[1],
                    peer->Address[2], peer->Address[3], peer->Address[4], peer->Address[5],
                    peer->Received[0], peer->Received[1], peer->Received[2],
                    (unsigned)peer->LastChannel, (unsigned)peer->NextChannel);
                if (peer->SlotCount == 0) {
                    fputs(" unavailable", stdout);
                }
                for (ULONG slot = 0; slot < peer->SlotCount && slot < MT7922_AWDL_SURVEY_SLOTS; ++slot) {
                    printf(" %u", (unsigned)peer->Slots[slot]);
                }
                putchar('\n');
            }
        }
    }
    if (transmit && bytesRead >= MT7922_MCU_PROBE_V5_BYTES) {
        printf("iOS peer observed: %lu, data TX DMA index: %lu\n"
            "TX status seen: %lu, ACK error bits: 0x%lx, rate: 0x%lx\n"
            "TX buffer released: %lu, release status: %lu, attempts: %lu\n",
            info.TxPeerFound, info.DataTxDmaIndex, info.TxReportSeen,
            info.TxReportAckErrors, info.TxReportRate, info.TxReleaseSeen,
            info.TxReleaseStatus, info.TxAttempts);
    }
    if (transmit && bytesRead >= MT7922_MCU_PROBE_V18_BYTES) {
        for (ULONG i = 0; i < info.TxProbes && i < MT7922_AWDL_TX_MAX_ATTEMPTS; ++i) {
            const MT7922_TX_ATTEMPT_INFO *attempt = &info.TxProbe[i];
            printf("Probe %lu: status=0x%08lx TX status records=%lu matched=%lu ACK errors=0x%lx "
                "rate=0x%lx release=%lu result=%lu attempts=%lu\n", i + 1, attempt->Status,
                attempt->TxStatusRecords, attempt->ReportSeen, attempt->AckErrors,
                attempt->ReportRate, attempt->ReleaseSeen, attempt->ReleaseStatus, attempt->RetryCount);
        }
        printf("RX record types after TX:");
        for (ULONG i = 0; i < MT7922_TX_RX_TYPE_BINS; ++i) {
            if (info.TxRxTypeCount[i] != 0)
                printf(" %lu:%lu", i, info.TxRxTypeCount[i]);
        }
        putchar('\n');
        if (info.TxStatusSampleBytes != 0) {
            printf("TX status sample: %lu bytes\n", info.TxStatusSampleBytes);
            for (ULONG i = 0; i < MT7922_TX_SAMPLE_WORDS; ++i) {
                printf("  %08lx", info.TxStatusSample[i]);
                if (i % 8 == 7 || i + 1 == MT7922_TX_SAMPLE_WORDS)
                    putchar('\n');
            }
        }
        if (info.TxReleaseSampleBytes != 0) {
            printf("TX release sample: %lu bytes\n", info.TxReleaseSampleBytes);
            for (ULONG i = 0; i < MT7922_TX_SAMPLE_WORDS; ++i) {
                printf("  %08lx", info.TxReleaseSample[i]);
                if (i % 8 == 7 || i + 1 == MT7922_TX_SAMPLE_WORDS)
                    putchar('\n');
            }
        }
        printf("Sync broadcast: PSF=%lu MIF=%lu released=%lu failed=%lu peer frames=%lu, "
            "last TSF=%lu, TU to slot=%lu\n", info.SyncPsfSent, info.SyncMifSent,
            info.SyncReleased, info.SyncFailed, info.SyncPeerFrames,
            info.SyncLastTsfUs, info.SyncLastNextSlotTu);
        printf("Data probe: sent=%lu acked=%lu failed=%lu, last ACK errors=0x%lx "
            "release=%lu attempts=%lu, peer=%02x:%02x:%02x:%02x:%02x:%02x\n",
            info.DataProbeSent, info.DataProbeAcked, info.DataProbeFailed,
            info.DataProbeAckErrors, info.DataProbeReleaseStatus, info.DataProbeRetries,
            info.DataPeer[0], info.DataPeer[1], info.DataPeer[2],
            info.DataPeer[3], info.DataPeer[4], info.DataPeer[5]);
        printf("Mgmt probe: sent=%lu acked=%lu failed=%lu, last ACK errors=0x%lx "
            "release=%lu attempts=%lu\n", info.MgmtProbeSent, info.MgmtProbeAcked,
            info.MgmtProbeFailed, info.MgmtProbeAckErrors, info.MgmtProbeReleaseStatus,
            info.MgmtProbeRetries);
        printf("Peer observations: self frames=%lu, addressed to us=%lu, mentions us=%lu, "
            "control frames=%lu, ACKs to us=%lu\n", info.SelfFrames, info.PeerAddressed,
            info.PeerMentions, info.ControlFrames, info.AcksToUs);
        static const ULONG offsets[] = {6, 22, 38, 54};
        printf("Probe windows missed: %lu, unicast release timeouts: %u\n",
            info.ProbeWindowsMissed, (unsigned)info.UnicastReleaseTimeouts);
        printf("Data probe by offset:");
        for (ULONG k = 0; k < 4; ++k) {
            ULONG total = 0, acked = 0;
            for (ULONG i = 0; i < info.DataProbeSent && i < MT7922_AWDL_DATA_PROBES; ++i) {
                if (info.DataProbeOffset[i] == offsets[k]) {
                    ++total;
                    acked += info.DataProbeResult[i];
                }
            }
            printf(" %luTU:%lu/%lu", offsets[k], acked, total);
        }
        putchar('\n');
        printf("Ping: sent=%lu replies=%lu, last reply TTL=%lu\n",
            info.PingSent, info.PingReplies, info.PingLastTtl);
        printf("ADDBA: requests=%lu responses=%lu acked=%lu\n",
            info.AddbaRequests, info.AddbaResponses, info.AddbaAcked);
        if (bytesRead >= MT7922_MCU_PROBE_V19_BYTES) {
            printf("RX Block Ack configuration: attempted=%lu configured=%lu last status=0x%08lx "
                "firmware reply=0x%08lx\n", info.RxBaAttempts, info.RxBaConfigured,
                info.RxBaLastStatus, info.RxBaFirmwareReply);
        }
        if (bytesRead >= MT7922_MCU_PROBE_V20_BYTES) {
            printf("AirDrop mDNS: queries=%lu released=%lu peer responses=%lu service answers=%lu\n",
                info.MdnsQueriesSent, info.MdnsQueriesReleased, info.MdnsResponses, info.MdnsAirDropAnswers);
        }
        if (bytesRead >= MT7922_MCU_PROBE_V21_BYTES) {
            printf("AirDrop mDNS direct: sent=%lu acked=%lu, other peer responses=%lu "
                "other service answers=%lu, selected peer IPv6 frames=%lu\n",
                info.MdnsDirectQueries, info.MdnsDirectAcked, info.MdnsOtherResponses,
                info.MdnsOtherAirDropAnswers, info.PeerIpv6Frames);
        }
        if (bytesRead >= MT7922_MCU_PROBE_V23_BYTES) {
            printf("AWDL RX data: selected=%lu other=%lu to us=%lu, normal RX skipped=%lu\n",
                info.AwdlRxDataSelected, info.AwdlRxDataOther, info.AwdlRxDataToUs, info.NormalRxSkipped);
        }
        if (bytesRead >= MT7922_MCU_PROBE_V24_BYTES) {
            printf("RX skip flags: FCS=%lu translated=%lu A-MSDU error=%lu",
                info.NormalRxFcsErrors, info.NormalRxTranslated, info.NormalRxAmsduErrors);
            if (bytesRead >= MT7922_MCU_PROBE_V25_BYTES) {
                printf(" length=%lu groups=%lu", info.NormalRxBadLength, info.NormalRxBadGroups);
            }
            putchar('\n');
        }
        if (bytesRead == sizeof(info) && info.TcpPort != 0) {
            printf("AirDrop TCP: port=%lu SYN=%lu SYN-ACK=%lu refused=%lu reset-sent=%lu "
                "session-service=%lu session-ping=%lu\n",
                info.TcpPort, info.TcpSynSent, info.TcpSynAck, info.TcpRefused, info.TcpResetSent,
                info.MdnsAirDropAnswers, info.PingReplies);
        }
        if (bytesRead >= MT7922_MCU_PROBE_V28_BYTES) {
            printf("AirDrop mDNS current-window multicast: sent=%lu\n", info.MdnsCurrentWindowQueries);
        }
        if (bytesRead >= MT7922_MCU_PROBE_V27_BYTES) {
            for (ULONG i = 0; i < info.MdnsSampleCount && i < MT7922_AWDL_MDNS_SAMPLES; ++i) {
                const MT7922_AWDL_MDNS_SAMPLE *sample = &info.MdnsSamples[i];
                if (sample->Length == 0 || sample->Length > sizeof(sample->Bytes)) {
                    continue;
                }
                fputs("AirDrop DNS packet: source=", stdout);
                for (ULONG j = 0; j < sizeof(sample->SourceIpv6); ++j) {
                    printf("%02x", sample->SourceIpv6[j]);
                }
                fputs(" data=", stdout);
                for (ULONG j = 0; j < sample->Length; ++j) {
                    printf("%02x", sample->Bytes[j]);
                }
                putchar('\n');
            }
        }
        if (bytesRead >= MT7922_MCU_PROBE_V26_BYTES) {
            printf("AWDL IPv6: wrapped=%lu to Windows=%lu, raw RX recovered=%lu\n",
                info.WrappedIpv6Packets, info.Ipv6PacketsToUs, info.NormalRxRawTranslationError);
        }
        if (bytesRead >= MT7922_MCU_PROBE_V25_BYTES) {
            printf("AWDL RX hardware-split A-MSDU: %lu\n", info.AwdlRxDataSplit);
            for (ULONG i = 0; i < sizeof(info.AwdlRxHeaders) / sizeof(info.AwdlRxHeaders[0]); ++i) {
                const MT7922_AWDL_RX_HEADER *sample = &info.AwdlRxHeaders[i];
                if (sample->SampleBytes == 0) {
                    continue;
                }
                printf("AWDL RX header: length=%lu A-MSDU format=%lu", sample->Length, sample->AmsduPart);
                for (ULONG j = 0; j < sample->SampleBytes && j < sizeof(sample->Bytes); ++j) {
                    printf(" %02x", sample->Bytes[j]);
                }
                putchar('\n');
            }
            if (info.FirstSkippedRxBytes != 0) {
                printf("RX skip sample: DMA bytes=%lu words", info.FirstSkippedRxBytes);
                for (ULONG i = 0; i < sizeof(info.FirstSkippedRx) / sizeof(info.FirstSkippedRx[0]); ++i) {
                    printf(" %08lx", info.FirstSkippedRx[i]);
                }
                putchar('\n');
            }
        }
        printf("iOS peers: seen=%lu changes=%lu:", info.PeerCount, info.DataPeerChanges);
        for (ULONG i = 0; i < info.PeerCount && i < 4; ++i) {
            printf(" %02x:%02x:%02x:%02x:%02x:%02x=%lu", info.PeerAddr[i][0],
                info.PeerAddr[i][1], info.PeerAddr[i][2], info.PeerAddr[i][3],
                info.PeerAddr[i][4], info.PeerAddr[i][5], info.PeerFrames[i]);
        }
        printf(", ADDBA peer=%02x:%02x:%02x:%02x:%02x:%02x\n", info.AddbaPeerAddr[0],
            info.AddbaPeerAddr[1], info.AddbaPeerAddr[2], info.AddbaPeerAddr[3],
            info.AddbaPeerAddr[4], info.AddbaPeerAddr[5]);
        for (ULONG i = 0; i < info.DataProbeSent && i < MT7922_AWDL_DATA_PROBES; ++i) {
            printf("Probe #%lu off=%luTU plan=%luus act=%luus peer-ago=%luus %s%s\n",
                i + 1, info.DataProbeOffset[i], info.ProbePlanUs[i],
                info.ProbeSubmitUs[i],
                info.ProbePeerDeltaUs[i] == 0xffffffffUL ? 0 : info.ProbePeerDeltaUs[i],
                info.DataProbeResult[i] ? "ack" : "noack",
                bytesRead == sizeof(info) && (info.TcpProbeMask & (1U << i)) != 0 ? " TCP" :
                    bytesRead < MT7922_MCU_PROBE_V21_BYTES ? "" :
                    (info.MdnsDirectProbeMask & (1U << i)) != 0 ? " mDNS" : " Ping");
        }
        printf("Peer QoS frames=%lu\n", info.PeerQos);
        if (info.PeerQosSampleBytes != 0) {
            printf("Peer QoS sample:");
            for (ULONG b = 0; b < info.PeerQosSampleBytes && b < 64; ++b) {
                printf(" %02x", info.PeerQosSample[b]);
            }
            putchar('\n');
        }
        for (ULONG i = 0; i < info.PeerSamples && i < 3; ++i) {
            printf("Peer sample %lu:", i + 1);
            for (ULONG b = 0; b < 48; ++b) {
                printf(" %02x", info.PeerSample[i][b]);
            }
            putchar('\n');
        }
    }
    return info.Status == 0 && info.CleanupStatus == 0 &&
        info.Stage == MT7922_MCU_COMPLETE ? 0 : 8;
}

static BOOL ConfirmAssociatedBand(HANDLE Wlan, const GUID *Interface, ULONG Channel)
{
    PVOID data = NULL;
    DWORD bytes = 0;
    BOOL confirmed = FALSE;
    const DWORD error = WlanQueryInterface(Wlan, Interface, wlan_intf_opcode_current_connection,
        NULL, &bytes, &data, NULL);
    if (error == ERROR_SUCCESS && bytes >= sizeof(WLAN_CONNECTION_ATTRIBUTES)) {
        const WLAN_CONNECTION_ATTRIBUTES *connection = data;
        if (connection->isState == wlan_interface_state_connected) {
            const DOT11_PHY_TYPE phy = connection->wlanAssociationAttributes.dot11PhyType;
            /* The legacy PHY types establish the band even without link quality.
               HT/HE can operate in more than one of the relevant bands. */
            confirmed = Channel == 6 ? (phy == dot11_phy_type_dsss || phy == dot11_phy_type_hrdsss ||
                phy == dot11_phy_type_erp) : (phy == dot11_phy_type_ofdm || phy == dot11_phy_type_vht);
        }
    }
    if (data != NULL) {
        WlanFreeMemory(data);
    }
    return confirmed;
}

static int ReadSharedWifi(const wchar_t *InterfaceGuid)
{
    GUID guid;
    if (FAILED(CLSIDFromString(InterfaceGuid, &guid))) {
        fputs("Expected the MT7922 WLAN interface GUID.\n", stderr);
        return 2;
    }

    HANDLE wlan = NULL;
    DWORD version = 0;
    DWORD error = WlanOpenHandle(2, NULL, &version, &wlan);
    if (error != ERROR_SUCCESS) {
        fprintf(stderr, "Could not open Windows WLAN (error %lu).\n", error);
        return 3;
    }

    int result = 3;
    PVOID data = NULL;
    DWORD size = 0;
    error = WlanQueryInterface(wlan, &guid, wlan_intf_opcode_interface_state, NULL, &size, &data, NULL);
    if (error != ERROR_SUCCESS || size < sizeof(WLAN_INTERFACE_STATE)) {
        fprintf(stderr, "Could not read the MT7922 connection state (error %lu).\n", error);
        goto done;
    }
    if (*(const WLAN_INTERFACE_STATE *)data != wlan_interface_state_connected) {
        puts("MT7922 WLAN is not connected to an access point.");
        result = 4;
        goto done;
    }

    WlanFreeMemory(data);
    data = NULL;
    error = WlanQueryInterface(wlan, &guid, wlan_intf_opcode_channel_number, NULL, &size, &data, NULL);
    if (error != ERROR_SUCCESS || size < sizeof(ULONG)) {
        fprintf(stderr, "Could not read the connected access point's primary channel (error %lu).\n", error);
        goto done;
    }
    const ULONG channel = *(const ULONG *)data;
    const ULONG expectedMhz = channel == 6 ? 2437 : channel == 44 ? 5220 : channel == 149 ? 5745 : 0;
    if (expectedMhz == 0) {
        printf("MT7922 connected: primary-channel=%lu\n", channel);
        puts("The AP channel is outside the current shared-mode profile (6, 44, or 149).");
        result = 4;
        goto done;
    }

    WlanFreeMemory(data);
    data = NULL;
    error = WlanQueryInterface(wlan, &guid, wlan_intf_opcode_realtime_connection_quality,
        NULL, &size, &data, NULL);
    ULONG frequency;
    if (error == ERROR_SUCCESS && size >= sizeof(WLAN_REALTIME_CONNECTION_QUALITY)) {
        const WLAN_REALTIME_CONNECTION_QUALITY *quality = data;
        if (quality->bIsMLOConnection || quality->ulNumLinks != 1) {
            fputs("The Wi-Fi connection does not have one confirmed radio link.\n", stderr);
            goto done;
        }
        /* WLAN reports kilohertz despite the field name. */
        frequency = quality->linksInfo[0].ulChannelCenterFrequencyMhz / 1000UL;
    } else {
        if (!ConfirmAssociatedBand(wlan, &guid, channel)) {
            fprintf(stderr, "MT7922 channel %lu: unable to confirm the Wi-Fi band (error %lu).\n", channel, error);
            goto done;
        }
        frequency = expectedMhz;
    }
    printf("MT7922 connected: primary-channel=%lu frequency=%lu MHz\n", channel, frequency);
    /* Owly shares the station's channel and does not retune the station. */
    if ((channel == 6 && frequency >= 2400 && frequency < 2500) ||
        ((channel == 44 || channel == 149) && frequency >= 5000 && frequency < 5925)) {
        puts("The AP channel meets Owly's same-channel prerequisite.");
        result = 0;
    } else {
        puts("The AP channel is outside the current shared-mode profile.");
        result = 4;
    }

done:
    if (data != NULL) {
        WlanFreeMemory(data);
    }
    WlanCloseHandle(wlan, NULL);
    return result;
}

int wmain(int argc, wchar_t **argv)
{
    if (argc == 3 && wcscmp(argv[1], L"--shared-wifi-status") == 0) {
        return ReadSharedWifi(argv[2]);
    }
    BOOL requireRegisters = argc == 2 && wcscmp(argv[1], L"--require-registers") == 0;
    BOOL requireFirmware = argc == 2 && wcscmp(argv[1], L"--require-firmware-probe") == 0;
    BOOL requireDma = argc == 2 && wcscmp(argv[1], L"--require-dma-buffer") == 0;
    BOOL requireKernelPath = argc == 2 && wcscmp(argv[1], L"--require-kernel-path") == 0;
    BOOL probeMcu = argc == 2 && wcscmp(argv[1], L"--probe-mcu") == 0;
    BOOL bootFirmware = argc == 3 && wcscmp(argv[1], L"--boot-firmware") == 0;
    BOOL receiveAwdl = argc == 3 && wcscmp(argv[1], L"--receive-awdl") == 0;
    const BOOL watchPair = argc == 5 && wcscmp(argv[1], L"--watch-awdl-pair") == 0;
    const BOOL transmitArgs = argc >= 3 && argc <= 5;
    const BOOL wide80 = transmitArgs && wcscmp(argv[1], L"--transmit-awdl-149-wide80") == 0;
    const unsigned transmitChannel = !transmitArgs ? 0 :
        wide80 || wcscmp(argv[1], L"--transmit-awdl-149") == 0 ? 149 :
        wcscmp(argv[1], L"--transmit-awdl-44") == 0 ? 44 :
        wcscmp(argv[1], L"--transmit-awdl") == 0 ? 6 : 0;
    const BOOL transmitAwdl = transmitChannel != 0;
    if (argc != 1 && !requireRegisters && !requireFirmware && !requireDma &&
        !requireKernelPath && !probeMcu && !bootFirmware && !receiveAwdl && !transmitAwdl && !watchPair) {
        fputs("Usage: mt7922-query [--require-registers|--require-firmware-probe|"
            "--require-dma-buffer|--require-kernel-path|--probe-mcu|--boot-firmware IMAGE|--receive-awdl IMAGE|--transmit-awdl IMAGE [PEER]|--transmit-awdl-44 IMAGE [PEER]|"
            "--transmit-awdl-149 IMAGE [PEER]|--transmit-awdl-149-wide80 IMAGE [PEER]] [TCP_PORT]\n"
            "       mt7922-query --watch-awdl-pair IMAGE PEER_A PEER_B\n", stderr);
        return 1;
    }

    UCHAR targetAddress[MT7922_AWDL_WATCH_PEER_BYTES];
    const UCHAR *targetPeer = NULL;
    USHORT tcpPort = 0;
    if (watchPair) {
        UCHAR *other = targetAddress + MT7922_AWDL_PEER_BYTES;
        if (!ParsePeerMac(argv[3], targetAddress) || !ParsePeerMac(argv[4], other) ||
            memcmp(targetAddress, other, MT7922_AWDL_PEER_BYTES) == 0) {
            fputs("Supply two distinct unicast MAC addresses for the test devices.\n", stderr);
            return 1;
        }
        targetPeer = targetAddress;
    }
    if (transmitAwdl && argc >= 4) {
        if (!ParsePeerMac(argv[3], targetAddress)) {
            fputs("PEER must be a nonzero unicast MAC address in xx:xx:xx:xx:xx:xx form.\n", stderr);
            return 1;
        }
        targetPeer = targetAddress;
        if (argc == 5) {
            const unsigned long port = wcstoul(argv[4], NULL, 10);
            if (port == 0 || port > 65535 || wcsspn(argv[4], L"0123456789") != wcslen(argv[4])) {
                fputs("TCP_PORT must be a decimal port from 1 to 65535.\n", stderr);
                return 1;
            }
            tcpPort = (USHORT)port;
        }
    }

    MT7922_PROBE_INFO info;
    ULONG size;
    CONFIGRET result = CM_Get_Device_Interface_List_SizeW(&size,
        (LPGUID)&GUID_DEVINTERFACE_MT7922_PROBE, NULL, CM_GET_DEVICE_INTERFACE_LIST_PRESENT);
    if (result != CR_SUCCESS) {
        fprintf(stderr, "Could not find the probe interface (CONFIGRET 0x%lx).\n", result);
        return 1;
    }
    if (size <= 1) {
        fputs("The MT7922 probe driver is not active.\n", stderr);
        return 2;
    }

    WCHAR *paths = calloc(size, sizeof(*paths));
    if (paths == NULL) {
        fputs("Could not allocate the interface list.\n", stderr);
        return 1;
    }
    result = CM_Get_Device_Interface_ListW((LPGUID)&GUID_DEVINTERFACE_MT7922_PROBE,
        NULL, paths, size, CM_GET_DEVICE_INTERFACE_LIST_PRESENT);
    if (result != CR_SUCCESS) {
        fprintf(stderr, "Could not read the interface list (CONFIGRET 0x%lx).\n", result);
        free(paths);
        return 1;
    }
    if (*paths == L'\0') {
        free(paths);
        fputs("The MT7922 probe driver is not active.\n", stderr);
        return 2;
    }

    BOOL mcuOperation = probeMcu || bootFirmware || receiveAwdl || transmitAwdl || watchPair;
    DWORD access = mcuOperation ? GENERIC_READ | GENERIC_WRITE : GENERIC_READ;
    HANDLE device = CreateFileW(paths, access, FILE_SHARE_READ, NULL,
        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    DWORD error = GetLastError();
    free(paths);
    if (device == INVALID_HANDLE_VALUE) {
        fprintf(stderr, "Could not open the probe (Windows error %lu).\n", error);
        return 1;
    }

    if (mcuOperation) {
        DWORD request = IOCTL_MT7922_BOOT_FIRMWARE;
        if (watchPair)
            request = IOCTL_MT7922_AWDL_WATCH_PAIR;
        else if (probeMcu)
            request = IOCTL_MT7922_PROBE_MCU;
        else if (receiveAwdl)
            request = IOCTL_MT7922_RECEIVE_AWDL;
        else if (transmitAwdl)
            request = wide80 ? IOCTL_MT7922_TRANSMIT_AWDL_149_WIDE :
                transmitChannel == 149 ? IOCTL_MT7922_TRANSMIT_AWDL_149 :
                transmitChannel == 44 ? IOCTL_MT7922_TRANSMIT_AWDL_44 : IOCTL_MT7922_TRANSMIT_AWDL;
        if (watchPair) {
            printf("Passively watching channel 149 (80 MHz) for %u seconds after firmware startup. "
                "Start a transfer between the two Apple devices now.\n", MT7922_AWDL_WATCH_MS / 1000U);
            fflush(stdout);
        } else if (receiveAwdl) {
            printf("Listening for up to %u seconds on each of Wi-Fi channels 6, 44 and 149 once firmware is ready.\n",
                MT7922_AWDL_PROBE_DWELL_MS / 1000U);
            fflush(stdout);
        } else if (transmitAwdl) {
            printf("Waiting for an iOS AWDL peer on Wi-Fi channel %u (%u MHz), "
                "probing up to three broadcast frames, then advertising PSF/MIF for %u seconds.\n",
                transmitChannel, wide80 ? 80U : 20U,
                (tcpPort ? MT7922_AWDL_TCP_SYNC_MS : MT7922_AWDL_SYNC_MS) / 1000U);
            fflush(stdout);
        }
        int exitCode = ProbeMcu(device, probeMcu ? NULL : argv[2], request, targetPeer, tcpPort);
        CloseHandle(device);
        return exitCode;
    }
    DWORD bytesRead;
    if (!DeviceIoControl(device, IOCTL_MT7922_PROBE_GET_INFO, NULL, 0, &info,
            sizeof(info), &bytesRead, NULL)) {
        error = GetLastError();
        CloseHandle(device);
        fprintf(stderr, "Could not read probe information (Windows error %lu).\n", error);
        return 1;
    }
    CloseHandle(device);
    if (bytesRead != sizeof(info) || info.Version != MT7922_PROBE_VERSION ||
        info.VendorId != MT7922_PCI_VENDOR || info.DeviceId != MT7922_PCI_DEVICE) {
        fputs("The driver returned incompatible probe information.\n", stderr);
        return 1;
    }

    printf("PCI ID: %04lx:%04lx\nMemory ranges: %lu\nInterrupt resources: %lu\n",
        info.VendorId, info.DeviceId, info.MemoryRangeCount, info.InterruptCount);
    printf("Register window: %lu bytes\nRegister status: 0x%08lx\n"
        "Chip ID register: 0x%08lx\nChip revision register: 0x%08lx\n",
        info.RegisterWindowBytes, info.RegisterStatus, info.ChipId, info.ChipRevision);
    printf("Firmware probe status: 0x%08lx\n"
        "Ownership before/host/after: 0x%08lx / 0x%08lx / 0x%08lx\n"
        "Firmware state: 0x%08lx\nDMA config: 0x%08lx\n",
        info.FirmwareProbeStatus, info.OwnershipBefore, info.OwnershipWhileProbing,
        info.OwnershipAfter, info.FirmwareState, info.DmaConfig);
    printf("DMA common buffer: %lu bytes, status 0x%08lx\n",
        info.DmaBufferBytes, info.DmaBufferStatus);
    printf("PCI command: 0x%04lx\n", info.PciCommand);
    if ((requireRegisters || requireFirmware || requireKernelPath) && info.RegisterStatus != 0) {
        return 3;
    }
    if ((requireFirmware || requireKernelPath) && info.FirmwareProbeStatus != 0) {
        return 4;
    }
    if ((requireDma || requireKernelPath) && (info.DmaBufferStatus != 0 || !info.DmaBufferBytes)) {
        return 5;
    }
    const ULONG pciAccess = MT7922_PCI_COMMAND_MEMORY | MT7922_PCI_COMMAND_MASTER;
    if (requireKernelPath && (info.PciCommand & pciAccess) != pciAccess) {
        fputs("The PCI bus has not enabled memory access and bus mastering together.\n", stderr);
        return 6;
    }
    return 0;
}
