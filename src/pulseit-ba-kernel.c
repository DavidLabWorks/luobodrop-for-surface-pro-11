/* Compile the original PulseIt Block Ack module with a KMDF allocator. */
#include <ntddk.h>
#include <stdlib.h>

#define MT7922_BA_POOL_TAG 'b287'

static void *BaAllocate(size_t Bytes)
{
    return ExAllocatePool2(POOL_FLAG_NON_PAGED, Bytes, MT7922_BA_POOL_TAG);
}

static void BaFree(void *Buffer)
{
    if (Buffer != NULL) {
        ExFreePoolWithTag(Buffer, MT7922_BA_POOL_TAG);
    }
}

#define malloc BaAllocate
#define free BaFree
#include "../tools/pulseit-ba/ba_reorder.c"
