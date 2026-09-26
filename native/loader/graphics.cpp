// Riftstone runtime: Direct3D 9 -- which runtime the game gets, and what it asks that runtime to hold.
//
//   chain   [d3d9] chain = riftstone\dxvk\d3d9.dll: the game's Direct3DCreate9 goes to that DLL (DXVK's
//           32-bit d3d9.dll, or another Direct3D 9 runtime) instead of Windows' own, and no file goes into
//           the game folder.  Before it is loaded it must be inside the game folder, a 32-bit DLL and
//           export Direct3DCreate9; a d3d9.dll already in the game folder stays in charge.  If it cannot
//           start (DXVK without a Vulkan device it can use), the game gets Windows' own Direct3D 9 and
//           loader.log says so.  Off in safe mode.  For DXVK: DXVK_LOG_PATH is riftstone\logs and
//           DXVK_CONFIG_FILE the dxvk.conf beside the DLL, unless they are set already.
//   pools   [d3d9] pool_stats: the textures and buffers the game creates, in bytes by pool, until it
//           releases them.  Windows' Direct3D 9 keeps a system-memory copy of everything in
//           D3DPOOL_MANAGED inside the game's own address space, and DDDA.exe creates every texture there
//           except render targets, dynamic and system-memory ones (its texture setup, 0x01105383).  That
//           copy is what DXVK keeps out of the address space (in memory-mapped files it unmaps).
//
// The pool counters patch the device's function table (CreateTexture, CreateVolumeTexture,
// CreateCubeTexture, CreateVertexBuffer, CreateIndexBuffer) and Release in the table of each kind of
// object they count, so they work the same on Windows' Direct3D 9 and on DXVK.  What the loader itself
// creates (the F10 panel) is not counted.
#include "runtime.h"
#include <d3d9.h>
#include <intrin.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

#pragma intrinsic(_ReturnAddress)

static wchar_t g_chainSetting[MAX_PATH];        // [d3d9] chain as written
static BOOL g_poolStats = FALSE;                // counting ([d3d9] pool_stats and [live] frame_stats)
static volatile LONG g_provider = D3D_UNKNOWN;
static wchar_t g_providerPath[MAX_PATH];
static CRITICAL_SECTION g_lock;                 // the pool table, the counters, the provider path
static BOOL g_lockReady = FALSE;

// ---------------------------------------------------------------------------
// what a texture or buffer takes, in bytes

static BOOL IsFourCC(D3DFORMAT f, const char* cc) {
    return (DWORD)f == MAKEFOURCC(cc[0], cc[1], cc[2], cc[3]);
}

// Bytes of one 4x4 block for block-compressed formats, else 0.
static UINT BlockBytes(D3DFORMAT f) {
    if (IsFourCC(f, "DXT1") || IsFourCC(f, "ATI1") || IsFourCC(f, "BC4U") || IsFourCC(f, "BC4S")) return 8;
    if (IsFourCC(f, "DXT2") || IsFourCC(f, "DXT3") || IsFourCC(f, "DXT4") || IsFourCC(f, "DXT5") ||
        IsFourCC(f, "ATI2") || IsFourCC(f, "BC5U") || IsFourCC(f, "BC5S"))
        return 16;
    return 0;
}

// Bits per pixel of the other formats (32 when it is not one of these).
static UINT PixelBits(D3DFORMAT f) {
    switch (f) {
    case D3DFMT_A32B32G32R32F: return 128;
    case D3DFMT_A16B16G16R16F: case D3DFMT_A16B16G16R16: case D3DFMT_G32R32F: case D3DFMT_Q16W16V16U16: return 64;
    case D3DFMT_R8G8B8: return 24;
    case D3DFMT_R5G6B5: case D3DFMT_X1R5G5B5: case D3DFMT_A1R5G5B5: case D3DFMT_A4R4G4B4: case D3DFMT_A8R3G3B2:
    case D3DFMT_X4R4G4B4: case D3DFMT_A8L8: case D3DFMT_V8U8: case D3DFMT_L6V5U5: case D3DFMT_L16: case D3DFMT_D16:
    case D3DFMT_D16_LOCKABLE: case D3DFMT_D15S1: case D3DFMT_R16F: case D3DFMT_A8P8: case D3DFMT_UYVY: case D3DFMT_YUY2:
        return 16;
    case D3DFMT_A8: case D3DFMT_L8: case D3DFMT_A4L4: case D3DFMT_P8: case D3DFMT_R3G3B2: return 8;
    default: return 32;
    }
}

