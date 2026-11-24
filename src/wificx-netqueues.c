#include <ntddk.h>
#include <wdf.h>
#include <ndis.h>
#include <netadaptercx.h>
#include <net/virtualaddress.h>
#include <net/wifi/exemptionaction.h>
#include "probe.h"
#include "mcu.h"
#include "wificx-netqueues.h"
#include "wificx-netrings.h"
#ifdef MT7922_STATION_PACKET_TRACE
#include "station-packet-trace.h"
#endif

#define STATION_RX_QUEUE_COUNT 64U
#define STATION_TX_QUEUE_COUNT 64U

typedef struct {
    MT7922_NET_QUEUES *Backend;
    NETPACKETQUEUE Handle;
    const NET_RING_COLLECTION *Rings;
    NET_EXTENSION VirtualAddress;
    NET_EXTENSION ExemptionAction;
    bool Armed;
    bool Receive;
} STATION_PACKET_QUEUE;

struct _MT7922_NET_QUEUES {
    WDFSPINLOCK Lock;
    MT7922_MCU_STATION *Station;
    MT7922_WAKE_STATION Wake;
    void *WakeContext;
    STATION_PACKET_QUEUE *Tx;
    STATION_PACKET_QUEUE *Rx;
    MT7922_STATION_BUFFER *TxFrames;
    unsigned TxHead;
    unsigned TxCount;
    MT7922_STATION_BUFFER *RxFrames;
    unsigned RxHead;
    unsigned RxCount;
    uint64_t TxGeneration;
    uint32_t EapolPosted; /* Queue callback writes; the radio worker collects under Lock. */
};

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(MT7922_NET_QUEUES, GetStationNetQueues);
WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(STATION_PACKET_QUEUE, GetStationPacketQueue);

static EVT_NET_ADAPTER_CREATE_TXQUEUE CreateStationTx;
static EVT_NET_ADAPTER_CREATE_RXQUEUE CreateStationRx;
static EVT_PACKET_QUEUE_START StartStationQueue;
static EVT_PACKET_QUEUE_STOP StopStationQueue;
static EVT_PACKET_QUEUE_ADVANCE AdvanceStationQueue;
static EVT_PACKET_QUEUE_CANCEL CancelStationQueue;
static EVT_PACKET_QUEUE_SET_NOTIFICATION_ENABLED SetStationNotification;

static void NotifyStationQueue(STATION_PACKET_QUEUE *Queue)
{
    /* Caller holds the lock that serializes notification enable/disable and queue stop. */
    if (Queue != NULL && Queue->Armed) {
        Queue->Armed = false;
        if (Queue->Receive) {
            NetRxQueueNotifyMoreReceivedPacketsAvailable(Queue->Handle);
        } else {
            NetTxQueueNotifyMoreCompletedPacketsAvailable(Queue->Handle);
        }
    }
}

static void StartStationQueue(NETPACKETQUEUE Handle)
{
    STATION_PACKET_QUEUE *queue = GetStationPacketQueue(Handle);
    MT7922_NET_QUEUES *backend = queue->Backend;
    WdfSpinLockAcquire(backend->Lock);
    queue->Armed = false;
    if (queue->Receive) {
        backend->Rx = queue;
    } else {
        backend->Tx = queue;
    }
    WdfSpinLockRelease(backend->Lock);
    backend->Wake(backend->WakeContext);
}

static void StopStationQueue(NETPACKETQUEUE Handle)
{
    STATION_PACKET_QUEUE *queue = GetStationPacketQueue(Handle);
    MT7922_NET_QUEUES *backend = queue->Backend;
    WdfSpinLockAcquire(backend->Lock);
    queue->Armed = false;
    if (queue->Receive && backend->Rx == queue) {
        backend->Rx = NULL;
        backend->RxHead = 0;
        backend->RxCount = 0;
    } else if (!queue->Receive && backend->Tx == queue) {
        backend->Tx = NULL;
        backend->TxHead = 0;
        backend->TxCount = 0;
        ++backend->TxGeneration;
    }
    WdfSpinLockRelease(backend->Lock);
}

