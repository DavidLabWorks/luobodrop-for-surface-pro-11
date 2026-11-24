#include <ntddk.h>
#include <wdf.h>
#include <ndis.h>
#include <netadaptercx.h>
#include <net/virtualaddress.h>
#include <net/wifi/exemptionaction.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../src/wificx-netrings.h"

#define RING_COUNT 8U

#pragma warning(push)
#pragma warning(disable:4324) /* The WDK ring headers require cache line alignment. */
typedef struct {
    DECLSPEC_ALIGN(SYSTEM_CACHE_ALIGNMENT_SIZE)
    uint8_t Packets[FIELD_OFFSET(NET_RING, Buffer) + RING_COUNT * sizeof(NET_PACKET)];
    DECLSPEC_ALIGN(SYSTEM_CACHE_ALIGNMENT_SIZE)
    uint8_t Fragments[FIELD_OFFSET(NET_RING, Buffer) + RING_COUNT * sizeof(NET_FRAGMENT)];
    NET_RING_COLLECTION Rings;
    NET_EXTENSION Extension;
    NET_EXTENSION Exemption;
    NET_PACKET_WIFI_EXEMPTION_ACTION Actions[RING_COUNT];
    NET_FRAGMENT_VIRTUAL_ADDRESS Address[RING_COUNT];
    uint8_t Storage[RING_COUNT][64];
} TEST_NET_RINGS;
#pragma warning(pop)

static void InitRings(TEST_NET_RINGS *Fixture)
{
    memset(Fixture, 0, sizeof(*Fixture));
    NET_RING *p = (NET_RING *)Fixture->Packets;
    NET_RING *f = (NET_RING *)Fixture->Fragments;
    p->ElementStride = sizeof(NET_PACKET);
    f->ElementStride = sizeof(NET_FRAGMENT);
    p->NumberOfElements = f->NumberOfElements = RING_COUNT;
    p->ElementIndexMask = f->ElementIndexMask = RING_COUNT - 1;
    Fixture->Rings.Rings[NetRingTypePacket] = p;
    Fixture->Rings.Rings[NetRingTypeFragment] = f;
    Fixture->Extension.Reserved[0] = Fixture->Address;
    Fixture->Extension.Reserved[1] = (void *)(UINT_PTR)sizeof(Fixture->Address[0]);
    Fixture->Extension.Enabled = TRUE;
    Fixture->Exemption.Reserved[0] = Fixture->Actions;
    Fixture->Exemption.Reserved[1] = (void *)(UINT_PTR)sizeof(Fixture->Actions[0]);
    Fixture->Exemption.Enabled = TRUE;
    for (unsigned i = 0; i < RING_COUNT; ++i) {
        Fixture->Address[i].VirtualAddress = Fixture->Storage[i];
        NetRingGetFragmentAtIndex(f, i)->Capacity = sizeof(Fixture->Storage[i]);
    }
}

static void SetFragment(TEST_NET_RINGS *Fixture, UINT32 Index, const uint8_t *Data, size_t Bytes)
{
    NET_RING *ring = Fixture->Rings.Rings[NetRingTypeFragment];
    NET_FRAGMENT *fragment = NetRingGetFragmentAtIndex(ring, Index);
    fragment->Offset = 2;
    fragment->ValidLength = (UINT32)Bytes;
    memcpy(Fixture->Storage[Index] + 2, Data, Bytes);
}