// All levels of a width x height x depth texture (faces 6 for a cube), levels as it was created with.
static uint64_t TextureBytes(UINT width, UINT height, UINT depth, UINT levels, D3DFORMAT format, UINT faces) {
    uint64_t total = 0;
    UINT block = BlockBytes(format), bits = PixelBits(format);
    if (!levels) levels = 1;
    for (UINT l = 0; l < levels && l < 32; l++) {
        uint64_t w = width >> l, h = height >> l, d = depth >> l;
        if (!w) w = 1;
        if (!h) h = 1;
        if (!d) d = 1;
        uint64_t one = block ? ((w + 3) / 4) * ((h + 3) / 4) * block : (w * h * bits + 7) / 8;
        total += one * d;
    }
    return total * (faces ? faces : 1);
}

// ---------------------------------------------------------------------------
// the pool table: object -> what it was counted as (linear probing, backward-shift deletion)

enum { KIND_TEXTURE = 1, KIND_VOLUME, KIND_CUBE, KIND_VERTICES, KIND_INDICES };
struct PoolEntry {
    uintptr_t key;                               // the object (0: empty)
    uint32_t bytes;
    uint8_t pool, kind;
};
static PoolEntry* g_table = NULL;
static const uint32_t TABLE_BITS = 16, TABLE_SIZE = 1u << TABLE_BITS, TABLE_MASK = TABLE_SIZE - 1;
static uint32_t g_tableUsed = 0;
static uint64_t g_bytes[4], g_peak[4];
static uint32_t g_objects[4];
static uint32_t g_untracked = 0;                // objects that did not fit the table (not counted)

static uint32_t Slot(uintptr_t key) { return (uint32_t)(((uint32_t)key * 0x9E3779B1u) >> (32 - TABLE_BITS)); }

static void Count(uint8_t pool, uint32_t bytes, int sign) {
    if (sign > 0) {
        g_bytes[pool] += bytes;
        g_objects[pool]++;
        if (g_bytes[pool] > g_peak[pool]) g_peak[pool] = g_bytes[pool];
    } else {
        g_bytes[pool] -= bytes <= g_bytes[pool] ? bytes : g_bytes[pool];
        if (g_objects[pool]) g_objects[pool]--;
    }
}

static void Remember(void* object, uint8_t kind, D3DPOOL pool, uint64_t bytes) {
    if ((DWORD)pool > D3DPOOL_SCRATCH || !object) return;
    if (bytes > 0xFFFFFFFFull) bytes = 0xFFFFFFFFull;
    EnterCriticalSection(&g_lock);
    if (g_table) {
        uintptr_t key = (uintptr_t)object;
        uint32_t i = Slot(key);
        while (g_table[i].key && g_table[i].key != key) i = (i + 1) & TABLE_MASK;
        if (g_table[i].key == key) {
            Count(g_table[i].pool, g_table[i].bytes, -1);   // an object released out of our sight, address reused
        } else if (g_tableUsed >= TABLE_SIZE / 4 * 3) {
            g_untracked++;
            LeaveCriticalSection(&g_lock);
            return;
        } else {
            g_tableUsed++;
        }
        g_table[i].key = key;
        g_table[i].bytes = (uint32_t)bytes;
        g_table[i].pool = (uint8_t)pool;
        g_table[i].kind = kind;
        Count((uint8_t)pool, (uint32_t)bytes, +1);
    }
    LeaveCriticalSection(&g_lock);
}

