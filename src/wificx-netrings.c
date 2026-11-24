#include <ntddk.h>
#include <wdf.h>
#include <ndis.h>
#include <netadaptercx.h>
#include <net/virtualaddress.h>
#include <net/wifi/exemptionaction.h>
#include "wificx-netrings.h"

static size_t CopyTransmitFragments(const NET_RING *Ring, UINT32 Start, UINT32 Count,
    const NET_EXTENSION *VirtualAddress, uint8_t *Output, size_t Capacity)
{
    size_t copied = 0;
    UINT32 index = Start;
    for (UINT32 remaining = Count; remaining != 0; --remaining) {
        const NET_FRAGMENT *frag = NetRingGetFragmentAtIndex(Ring, index);
        const NET_FRAGMENT_VIRTUAL_ADDRESS *address = NetExtensionGetFragmentVirtualAddress(VirtualAddress, index);
        if (address->VirtualAddress == NULL || frag->Offset > frag->Capacity ||
            frag->ValidLength > frag->Capacity - frag->Offset || frag->ValidLength > Capacity - copied) {
            return 0; /* Reject an incomplete frame. */
        }
        RtlCopyMemory(Output + copied, (const uint8_t *)address->VirtualAddress + frag->Offset,
            (size_t)frag->ValidLength);
        copied += (size_t)frag->ValidLength;
        index = NetRingIncrementIndex(Ring, index);
    }
    return copied;
}

bool Mt7922TakeNetTxData(const NET_RING_COLLECTION *Rings, const NET_EXTENSION *VirtualAddress,
    const NET_EXTENSION *ExemptionAction, MT7922_STATION_BUFFER *Destination)
{
    if (Destination->Bytes != 0 || !VirtualAddress->Enabled || !ExemptionAction->Enabled) {
        return false;
    }
    NET_RING *packetRing = Rings->Rings[NetRingTypePacket];
    NET_RING *fragmentRing = Rings->Rings[NetRingTypeFragment];
    UINT32 nextPacket = packetRing->NextIndex;
    UINT32 nextFragment = fragmentRing->NextIndex;
    const UINT32 packetEnd = packetRing->EndIndex;
    while (nextPacket != packetEnd) {
        NET_PACKET *packet = NetRingGetPacketAtIndex(packetRing, nextPacket);
        const UINT32 fragments = packet->FragmentCount;
        const UINT32 available = NetRingGetRangeCount(fragmentRing, nextFragment, fragmentRing->EndIndex);
        if (fragments != 0 && (packet->FragmentIndex != nextFragment || fragments > available)) {
            break;
        }
        const size_t copied = packet->Ignore || fragments == 0 ? 0 :
            CopyTransmitFragments(fragmentRing, nextFragment, fragments, VirtualAddress,
                Destination->Data, sizeof(Destination->Data));
        const UINT8 exemption = WifiExtensionGetExemptionAction(ExemptionAction, nextPacket)->ExemptionAction;
        if (copied > 32 && exemption <= Mt7922PrivacyClearUntilKey) {
            Destination->Privacy = (MT7922_PRIVACY)exemption;
            Destination->Bytes = copied;
        } else {
            packet->Scratch = 1;
        }
        nextFragment = NetRingAdvanceIndex(fragmentRing, nextFragment, fragments);
        nextPacket = NetRingIncrementIndex(packetRing, nextPacket);
        if (Destination->Bytes != 0) {
            break;
        }
    }

    /* The private copy is complete. Return the OS buffers together. */
    fragmentRing->NextIndex = nextFragment;
    fragmentRing->BeginIndex = nextFragment;
    packetRing->NextIndex = nextPacket;
    packetRing->BeginIndex = nextPacket;
    return Destination->Bytes != 0;
}

bool Mt7922PutNetRxData(const NET_RING_COLLECTION *Rings, const NET_EXTENSION *VirtualAddress,
    const MT7922_STATION_BUFFER *Source)
{
    if (Source->Bytes <= 32 || Source->Bytes > sizeof(Source->Data) || !VirtualAddress->Enabled) {
        return false;
    }
    NET_RING *packetRing = Rings->Rings[NetRingTypePacket];
    NET_RING *fragmentRing = Rings->Rings[NetRingTypeFragment];
    UINT32 nextPacket = packetRing->NextIndex;
    UINT32 nextFragment = fragmentRing->NextIndex;
    const UINT32 packetEnd = packetRing->EndIndex;
    const UINT32 fragmentEnd = fragmentRing->EndIndex;
    bool copied = false;
    while (nextPacket != packetEnd && nextFragment != fragmentEnd) {
        NET_FRAGMENT *fragment = NetRingGetFragmentAtIndex(fragmentRing, nextFragment);
        const NET_FRAGMENT_VIRTUAL_ADDRESS *address =
            NetExtensionGetFragmentVirtualAddress(VirtualAddress, nextFragment);
        NET_PACKET *packet = NetRingGetPacketAtIndex(packetRing, nextPacket);
        fragment->Offset = 0;
        fragment->ValidLength = 0;
        packet->FragmentIndex = nextFragment;
        packet->FragmentCount = 1;
        RtlZeroMemory(&packet->Layout, sizeof(packet->Layout));
        packet->Layout.Layer2Type = NetPacketLayer2TypeIeee80211;

        if (address->VirtualAddress != NULL && fragment->Capacity >= Source->Bytes) {
            RtlCopyMemory(address->VirtualAddress, Source->Data, Source->Bytes);
            fragment->ValidLength = (UINT32)Source->Bytes;
            copied = true;
        }
        packet->Ignore = !copied;
        nextFragment = NetRingIncrementIndex(fragmentRing, nextFragment);
        nextPacket = NetRingIncrementIndex(packetRing, nextPacket);
        if (copied) {
            break;
        }
    }
    fragmentRing->NextIndex = nextFragment;
    fragmentRing->BeginIndex = nextFragment;
    packetRing->NextIndex = nextPacket;
    packetRing->BeginIndex = nextPacket;
    return copied;
}

void Mt7922CancelNetRing(const NET_RING_COLLECTION *Rings, bool Receive)
{
    NET_RING *packetRing = Rings->Rings[NetRingTypePacket];
    NET_RING *fragmentRing = Rings->Rings[NetRingTypeFragment];
    const UINT32 packetEnd = packetRing->EndIndex;
    const UINT32 fragmentEnd = fragmentRing->EndIndex;
    /* Cancel both the posted and unposted parts of the driver's packet range. */
    for (UINT32 index = packetRing->BeginIndex; index != packetEnd;
        index = NetRingIncrementIndex(packetRing, index)) {
        NET_PACKET *packet = NetRingGetPacketAtIndex(packetRing, index);
        if (Receive) {
            packet->Ignore = 1;
            packet->FragmentCount = 0;
        } else {
            packet->Scratch = 1;
        }
    }
    fragmentRing->NextIndex = fragmentEnd;
    fragmentRing->BeginIndex = fragmentEnd;
    packetRing->NextIndex = packetEnd;
    packetRing->BeginIndex = packetEnd;
}
