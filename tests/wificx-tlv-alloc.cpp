#include <cstddef>
#include <new>

/* The WDK generator uses the allocation hook provided by the host.
   The user-mode test uses the CRT heap so the library can free its buffers. */
void* __cdecl operator new(std::size_t Bytes, std::size_t) noexcept
{
    return ::operator new(Bytes, std::nothrow);
}