// Under g_lock.
static void Forget(void* object) {
    if (!g_table) return;
    uintptr_t key = (uintptr_t)object;
    uint32_t i = Slot(key);
    while (g_table[i].key && g_table[i].key != key) i = (i + 1) & TABLE_MASK;
    if (g_table[i].key != key) return;          // not one we counted (the loader's own, or untracked)
    Count(g_table[i].pool, g_table[i].bytes, -1);
    g_tableUsed--;
    for (uint32_t j = i;;) {                     // close the gap so every later entry stays reachable
        j = (j + 1) & TABLE_MASK;
        if (!g_table[j].key) break;
        uint32_t home = Slot(g_table[j].key);
        BOOL between = i <= j ? (i < home && home <= j) : (i < home || home <= j);
        if (!between) {
            g_table[i] = g_table[j];
            i = j;
        }
    }
    g_table[i].key = 0;
}

// ---------------------------------------------------------------------------
// the hooks

typedef ULONG(STDMETHODCALLTYPE* Release_t)(IUnknown*);
struct ReleaseHook { void** table; Release_t real; };
static ReleaseHook g_releases[16];
static volatile LONG g_releaseCount = 0;

static BOOL PatchSlot(void** table, int index, void* hook, void** real) {
    if (table[index] == hook) return TRUE;
    DWORD old;
    if (!VirtualProtect(&table[index], sizeof(void*), PAGE_READWRITE, &old)) return FALSE;
    *real = table[index];
    table[index] = hook;
    VirtualProtect(&table[index], sizeof(void*), old, &old);
    return TRUE;
}

static ULONG STDMETHODCALLTYPE Hook_Release(IUnknown* self) {
    void** table = *(void***)self;
    Release_t real = NULL;
    for (LONG i = 0; i < g_releaseCount && i < (LONG)_countof(g_releases); i++)
        if (g_releases[i].table == table) real = g_releases[i].real;
    if (!real) return 0;                        // not reachable: this hook sits only in tables it recorded
    // Held across the real Release: no other thread can get the same address back from the runtime and
    // count it before this one is forgotten.  The runtime takes its own lock inside; ours is never taken
    // the other way round (the create hooks count after the real call has returned).
    EnterCriticalSection(&g_lock);
    ULONG left = real(self);
    if (left == 0) Forget(self);
    LeaveCriticalSection(&g_lock);
    return left;
}

// The Release of this kind of object goes through Hook_Release (once per function table).  FALSE when it
// cannot: such an object is not counted, since its release would never be seen.
static BOOL WatchRelease(IUnknown* object) {
    void** table = *(void***)object;
    EnterCriticalSection(&g_lock);
    BOOL known = FALSE;
    for (LONG i = 0; i < g_releaseCount; i++) known = known || g_releases[i].table == table;
    if (!known && g_releaseCount < (LONG)_countof(g_releases)) {
        Release_t real = NULL;
        if (PatchSlot(table, 2, (void*)Hook_Release, (void**)&real) && real && real != (Release_t)Hook_Release) {
            g_releases[g_releaseCount].table = table;
            g_releases[g_releaseCount].real = real;
            InterlockedIncrement(&g_releaseCount);
            known = TRUE;
        }
    }
    if (!known) g_untracked++;
    LeaveCriticalSection(&g_lock);
    return known;
}

static BOOL FromLoader(void* returnAddress) {
    HMODULE m = NULL;
    return GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                              (LPCWSTR)returnAddress, &m) && m == g_self;
}

typedef HRESULT(STDMETHODCALLTYPE* CreateTexture_t)(IDirect3DDevice9*, UINT, UINT, UINT, DWORD, D3DFORMAT, D3DPOOL,
                                                    IDirect3DTexture9**, HANDLE*);
typedef HRESULT(STDMETHODCALLTYPE* CreateVolumeTexture_t)(IDirect3DDevice9*, UINT, UINT, UINT, UINT, DWORD, D3DFORMAT,
                                                          D3DPOOL, IDirect3DVolumeTexture9**, HANDLE*);