static void AdvanceStationQueue(NETPACKETQUEUE Handle)
{
    STATION_PACKET_QUEUE *queue = GetStationPacketQueue(Handle);
    MT7922_NET_QUEUES *backend = queue->Backend;
    bool wake = false;
    WdfSpinLockAcquire(backend->Lock);
    if (queue->Receive && backend->Rx == queue) {
        /* Leave the oldest frame queued if NetAdapterCx runs out of buffers. */
        while (backend->RxCount != 0) {
            const MT7922_STATION_BUFFER *frame = &backend->RxFrames[backend->RxHead];
            if (!Mt7922PutNetRxData(queue->Rings, &queue->VirtualAddress, frame)) {
                break;
            }
#ifdef MT7922_STATION_PACKET_TRACE
            Mt7922TraceStationFrame(frame->Data, frame->Bytes, true, Mt7922TraceRxPosted);
#endif
            backend->EapolPosted += frame->Eapol;
            backend->RxHead = (backend->RxHead + 1U) % STATION_RX_QUEUE_COUNT;
            --backend->RxCount;
            wake = true;
        }
    } else if (!queue->Receive && backend->Tx == queue) {
        while (backend->TxCount < STATION_TX_QUEUE_COUNT) {
            const unsigned tail = (backend->TxHead + backend->TxCount) % STATION_TX_QUEUE_COUNT;
            MT7922_STATION_BUFFER *frame = &backend->TxFrames[tail];
            frame->Bytes = 0;
            if (!Mt7922TakeNetTxData(queue->Rings, &queue->VirtualAddress, &queue->ExemptionAction, frame)) {
                break;
            }
#ifdef MT7922_STATION_PACKET_TRACE
            Mt7922TraceStationFrame(frame->Data, frame->Bytes, false, Mt7922TraceTxQueued);
#endif
            ++backend->TxCount;
            wake = true;
        }
    }
    WdfSpinLockRelease(backend->Lock);
    if (wake) {
        backend->Wake(backend->WakeContext);
    }
}

static void CancelStationQueue(NETPACKETQUEUE Handle)
{
    STATION_PACKET_QUEUE *queue = GetStationPacketQueue(Handle);
    if (queue->Receive) {
        AdvanceStationQueue(Handle);
    }
    StopStationQueue(Handle);
    Mt7922CancelNetRing(queue->Rings, queue->Receive);
}

static void SetStationNotification(NETPACKETQUEUE Handle, BOOLEAN Enable)
{
    STATION_PACKET_QUEUE *queue = GetStationPacketQueue(Handle);
    MT7922_NET_QUEUES *backend = queue->Backend;
    WdfSpinLockAcquire(backend->Lock);
    if ((queue->Receive ? backend->Rx : backend->Tx) == queue) {
        queue->Armed = Enable != FALSE;
        const NET_RING *packets = queue->Rings->Rings[NetRingTypePacket];
        const NET_RING *fragments = queue->Rings->Rings[NetRingTypeFragment];
        if (packets->NextIndex != packets->EndIndex &&
            (queue->Receive ? backend->RxCount != 0 && fragments->NextIndex != fragments->EndIndex
                : backend->TxCount < STATION_TX_QUEUE_COUNT)) {
            NotifyStationQueue(queue);
        }
    }
    WdfSpinLockRelease(backend->Lock);
}

static NTSTATUS InitializeStationQueue(NETADAPTER Adapter, void *Init, bool Receive)
{
    MT7922_NET_QUEUES *backend = GetStationNetQueues(Adapter);
    if (backend == NULL || backend->Lock == NULL) {
        return STATUS_INVALID_DEVICE_STATE;
    }
    NET_PACKET_QUEUE_CONFIG config;
    NET_PACKET_QUEUE_CONFIG_INIT(&config, AdvanceStationQueue, SetStationNotification, CancelStationQueue);
    config.EvtStart = StartStationQueue;
    config.EvtStop = StopStationQueue;
    WDF_OBJECT_ATTRIBUTES attributes;
    WDF_OBJECT_ATTRIBUTES_INIT_CONTEXT_TYPE(&attributes, STATION_PACKET_QUEUE);
    NETPACKETQUEUE handle;
    NTSTATUS status = Receive ? NetRxQueueCreate(Init, &attributes, &config, &handle) :
        NetTxQueueCreate(Init, &attributes, &config, &handle);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    STATION_PACKET_QUEUE *queue = GetStationPacketQueue(handle);
    queue->Handle = handle;
    queue->Backend = backend;
    queue->Receive = Receive;
    NET_EXTENSION_QUERY extension;
    NET_EXTENSION_QUERY_INIT(&extension, NET_FRAGMENT_EXTENSION_VIRTUAL_ADDRESS_NAME,
        NET_FRAGMENT_EXTENSION_VIRTUAL_ADDRESS_VERSION_1, NetExtensionTypeFragment);
    if (Receive) {
        queue->Rings = NetRxQueueGetRingCollection(handle);
        NetRxQueueGetExtension(handle, &extension, &queue->VirtualAddress);
    } else {
        queue->Rings = NetTxQueueGetRingCollection(handle);
        NetTxQueueGetExtension(handle, &extension, &queue->VirtualAddress);
        NET_EXTENSION_QUERY_INIT(&extension, NET_PACKET_EXTENSION_WIFI_EXEMPTION_ACTION_NAME,
            NET_PACKET_EXTENSION_WIFI_EXEMPTION_ACTION_VERSION_1, NetExtensionTypePacket);
        NetTxQueueGetExtension(handle, &extension, &queue->ExemptionAction);
    }
    return queue->VirtualAddress.Enabled && (Receive || queue->ExemptionAction.Enabled)
        ? STATUS_SUCCESS : STATUS_NOT_SUPPORTED;
}

