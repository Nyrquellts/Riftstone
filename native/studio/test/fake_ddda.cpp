// The selftest's stand-in DDDA.exe: it writes the folder it was started in (UTF-8) to stand-in-started.txt there and
// ends, so a test can see that Studio's Launch starts the game in its own folder. No window, nothing else.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string>

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    wchar_t dir[4096];
    DWORD n = GetCurrentDirectoryW(4096, dir);
    if (!n || n >= 4096) return 1;
    int len = WideCharToMultiByte(CP_UTF8, 0, dir, int(n), nullptr, 0, nullptr, nullptr);
    std::string text(size_t(len), '\0');
    WideCharToMultiByte(CP_UTF8, 0, dir, int(n), &text[0], len, nullptr, nullptr);
    HANDLE f = CreateFileW(L"stand-in-started.txt", GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL,
                           nullptr);
    if (f == INVALID_HANDLE_VALUE) return 2;
    DWORD wrote = 0;
    BOOL ok = WriteFile(f, text.data(), DWORD(text.size()), &wrote, nullptr);
    CloseHandle(f);
    Sleep(300);   // long enough for the test to open the process and wait on it
    return ok && wrote == text.size() ? 0 : 3;
}