typedef HRESULT(STDMETHODCALLTYPE* CreateCubeTexture_t)(IDirect3DDevice9*, UINT, UINT, DWORD, D3DFORMAT, D3DPOOL,
                                                        IDirect3DCubeTexture9**, HANDLE*);
typedef HRESULT(STDMETHODCALLTYPE* CreateVertexBuffer_t)(IDirect3DDevice9*, UINT, DWORD, DWORD, D3DPOOL,
                                                         IDirect3DVertexBuffer9**, HANDLE*);
typedef HRESULT(STDMETHODCALLTYPE* CreateIndexBuffer_t)(IDirect3DDevice9*, UINT, DWORD, D3DFORMAT, D3DPOOL,
                                                        IDirect3DIndexBuffer9**, HANDLE*);
static CreateTexture_t Real_CreateTexture;
static CreateVolumeTexture_t Real_CreateVolumeTexture;
static CreateCubeTexture_t Real_CreateCubeTexture;
static CreateVertexBuffer_t Real_CreateVertexBuffer;
static CreateIndexBuffer_t Real_CreateIndexBuffer;
static void** g_poolDevTable = NULL;

enum { VT_CREATE_TEXTURE = 23, VT_CREATE_VOLUME = 24, VT_CREATE_CUBE = 25, VT_CREATE_VB = 26, VT_CREATE_IB = 27 };

static HRESULT STDMETHODCALLTYPE Hook_CreateTexture(IDirect3DDevice9* dev, UINT w, UINT h, UINT levels, DWORD usage,
                                                    D3DFORMAT fmt, D3DPOOL pool, IDirect3DTexture9** out, HANDLE* shared) {
    HRESULT hr = Real_CreateTexture(dev, w, h, levels, usage, fmt, pool, out, shared);
    if (SUCCEEDED(hr) && out && *out && !FromLoader(_ReturnAddress())) {
        if (WatchRelease(*out)) Remember(*out, KIND_TEXTURE, pool, TextureBytes(w, h, 1, (*out)->GetLevelCount(), fmt, 1));
    }
    return hr;
}

static HRESULT STDMETHODCALLTYPE Hook_CreateVolumeTexture(IDirect3DDevice9* dev, UINT w, UINT h, UINT d, UINT levels,
                                                          DWORD usage, D3DFORMAT fmt, D3DPOOL pool,
                                                          IDirect3DVolumeTexture9** out, HANDLE* shared) {
    HRESULT hr = Real_CreateVolumeTexture(dev, w, h, d, levels, usage, fmt, pool, out, shared);
    if (SUCCEEDED(hr) && out && *out && !FromLoader(_ReturnAddress())) {
        if (WatchRelease(*out)) Remember(*out, KIND_VOLUME, pool, TextureBytes(w, h, d, (*out)->GetLevelCount(), fmt, 1));
    }
    return hr;
}

static HRESULT STDMETHODCALLTYPE Hook_CreateCubeTexture(IDirect3DDevice9* dev, UINT edge, UINT levels, DWORD usage,
                                                        D3DFORMAT fmt, D3DPOOL pool, IDirect3DCubeTexture9** out,
                                                        HANDLE* shared) {
    HRESULT hr = Real_CreateCubeTexture(dev, edge, levels, usage, fmt, pool, out, shared);
    if (SUCCEEDED(hr) && out && *out && !FromLoader(_ReturnAddress())) {
        if (WatchRelease(*out)) Remember(*out, KIND_CUBE, pool, TextureBytes(edge, edge, 1, (*out)->GetLevelCount(), fmt, 6));
    }
    return hr;
}

