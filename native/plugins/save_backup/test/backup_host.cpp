// backup_host -- a stand-in game process for the save_backup plugin.
//
//   backup_host.exe <save_backup.asi>
//
// Loads the plugin the way the loader does (LoadLibrary from the plugins folder), prints "loaded",
// then waits until stdin reaches its end (run_tests.py closes it) and exits.  The plugin's copies
// and its log are what run_tests.py checks; the log goes to riftstone\logs next to this exe.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdio.h>

int wmain(int argc, wchar_t** argv) {
    if (argc < 2) {
        printf("usage: backup_host <save_backup.asi>\n");
        return 1;
    }
    HMODULE plugin = LoadLibraryW(argv[1]);
    printf(plugin ? "loaded\n" : "FAILED to load the plugin (%lu)\n", GetLastError());
    fflush(stdout);
    if (!plugin) return 1;
    char buf[64];
    DWORD got;
    HANDLE in = GetStdHandle(STD_INPUT_HANDLE);
    while (ReadFile(in, buf, sizeof buf, &got, nullptr) && got) {
    }
    return 0;
}