static void VerifyTxRings(void)
{
    const uint8_t eapol[] = {
        0x08, 0x01, 0, 0, 2, 1, 2, 3, 4, 5, 2, 6, 7, 8, 9, 10,
        2, 1, 2, 3, 4, 5, 0, 0, 0xaa, 0xaa, 0x03, 0, 0, 0, 0x88, 0x8e, 2, 0, 0, 0
    };
    TEST_NET_RINGS fixture;
    InitRings(&fixture);
    NET_RING *p = fixture.Rings.Rings[NetRingTypePacket];
    NET_RING *f = fixture.Rings.Rings[NetRingTypeFragment];
    p->BeginIndex = p->NextIndex = 6;
    p->EndIndex = 1;
    f->BeginIndex = f->NextIndex = 6;
    f->EndIndex = 2;

    NET_PACKET *other = NetRingGetPacketAtIndex(p, 6);
    other->FragmentIndex = 6;
    other->FragmentCount = 1;
    const uint8_t invalid[32] = {0};
    SetFragment(&fixture, 6, invalid, sizeof(invalid));

    NET_PACKET *first = NetRingGetPacketAtIndex(p, 7);
    first->FragmentIndex = 7;
    first->FragmentCount = 2;
    SetFragment(&fixture, 7, eapol, 13);
    SetFragment(&fixture, 0, eapol + 13, sizeof(eapol) - 13);

    NET_PACKET *second = NetRingGetPacketAtIndex(p, 0);
    second->FragmentIndex = 1;
    second->FragmentCount = 1;
    SetFragment(&fixture, 1, eapol, sizeof(eapol));

    fixture.Actions[7].ExemptionAction = Mt7922PrivacyClearUntilKey;
    MT7922_STATION_BUFFER output = {.Bytes = 1};
    assert(!Mt7922TakeNetTxData(&fixture.Rings, &fixture.Extension, &fixture.Exemption, &output));
    assert(p->BeginIndex == 6 && f->BeginIndex == 6);
    output.Bytes = 0;
    f->EndIndex = 6; /* Packet posted before its fragments become available. */
    assert(!Mt7922TakeNetTxData(&fixture.Rings, &fixture.Extension, &fixture.Exemption, &output));
    assert(!other->Scratch && p->BeginIndex == 6 && p->NextIndex == 6 &&
        f->BeginIndex == 6 && f->NextIndex == 6);
    f->EndIndex = 2;
    assert(Mt7922TakeNetTxData(&fixture.Rings, &fixture.Extension, &fixture.Exemption, &output));
    assert(other->Scratch && !other->Ignore && !first->Scratch && !second->Scratch);
    assert(output.Bytes == sizeof(eapol) && memcmp(output.Data, eapol, sizeof(eapol)) == 0);
    assert(output.Privacy == Mt7922PrivacyClearUntilKey);
    assert(p->BeginIndex == 0 && p->NextIndex == 0 && f->BeginIndex == 1 && f->NextIndex == 1);
    assert(!Mt7922TakeNetTxData(&fixture.Rings, &fixture.Extension, &fixture.Exemption, &output));
    assert(p->BeginIndex == 0 && f->BeginIndex == 1);

    output.Bytes = 0;
    NetRingGetFragmentAtIndex(f, 1)->ValidLength = 64; /* Offset already consumes two bytes. */
    assert(!Mt7922TakeNetTxData(&fixture.Rings, &fixture.Extension, &fixture.Exemption, &output));
    assert(second->Scratch && !second->Ignore && p->BeginIndex == p->EndIndex && f->BeginIndex == f->EndIndex);
    p->EndIndex = 2;
    f->EndIndex = 3;
    Mt7922CancelNetRing(&fixture.Rings, false);
    assert(NetRingGetPacketAtIndex(p, 1)->Scratch == 1 && p->NextIndex == 2 && f->BeginIndex == 3);

    const uint8_t arp[] = {
        0x08, 0x01, 0, 0, 2, 1, 2, 3, 4, 5, 2, 6, 7, 8, 9, 10,
        255, 255, 255, 255, 255, 255, 0, 0, 0xaa, 0xaa, 0x03, 0, 0, 0, 0x08, 0x06, 0, 1
    };
    p->EndIndex = 3;
    f->EndIndex = 4;
    NET_PACKET *data = NetRingGetPacketAtIndex(p, 2);
    data->FragmentIndex = 3;
    data->FragmentCount = 1;
    SetFragment(&fixture, 3, arp, sizeof(arp));
    assert(Mt7922TakeNetTxData(&fixture.Rings, &fixture.Extension, &fixture.Exemption, &output));
    assert(output.Bytes == sizeof(arp) && memcmp(output.Data, arp, sizeof(arp)) == 0);
    assert(output.Privacy == Mt7922PrivacyRequireKey && !data->Scratch);
}

static void VerifyRxRings(void)
{
    TEST_NET_RINGS fixture;
    InitRings(&fixture);
    NET_RING *p = fixture.Rings.Rings[NetRingTypePacket];
    NET_RING *f = fixture.Rings.Rings[NetRingTypeFragment];
    MT7922_STATION_BUFFER input = {
        .Bytes = 36,
        .Data = {0x08, 0x02, 0, 0, 2, 6, 7, 8, 9, 10, 2, 1, 2, 3, 4, 5,
            2, 1, 2, 3, 4, 5, 0, 0, 0xaa, 0xaa, 0x03, 0, 0, 0, 0x88, 0x8e, 2, 0, 0, 0}
    };

    p->BeginIndex = p->NextIndex = p->EndIndex = 7;
    f->BeginIndex = f->NextIndex = f->EndIndex = 7;
    assert(!Mt7922PutNetRxData(&fixture.Rings, &fixture.Extension, &input));
    assert(p->BeginIndex == 7 && f->BeginIndex == 7);

    p->EndIndex = 1;
    assert(!Mt7922PutNetRxData(&fixture.Rings, &fixture.Extension, &input));
    assert(p->BeginIndex == 7 && p->NextIndex == 7 &&
        f->BeginIndex == 7 && f->NextIndex == 7);

    f->EndIndex = 1;
    NetRingGetFragmentAtIndex(f, 7)->Capacity = 10;
    assert(Mt7922PutNetRxData(&fixture.Rings, &fixture.Extension, &input));
    assert(NetRingGetPacketAtIndex(p, 7)->Ignore);
    const NET_PACKET *packet = NetRingGetPacketAtIndex(p, 0);
    assert(!packet->Ignore && packet->FragmentIndex == 0 && packet->FragmentCount == 1);
    assert(packet->Layout.Layer2Type == NetPacketLayer2TypeIeee80211 && packet->Layout.Layer2HeaderLength == 0);
    assert(NetRingGetFragmentAtIndex(f, 0)->ValidLength == input.Bytes);
    assert(memcmp(fixture.Storage[0], input.Data, input.Bytes) == 0);
    assert(p->BeginIndex == p->EndIndex && f->BeginIndex == f->EndIndex);

    p->EndIndex = f->EndIndex = 3;
    p->NextIndex = f->NextIndex = 2;
    NetRingGetPacketAtIndex(p, 1)->FragmentCount = 1;
    NetRingGetPacketAtIndex(p, 2)->FragmentCount = 1;
    Mt7922CancelNetRing(&fixture.Rings, true);
    assert(NetRingGetPacketAtIndex(p, 1)->Ignore && NetRingGetPacketAtIndex(p, 2)->Ignore);
    assert(NetRingGetPacketAtIndex(p, 1)->FragmentCount == 0 &&
        NetRingGetPacketAtIndex(p, 2)->FragmentCount == 0);
    assert(p->BeginIndex == 3 && p->NextIndex == 3 && f->BeginIndex == 3 && f->NextIndex == 3);
}

void VerifyWificxNetRings(void)
{
    VerifyTxRings();
    VerifyRxRings();
}

#ifdef LUOBODROP_STANDALONE_NET_RINGS
int main(void)
{
    VerifyWificxNetRings();
    puts("NetRing TX, RX and cancellation tests passed.");
    return 0;
}
#endif