static HRESULT STDMETHODCALLTYPE Hook_CreateVertexBuffer(IDirect3DDevice9* dev, UINT length, DWORD usage, DWORD fvf,
                                                         D3DPOOL pool, IDirect3DVertexBuffer9** out, HANDLE* shared) {
    HRESULT hr = Real_CreateVertexBuffer(dev, length, usage, fvf, pool, out, shared);
    if (SUCCEEDED(hr) && out && *out && !FromLoader(_ReturnAddress())) {
        if (WatchRelease(*out)) Remember(*out, KIND_VERTICES, pool, length);
    }
    return hr;
}

static HRESULT STDMETHODCALLTYPE Hook_CreateIndexBuffer(IDirect3DDevice9* dev, UINT length, DWORD usage, D3DFORMAT fmt,
                                                        D3DPOOL pool, IDirect3DIndexBuffer9** out, HANDLE* shared) {
    HRESULT hr = Real_CreateIndexBuffer(dev, length, usage, fmt, pool, out, shared);
    if (SUCCEEDED(hr) && out && *out && !FromLoader(_ReturnAddress())) {
        if (WatchRelease(*out)) Remember(*out, KIND_INDICES, pool, length);
    }
    return hr;
}

void GraphicsDeviceCreated(IDirect3DDevice9* dev) {
    if (!g_poolStats || !dev) return;
    void** table = *(void***)dev;
    if (g_poolDevTable) {
        if (table != g_poolDevTable)
            LogLine(L"d3d9     a second kind of Direct3D device was created; its textures and buffers are not counted");
        return;
    }
    if (!g_table) {
        g_table = (PoolEntry*)VirtualAlloc(NULL, TABLE_SIZE * sizeof(PoolEntry), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
        if (!g_table) {
            LogLine(L"d3d9     no memory for the pool counters (error %lu); textures and buffers are not counted", GetLastError());
            g_poolStats = FALSE;
            return;
        }
    }
    BOOL ok = PatchSlot(table, VT_CREATE_TEXTURE, (void*)Hook_CreateTexture, (void**)&Real_CreateTexture) &&
              PatchSlot(table, VT_CREATE_VOLUME, (void*)Hook_CreateVolumeTexture, (void**)&Real_CreateVolumeTexture) &&
              PatchSlot(table, VT_CREATE_CUBE, (void*)Hook_CreateCubeTexture, (void**)&Real_CreateCubeTexture) &&
              PatchSlot(table, VT_CREATE_VB, (void*)Hook_CreateVertexBuffer, (void**)&Real_CreateVertexBuffer) &&
              PatchSlot(table, VT_CREATE_IB, (void*)Hook_CreateIndexBuffer, (void**)&Real_CreateIndexBuffer);
    g_poolDevTable = table;
    LogLine(L"d3d9     counting the game's textures and buffers by pool%s", ok ? L"" : L": UNAVAILABLE (the device's table "
            L"could not be changed)");
    if (!ok) g_poolStats = FALSE;
}

BOOL GraphicsPools(PoolStats* out, BOOL wait) {
    memset(out, 0, sizeof *out);
    if (!g_lockReady || !g_poolStats || !g_poolDevTable) return FALSE;
    if (wait) EnterCriticalSection(&g_lock);
    else if (!TryEnterCriticalSection(&g_lock)) return FALSE;   // a crash report never waits
    for (int i = 0; i < 4; i++) {
        out->bytes[i] = g_bytes[i];
        out->peak[i] = g_peak[i];
        out->objects[i] = g_objects[i];
    }
    out->untracked = g_untracked;
    LeaveCriticalSection(&g_lock);
    return TRUE;
}

// ---------------------------------------------------------------------------
// the runtime the game gets

int GraphicsProvider(wchar_t* path, size_t cap, BOOL wait) {
    if (path && cap) {
        path[0] = 0;
        if (g_lockReady && (wait ? (EnterCriticalSection(&g_lock), TRUE) : TryEnterCriticalSection(&g_lock))) {
            wcsncpy_s(path, cap, g_providerPath, _TRUNCATE);
            LeaveCriticalSection(&g_lock);
        }
    }
    return (int)g_provider;
}

const wchar_t* GraphicsProviderName(int provider) {
    switch (provider) {
    case D3D_WINDOWS: return L"Windows' own";
    case D3D_CHAINED: return L"chained ([d3d9] chain)";
    case D3D_GAME_FOLDER: return L"a d3d9.dll in the game folder";
    case D3D_OTHER: return L"another module";
    default: return L"unknown";
    }
}

static BOOL SameFolder(const wchar_t* file, const wchar_t* dir) {
    const wchar_t* slash = wcsrchr(file, L'\\');
    size_t n = slash ? (size_t)(slash - file) : 0;
    return n && wcslen(dir) == n && _wcsnicmp(file, dir, n) == 0;
}

static wchar_t g_chainFull[MAX_PATH];           // the chained DLL, once [d3d9] chain checked out

// What kind of runtime a module is, from where it lives.
static int ClassifyPath(const wchar_t* path) {
    if (!path[0]) return D3D_UNKNOWN;
    if (g_chainFull[0] && _wcsicmp(path, g_chainFull) == 0) return D3D_CHAINED;
    wchar_t sys[MAX_PATH] = L"", wow[MAX_PATH] = L"";
    GetSystemDirectoryW(sys, MAX_PATH);
    GetSystemWow64DirectoryW(wow, MAX_PATH);
    if ((sys[0] && SameFolder(path, sys)) || (wow[0] && SameFolder(path, wow))) return D3D_WINDOWS;
    if (SameFolder(path, g_root)) return D3D_GAME_FOLDER;
    return D3D_OTHER;
}

static void ModuleOf(const void* address, wchar_t* path, size_t cap) {
    path[0] = 0;
    HMODULE m = NULL;
    if (address && GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                                      (LPCWSTR)address, &m))
        GetModuleFileNameW(m, path, (DWORD)cap);
}

