// A stand-in for a plugin that hooks one of the game's imports itself: in its DllMain it points the
// game's import of KERNEL32!CreateFileW at its own function, which counts the call and passes it on to
// what the slot held before.  Plugins load after the loader has hooked the import table, so that is the
// loader's hook: the chain is game -> this plugin -> the loader -> Windows.  run_tests.py checks that the
// loader leaves the plugin's hook in place instead of taking it for the DRM restoring the table.
#define WIN32_LEAN_AND_MEAN
#include <Windows.h>
#include <string.h>

typedef HANDLE(WINAPI* CreateFileW_t)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
static CreateFileW_t g_next;
static volatile LONG g_calls;

static HANDLE WINAPI Hooked(LPCWSTR name, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES sa, DWORD disposition,
                            DWORD flags, HANDLE templ) {
    InterlockedIncrement(&g_calls);
    return g_next(name, access, share, sa, disposition, flags, templ);
}

extern "C" __declspec(dllexport) LONG ChainPlugin_Calls() { return g_calls; }

// The game executable's import slot for dll!func.
static void** GameImport(const char* dll, const char* func) {
    BYTE* base = (BYTE*)GetModuleHandleW(NULL);
    IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)(base + ((IMAGE_DOS_HEADER*)base)->e_lfanew);
    IMAGE_DATA_DIRECTORY dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!dir.VirtualAddress) return NULL;
    for (IMAGE_IMPORT_DESCRIPTOR* imp = (IMAGE_IMPORT_DESCRIPTOR*)(base + dir.VirtualAddress); imp->Name; ++imp) {
        if (_stricmp((const char*)(base + imp->Name), dll) != 0 || !imp->OriginalFirstThunk) continue;
        IMAGE_THUNK_DATA* thunk = (IMAGE_THUNK_DATA*)(base + imp->FirstThunk);
        IMAGE_THUNK_DATA* names = (IMAGE_THUNK_DATA*)(base + imp->OriginalFirstThunk);
        for (; thunk->u1.Function; ++thunk, ++names) {
            if (IMAGE_SNAP_BY_ORDINAL(names->u1.Ordinal)) continue;
            IMAGE_IMPORT_BY_NAME* ibn = (IMAGE_IMPORT_BY_NAME*)(base + names->u1.AddressOfData);
            if (strcmp((const char*)ibn->Name, func) == 0) return (void**)&thunk->u1.Function;
        }
    }
    return NULL;
}

BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        void** slot = GameImport("KERNEL32.dll", "CreateFileW");
        DWORD old;
        if (slot && VirtualProtect(slot, sizeof(void*), PAGE_READWRITE, &old)) {
            g_next = (CreateFileW_t)*slot;
            *slot = (void*)Hooked;
            VirtualProtect(slot, sizeof(void*), old, &old);
        }
    }
    return TRUE;
}
