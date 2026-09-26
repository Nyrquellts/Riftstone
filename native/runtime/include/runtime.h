#pragma once
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <stddef.h>
#include <stdint.h>
#ifdef RIFTSTONE_RUNTIME_BUILD
#define RS_API extern "C" __declspec(dllexport)
#else
#define RS_API extern "C" __declspec(dllimport)
#endif
enum RsStatus { RS_OK=0, RS_BAD_ARGUMENT=1, RS_NOT_INITIALIZED=2, RS_BUSY=3, RS_UNAVAILABLE=4,
                RS_NOT_OWNED=5, RS_NO_MEMORY=6, RS_HOOK_FAILED=7, RS_WRONG_THREAD=8 };
struct RsConfig {
    uint32_t size, version;
    const wchar_t* base_root;
    const wchar_t* log_path;
    size_t allocation_limit;
    uintptr_t pointer_ceiling;
    int use_mimalloc;
    HANDLE redirect_heap; // only this caller-owned heap is eligible for HeapAlloc redirection
};
struct RsStats { uint32_t size; uint64_t owned_blocks, owned_bytes, virtual_handles, redirected_opens, passthrough_virtual_alloc; };
RS_API int __cdecl RiftstoneRuntime_Initialize(const RsConfig* config) noexcept;
RS_API int __cdecl RiftstoneRuntime_Shutdown() noexcept;
RS_API int __cdecl RiftstoneRuntime_Stats(RsStats* stats) noexcept;
RS_API void* __cdecl RiftstoneRuntime_Allocate(size_t bytes, size_t alignment) noexcept;
RS_API int __cdecl RiftstoneRuntime_Free(void* pointer) noexcept;
RS_API int __cdecl RiftstoneRuntime_AddFile(const wchar_t* relative, const void* bytes, size_t size, int priority) noexcept;
RS_API int __cdecl RiftstoneRuntime_LoadBundle(const void* bytes, size_t size) noexcept;
// Hooks affect only direct calls from this PE32 module. No game is discovered,
// injected into, started, or given an unverified internal-allocator profile.
RS_API int __cdecl RiftstoneRuntime_EnableHooks(HMODULE caller_module) noexcept;
RS_API int __cdecl RiftstoneRuntime_DisableHooks() noexcept;
RS_API int __cdecl RiftstoneRuntime_GameProfile(const char* feature) noexcept;