static wchar_t g_loadedPath[MAX_PATH];         // the d3d9.dll the game's import loaded (at its first call)

// Where the game's Direct3DCreate9 went: the chained DLL when it answered, else the d3d9.dll the game loaded
// (Windows' own, or one in the game folder), else the module that made the object (its function table's
// slot 3, RegisterSoftwareDevice; the loader patches slot 16 only).  Not the import's target: Windows'
// compatibility shims (apphelp.dll) can sit there (seen on Windows 11 with a stand-in named DDDA.exe).
static void NoteProvider(IDirect3D9* d3d, BOOL chained) {
    wchar_t path[MAX_PATH];
    if (chained) wcsncpy_s(path, _countof(path), g_chainFull, _TRUNCATE);
    else if (g_loadedPath[0]) wcsncpy_s(path, _countof(path), g_loadedPath, _TRUNCATE);
    else ModuleOf((*(void***)d3d)[3], path, _countof(path));
    int kind = chained ? D3D_CHAINED : ClassifyPath(path);
    EnterCriticalSection(&g_lock);
    BOOL changed = kind != (int)g_provider || _wcsicmp(path, g_providerPath) != 0;
    if (changed) {
        wcsncpy_s(g_providerPath, _countof(g_providerPath), path, _TRUNCATE);
        InterlockedExchange(&g_provider, kind);
    }
    LeaveCriticalSection(&g_lock);
    if (changed) LogLine(L"d3d9     Direct3D 9 is %s (%s)", GraphicsProviderName(kind), path[0] ? path : L"?");
}