static NTSTATUS CreateStationTx(NETADAPTER Adapter, NETTXQUEUE_INIT *Init)
{
    return InitializeStationQueue(Adapter, Init, false);
}

static NTSTATUS CreateStationRx(NETADAPTER Adapter, NETRXQUEUE_INIT *Init)
{
    return InitializeStationQueue(Adapter, Init, true);
}

void Mt7922SetNetAdapterQueueCallbacks(NETADAPTER_INIT *AdapterInit)
{
    NET_ADAPTER_DATAPATH_CALLBACKS callbacks;
    NET_ADAPTER_DATAPATH_CALLBACKS_INIT(&callbacks, CreateStationTx, CreateStationRx);
    NetAdapterInitSetDatapathCallbacks(AdapterInit, &callbacks);
}

NTSTATUS Mt7922AttachNetAdapterQueues(NETADAPTER Adapter, MT7922_MCU_STATION *Station,
    MT7922_WAKE_STATION Wake, void *Context, MT7922_NET_QUEUES **Backend)
{
    if (Station == NULL || Wake == NULL || Backend == NULL) {
        return STATUS_INVALID_PARAMETER;
    }
    WDF_OBJECT_ATTRIBUTES attributes;
    WDF_OBJECT_ATTRIBUTES_INIT_CONTEXT_TYPE(&attributes, MT7922_NET_QUEUES);
    MT7922_NET_QUEUES *backend;
    NTSTATUS status = WdfObjectAllocateContext(Adapter, &attributes, (void **)&backend);
    if (!NT_SUCCESS(status)) {
        return status;
    }
    WDF_OBJECT_ATTRIBUTES_INIT(&attributes);
    attributes.ParentObject = Adapter;
    WDFMEMORY queueMemory;
    void *frames;
    status = WdfMemoryCreate(&attributes, NonPagedPoolNx, 'qRxM',
        (STATION_RX_QUEUE_COUNT + STATION_TX_QUEUE_COUNT) * sizeof(MT7922_STATION_BUFFER),
        &queueMemory, &frames);
    if (!NT_SUCCESS(status)) {
        return status;
    }
    backend->RxFrames = frames;
    backend->TxFrames = backend->RxFrames + STATION_RX_QUEUE_COUNT;
    status = WdfSpinLockCreate(&attributes, &backend->Lock);
    if (NT_SUCCESS(status)) {
        backend->Station = Station;
        backend->Wake = Wake;
        backend->WakeContext = Context;
        *Backend = backend;
    }
    return status;
}

static bool StageReceivedData(void *Context, const uint8_t *Frame, size_t Bytes, bool Eapol)
{
    MT7922_NET_QUEUES *backend = Context;
    if (Bytes <= 32 || Bytes > sizeof(backend->RxFrames[0].Data)) {
        return false;
    }
    bool accepted = false;
    WdfSpinLockAcquire(backend->Lock);
    MT7922_STATION_IO_DEBUG *trace = &backend->Station->ConnectDebug.StationIo;
    if (backend->Rx != NULL && backend->RxCount < STATION_RX_QUEUE_COUNT) {
        const unsigned tail = (backend->RxHead + backend->RxCount) % STATION_RX_QUEUE_COUNT;
        MT7922_STATION_BUFFER *frame = &backend->RxFrames[tail];
        RtlCopyMemory(frame->Data, Frame, Bytes);
        frame->Eapol = Eapol;
        frame->Bytes = Bytes;
#ifdef MT7922_STATION_PACKET_TRACE
        Mt7922TraceStationFrame(Frame, Bytes, true, Mt7922TraceRxStaged);
#endif
        ++backend->RxCount;
        if (trace->Active && backend->RxCount > trace->RxQueuePeak) {
            trace->RxQueuePeak = backend->RxCount;
        }
        NotifyStationQueue(backend->Rx);
        accepted = true;
    } else if (trace->Active && backend->Rx != NULL) {
        /* The radio worker may retry the pending frame later. */
        ++trace->RxQueueFullEvents;
    }
    WdfSpinLockRelease(backend->Lock);
    if (Eapol) {
        backend->Station->ConnectDebug.Eapol.RxStaged += accepted;
        backend->Station->ConnectDebug.Eapol.RxStageBusy += !accepted;
    } else {
        backend->Station->ConnectDebug.Coexistence.StationRxStaged += accepted;
    }
    return accepted;
}

