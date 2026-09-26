// A stand-in community plugin (like a third-party .asi): on DLL attach it hooks
// nothing, it just drops a marker file next to itself so run_tests.py can confirm the
// Riftstone loader brought it into the process from riftstone\plugins.
#define WIN32_LEAN_AND_MEAN
#include <Windows.h>

BOOL APIENTRY DllMain(HMODULE self, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        wchar_t path[MAX_PATH];
        GetModuleFileNameW(self, path, MAX_PATH);
        wchar_t* slash = wcsrchr(path, L'\\');
        if (slash) {
            wcscpy_s(slash + 1, MAX_PATH - (slash + 1 - path), L"marker_plugin.loaded");
            HANDLE h = CreateFileW(path, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
            if (h != INVALID_HANDLE_VALUE) {
                DWORD w;
                WriteFile(h, "loaded", 6, &w, nullptr);
                CloseHandle(h);
            }
        }
    }
    return TRUE;
}