// NULL when the file is a 32-bit DLL; otherwise what it is instead.
static const wchar_t* NotA32BitDll(const wchar_t* path) {
    HANDLE f = Real_CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, NULL, OPEN_EXISTING,
                                FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return L"cannot be read";
    BYTE head[4096];
    DWORD got = 0;
    BOOL read = ReadFile(f, head, sizeof head, &got, NULL);
    CloseHandle(f);
    if (!read || got < sizeof(IMAGE_DOS_HEADER) || ((IMAGE_DOS_HEADER*)head)->e_magic != IMAGE_DOS_SIGNATURE)
        return L"is not a DLL";
    LONG at = ((IMAGE_DOS_HEADER*)head)->e_lfanew;
    if (at < (LONG)sizeof(IMAGE_DOS_HEADER) || (DWORD)at + sizeof(IMAGE_NT_HEADERS32) > got) return L"is not a DLL";
    IMAGE_NT_HEADERS32* nt = (IMAGE_NT_HEADERS32*)(head + at);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return L"is not a DLL";
    if (nt->FileHeader.Machine == IMAGE_FILE_MACHINE_AMD64)
        return L"is a 64-bit DLL, and the game is 32-bit (for DXVK: the d3d9.dll from its x32 folder)";
    if (nt->FileHeader.Machine != IMAGE_FILE_MACHINE_I386) return L"is not a 32-bit Windows DLL";
    if (!(nt->FileHeader.Characteristics & IMAGE_FILE_DLL)) return L"is a program, not a DLL";
    if (nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR32_MAGIC) return L"is not a 32-bit Windows DLL";
    return NULL;
}

// [d3d9] chain -> the DLL's full path inside the game folder; FALSE with the reason (empty: no chain).
static BOOL ChainPath(wchar_t* full, size_t cap, const wchar_t** why) {
    *why = L"";
    const wchar_t* s = g_chainSetting;
    if (!s[0]) return FALSE;
    if (wcschr(s, L':') || s[0] == L'\\' || s[0] == L'/') {
        *why = L"is not inside the game folder (write it as a path in it, such as riftstone\\dxvk\\d3d9.dll)";
        return FALSE;
    }
    wchar_t joined[MAX_PATH * 2];
    if (_snwprintf_s(joined, _countof(joined), _TRUNCATE, L"%s\\%s", g_root, s) < 0) {
        *why = L"is too long";
        return FALSE;
    }
    DWORD n = GetFullPathNameW(joined, (DWORD)cap, full, NULL);
    size_t rootLen = wcslen(g_root);
    if (!n || n >= cap || n <= rootLen + 1 || _wcsnicmp(full, g_root, rootLen) != 0 || full[rootLen] != L'\\') {
        *why = L"is not inside the game folder (write it as a path in it, such as riftstone\\dxvk\\d3d9.dll)";
        return FALSE;
    }
    const wchar_t* dot = wcsrchr(full, L'.');
    if (!dot || _wcsicmp(dot, L".dll") != 0) {
        *why = L"is not a .dll";
        return FALSE;
    }
    DWORD a = Real_GetFileAttributesW(full);
    if (a == INVALID_FILE_ATTRIBUTES || (a & FILE_ATTRIBUTE_DIRECTORY)) {
        *why = L"does not exist";
        return FALSE;
    }
    const wchar_t* bad = NotA32BitDll(full);
    if (bad) {
        *why = bad;
        return FALSE;
    }
    return TRUE;
}

static void EnvDefault(const wchar_t* name, const wchar_t* value, wchar_t* said, size_t cap) {
    wchar_t current[8];
    if (GetEnvironmentVariableW(name, current, _countof(current)) || GetLastError() != ERROR_ENVVAR_NOT_FOUND) return;
    if (SetEnvironmentVariableW(name, value)) {
        size_t n = wcslen(said);
        _snwprintf_s(said + n, cap - n, _TRUNCATE, L"%s%s = %s", n ? L", " : L"", name, value);
    }
}

void GraphicsSettings(BOOL counting) {
    if (!g_lockReady) {
        InitializeCriticalSection(&g_lock);
        g_lockReady = TRUE;
    }
    IniStr(L"d3d9", L"chain", L"", g_chainSetting, _countof(g_chainSetting));
    g_poolStats = counting && IniInt(L"d3d9", L"pool_stats", 1) != 0;
    if (g_chainSetting[0] && SafeModeActive()) {
        LogLine(L"d3d9     safe mode: [d3d9] chain = %s is off; the game gets its usual Direct3D 9", g_chainSetting);
        g_chainSetting[0] = 0;
    }
}