void Mt7922DiscardStagedStationData(MT7922_NET_QUEUES *Backend)
{
    WdfSpinLockAcquire(Backend->Lock);
    Backend->Station->ConnectDebug.Eapol.RxPosted += Backend->EapolPosted;
    Backend->EapolPosted = 0;
    Backend->RxHead = 0;
    Backend->RxCount = 0;
    Backend->TxHead = 0;
    Backend->TxCount = 0;
    ++Backend->TxGeneration;
    WdfSpinLockRelease(Backend->Lock);
}

void Mt7922ServiceNetAdapterData(MT7922_NET_QUEUES *Backend)
{
    MT7922_MCU_STATION *station = Backend->Station;
    if (!station->Ready || !station->WdiAssociationReported || station->KeyStateInvalid) {
        return;
    }

    Mt7922McuPollStationData(station, StageReceivedData, Backend);
    WdfSpinLockAcquire(Backend->Lock);
    station->ConnectDebug.Eapol.RxPosted += Backend->EapolPosted;
    Backend->EapolPosted = 0;
    const bool stationTxWaiting = Backend->TxCount != 0;
    WdfSpinLockRelease(Backend->Lock);
    Mt7922McuServiceSharedAdvert(station, stationTxWaiting);

    /* Leave a busy frame at the FIFO head. The existing worker also services RX and AWDL. */
    for (unsigned i = 0; i < MT7922_STATION_DATA_TX_SLOTS; ++i) {
        MT7922_STATION_BUFFER tx;
        WdfSpinLockAcquire(Backend->Lock);
        if (Backend->TxCount == 0) {
            WdfSpinLockRelease(Backend->Lock);
            break;
        }
        const MT7922_STATION_BUFFER *frame = &Backend->TxFrames[Backend->TxHead];
        tx.Bytes = frame->Bytes;
        tx.Privacy = frame->Privacy;
        RtlCopyMemory(tx.Data, frame->Data, tx.Bytes);
        const uint64_t generation = Backend->TxGeneration;
        WdfSpinLockRelease(Backend->Lock);

        const size_t bytes = Mt7922ConvertStationTxData(tx.Data, tx.Bytes, station->Info.NicMac, station->Ap.Bssid);
        const NTSTATUS status = bytes != 0 ? Mt7922McuSendStationData(station, tx.Data, bytes, tx.Privacy)
            : STATUS_INVALID_PARAMETER;
        MT7922_STATION_DATA_DEBUG *debug = &station->ConnectDebug.Data;
        if (bytes == 0) {
            ++debug->TxFormatDrop;
        } else if (!Mt7922StationIsEapol(tx.Data)) {
            debug->TxBusy += status == STATUS_DEVICE_BUSY;
            debug->TxHostData += status != STATUS_DEVICE_BUSY;
            debug->TxSubmitted += NT_SUCCESS(status);
            debug->TxError += !NT_SUCCESS(status) && status != STATUS_DEVICE_BUSY;
            debug->TxLastStatus = status;
        }
        if (status == STATUS_DEVICE_BUSY) {
            break;
        }
        WdfSpinLockAcquire(Backend->Lock);
        const bool sameQueue = Backend->TxGeneration == generation;
        if (sameQueue) {
            Backend->TxHead = (Backend->TxHead + 1U) % STATION_TX_QUEUE_COUNT;
            --Backend->TxCount;
            NotifyStationQueue(Backend->Tx);
        }
        WdfSpinLockRelease(Backend->Lock);
        if (!sameQueue) {
            break;
        }
    }
}
