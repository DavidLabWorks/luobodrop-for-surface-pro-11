#pragma once

/* Include after NetAdapterCx and mcu.h. The adapter context outlives its queues. */
typedef struct _MT7922_NET_QUEUES MT7922_NET_QUEUES;
/* Requests another pass of the serialized radio worker. Callable at DISPATCH_LEVEL. */
typedef void (*MT7922_WAKE_STATION)(void *Context);

void Mt7922SetNetAdapterQueueCallbacks(NETADAPTER_INIT *AdapterInit);
NTSTATUS Mt7922AttachNetAdapterQueues(NETADAPTER Adapter, MT7922_MCU_STATION *Station,
    MT7922_WAKE_STATION Wake, void *Context, MT7922_NET_QUEUES **Backend);
/* The station worker calls this for both RX interrupts/poll and host TX wakeups. */
void Mt7922ServiceNetAdapterData(MT7922_NET_QUEUES *Backend);
void Mt7922DiscardStagedStationData(MT7922_NET_QUEUES *Backend);