BOOL GraphicsChainWanted() { return g_chainSetting[0] != 0; }

static INIT_ONCE g_chainOnce = INIT_ONCE_STATIC_INIT;
static Direct3DCreate9_t g_chainCreate = NULL;
static volatile LONG g_chainFailed = 0;

// Decided once, at the game's first Direct3DCreate9 (not while DLLs load): the chain's DLL is loaded only if
// everything about it checks out, and only when the d3d9.dll the game loaded is Windows' own.
static BOOL CALLBACK DecideChain(PINIT_ONCE, PVOID, PVOID*) {
    HMODULE first = GetModuleHandleW(L"d3d9.dll");   // what the game's import loaded
    if (first) GetModuleFileNameW(first, g_loadedPath, _countof(g_loadedPath));
    const wchar_t* why = L"";
    wchar_t full[MAX_PATH];
    if (!ChainPath(full, _countof(full), &why)) {
        if (g_chainSetting[0])
            LogLine(L"d3d9     [d3d9] chain = %s %s; the game gets its usual Direct3D 9", g_chainSetting, why);
        return TRUE;
    }
    if (ClassifyPath(g_loadedPath) == D3D_GAME_FOLDER) {
        LogLine(L"d3d9     the game folder has its own %s, which stays in charge; [d3d9] chain = %s is not loaded "
                L"(take one of the two out)", g_loadedPath, g_chainSetting);
        return TRUE;
    }
    wchar_t dir[MAX_PATH], conf[MAX_PATH], said[1024] = L"";
    wcsncpy_s(dir, _countof(dir), full, _TRUNCATE);
    wchar_t* slash = wcsrchr(dir, L'\\');
    if (slash) *slash = 0;
    EnvDefault(L"DXVK_LOG_PATH", g_logDir, said, _countof(said));
    _snwprintf_s(conf, _countof(conf), _TRUNCATE, L"%s\\dxvk.conf", dir);
    DWORD a = Real_GetFileAttributesW(conf);
    if (a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY)) EnvDefault(L"DXVK_CONFIG_FILE", conf, said, _countof(said));
    HMODULE lib = LoadLibraryExW(full, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!lib) {
        LogLine(L"d3d9     %s could not be loaded (error %lu); the game gets its usual Direct3D 9", full, GetLastError());
        return TRUE;
    }
    Direct3DCreate9_t create = (Direct3DCreate9_t)GetProcAddress(lib, "Direct3DCreate9");
    if (!create || lib == first) {
        LogLine(L"d3d9     %s %s; the game gets its usual Direct3D 9", full,
                create ? L"is the d3d9.dll the game has already" : L"has no Direct3DCreate9");
        return TRUE;
    }
    wcsncpy_s(g_chainFull, _countof(g_chainFull), full, _TRUNCATE);
    g_chainCreate = create;
    LogLine(L"d3d9     chained %s for the game's Direct3D 9", full);
    if (said[0]) LogLine(L"d3d9     for DXVK: %s", said);
    return TRUE;
}

IDirect3D9* GraphicsCreate9(UINT sdk, Direct3DCreate9_t real) {
    InitOnceExecuteOnce(&g_chainOnce, DecideChain, NULL, NULL);
    IDirect3D9* d3d = NULL;
    BOOL chained = FALSE;
    if (g_chainCreate && !g_chainFailed) {
        d3d = g_chainCreate(sdk);
        chained = d3d != NULL;
        // One that does not start will not start later either: the rest of the session gets the usual runtime.
        if (!d3d && !InterlockedExchange(&g_chainFailed, 1))
            LogLine(L"d3d9     %s did not start (for DXVK: no Vulkan device it can use; its log in riftstone\\logs "
                    L"says why); the game gets its usual Direct3D 9", g_chainFull);
    }
    if (!d3d && real) d3d = real(sdk);
    if (d3d) NoteProvider(d3d, chained);
    return d3d;
}
