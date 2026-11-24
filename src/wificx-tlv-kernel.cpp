#include <ntddk.h>

/* WiFiCx TLV allocator hooks follow the Microsoft sample. The driver always
   supplies AllocationContext zero, so the generator owns these buffers. */
#define MT7922_TLV_TAG 'TctM'

void* __cdecl operator new(size_t Bytes, ULONG_PTR AllocationContext) noexcept
{
    return AllocationContext == 0
        ? ExAllocatePool2(POOL_FLAG_NON_PAGED, Bytes == 0 ? 1 : Bytes, MT7922_TLV_TAG)
        : nullptr;
}

void __cdecl operator delete(void* Buffer) noexcept
{
    if (Buffer != nullptr) {
        ExFreePoolWithTag(Buffer, MT7922_TLV_TAG);
    }
}

void __cdecl operator delete[](void* Buffer) noexcept
{
    ::operator delete(Buffer);
}

void __cdecl operator delete(void* Buffer, ULONG_PTR) noexcept
{
    ::operator delete(Buffer);
}

void __cdecl operator delete[](void* Buffer, ULONG_PTR) noexcept
{
    ::operator delete(Buffer);
}
