"""Which Windows functions Riftstone's DLLs may import: each one checked to exist on Windows 10.

A DLL that imports a function Windows lacks does not load at all: Windows refuses it before any of its code runs
("The specified procedure could not be found", the loader's ``FAILED to load``), and for the loader itself the game
does not start.  So the player zip is checked before it is made (``package.build_plugins``) and so is the release
(``tools/make_release.py``): every import of the loader, the plugins and Ninput must be in :data:`VETTED`, and the
header must not ask for a Windows newer than 10.

:data:`VETTED` is every function the loader (both builds), the twelve plugins and Ninput's DLLs imported on
2026-10-16 (``pe.imports`` of the builds, MSVC x86, static runtime ``/MT``): KERNEL32, USER32, GDI32 and ADVAPI32
only, no Visual C++ runtime and no ``api-ms-win-*`` API set.  Microsoft's documentation gives each of them a
minimum of Windows 7 or older (the newest: ``TryAcquireSRWLockExclusive`` and ``K32GetProcessMemoryInfo``,
Windows 7; ``RegGetValueW``, ``GetTickCount64``, the thread-pool timers and waits, ``InitOnceExecuteOnce`` and
``QueryFullProcessImageNameW``, Vista), so all of them are in Windows 10 1809 and every later build.  Checked by
reading the files, not on a Windows 10 PC.  A build that imports anything else stops the package until someone
looks up that function's minimum and adds it here; one newer than Windows 10 1809 is looked up with
``GetProcAddress`` at run time and given a fallback instead (``GetDpiForWindow`` and
``SetThreadDpiAwarenessContext`` are Windows 10 1607, so in 1809 too; neither is imported).  What a DLL looks up
by name is not in its import table: the loader's newer ones (``GetCurrentThreadStackLimits``,
``GetCurrentInputMessageSource``: Windows 8; ``PssCaptureSnapshot``: 8.1) are each checked for NULL first and
have a Windows 7 path.
"""
from __future__ import annotations

from . import pe

OLDEST_WINDOWS = (10, 0)            # the header's subsystem version must not be newer (Windows 10 and 11 are 10.0)

VETTED: dict[str, frozenset[str]] = {
    "kernel32.dll": frozenset("""
        AcquireSRWLockExclusive AddVectoredExceptionHandler CloseHandle CloseThreadpoolTimer CloseThreadpoolWait
        CompareStringW CopyFileW CreateDirectoryW CreateEventExW CreateEventW CreateFileMappingW CreateFileW
        CreateSemaphoreExW CreateSymbolicLinkW CreateThread CreateThreadpoolTimer CreateThreadpoolWait
        CreateToolhelp32Snapshot DecodePointer DeleteCriticalSection DeleteFileW DisableThreadLibraryCalls
        EncodePointer EnterCriticalSection EnumSystemLocalesW ExitProcess ExitThread ExpandEnvironmentStringsW
        FileTimeToSystemTime FindClose FindFirstFileExW FindFirstFileW FindNextFileW FlsAlloc FlsFree FlsGetValue
        FlsSetValue FlushFileBuffers FlushInstructionCache FlushProcessWriteBuffers FormatMessageA FormatMessageW
        FreeEnvironmentStringsW FreeLibrary FreeLibraryAndExitThread FreeLibraryWhenCallbackReturns GetACP GetCPInfo
        GetCommandLineA GetCommandLineW GetConsoleMode GetConsoleOutputCP GetCurrentProcess GetCurrentProcessId
        GetCurrentProcessorNumber GetCurrentThread GetCurrentThreadId GetDateFormatW GetEnvironmentStringsW
        GetEnvironmentVariableW GetExitCodeThread GetFileAttributesExW GetFileAttributesW
        GetFileInformationByHandleEx GetFileSizeEx GetFileType GetFullPathNameW GetLastError GetLocalTime
        GetLocaleInfoEx GetLocaleInfoW GetModuleFileNameW GetModuleHandleA GetModuleHandleExA GetModuleHandleExW
        GetModuleHandleW GetNativeSystemInfo GetOEMCP GetPrivateProfileIntW GetPrivateProfileSectionW
        GetPrivateProfileStringW GetProcAddress GetProcessHandleCount GetProcessHeap GetStartupInfoW GetStdHandle
        GetStringTypeW GetSystemDirectoryW GetSystemInfo GetSystemTimeAsFileTime GetSystemWow64DirectoryW
        GetTempPathW GetThreadContext GetTickCount GetTickCount64 GetTimeFormatW GetUserDefaultLCID
        GlobalMemoryStatusEx HeapAlloc HeapFree HeapReAlloc HeapSize InitOnceExecuteOnce InitializeCriticalSection
        InitializeCriticalSectionAndSpinCount InitializeCriticalSectionEx InitializeSListHead InterlockedFlushSList
        InterlockedPushEntrySList IsBadReadPtr IsBadWritePtr IsDebuggerPresent IsProcessorFeaturePresent
        IsThreadAFiber IsValidCodePage IsValidLocale K32GetProcessMemoryInfo LCMapStringW LeaveCriticalSection
        LoadLibraryExW LoadLibraryW LocalFree MapViewOfFile Module32FirstW Module32NextW MoveFileExW
        MultiByteToWideChar OpenProcess OpenThread OutputDebugStringA OutputDebugStringW QueryFullProcessImageNameW
        QueryPerformanceCounter QueryPerformanceFrequency RaiseException ReadConsoleW ReadFile
        ReleaseSRWLockExclusive RemoveDirectoryW RemoveVectoredExceptionHandler ResumeThread RtlUnwind
        SetConsoleCtrlHandler SetEndOfFile SetEnvironmentVariableW SetFileInformationByHandle SetFilePointerEx
        SetFileTime SetLastError SetStdHandle SetThreadPriority SetThreadpoolTimer SetThreadpoolWait
        SetUnhandledExceptionFilter Sleep SleepConditionVariableSRW SuspendThread SwitchToThread
        SystemTimeToTzSpecificLocalTime TerminateProcess TlsAlloc TlsFree TlsGetValue TlsSetValue
        TryAcquireSRWLockExclusive TryEnterCriticalSection UnhandledExceptionFilter VirtualAlloc VirtualFree
        VirtualProtect VirtualQuery WaitForSingleObject WaitForSingleObjectEx WaitForThreadpoolTimerCallbacks
        WakeAllConditionVariable WakeConditionVariable WideCharToMultiByte WriteConsoleW WriteFile
        WritePrivateProfileStringW
    """.split()),
    "user32.dll": frozenset("""
        CallNextHookEx CallWindowProcA CallWindowProcW CharLowerBuffW EnumWindows GetAncestor GetAsyncKeyState
        GetClassLongA GetClassLongW GetClassNameW GetCursorPos GetForegroundWindow GetLastInputInfo GetMonitorInfoW
        GetSystemMetrics GetWindowLongW GetWindowThreadProcessId InSendMessageEx IsHungAppWindow IsIconic IsWindow
        IsWindowUnicode MessageBoxW MonitorFromPoint MonitorFromWindow SetRect SetWindowLongA SetWindowLongW
        SetWindowsHookExW UnhookWindowsHookEx WindowFromPoint
    """.split()),
    "gdi32.dll": frozenset("""
        CreateCompatibleDC CreateDIBSection CreateFontW DeleteDC DeleteObject GdiFlush GetCharABCWidthsW
        GetCharWidth32W GetGlyphOutlineW GetTextMetricsW SelectObject SetBkMode SetTextAlign SetTextColor TextOutW
    """.split()),
    "advapi32.dll": frozenset({"RegGetValueW"}),
}


def missing(data: bytes) -> list[str]:
    """The imports of a program or DLL that are not vetted for Windows 10, as ``DLL!function`` (``DLL!#N`` for one
    imported by number), in the file's order; [] when every one is.  FormatError when it is not a PE file."""
    out = []
    for dll, names in pe.imports(data).items():
        known = VETTED.get(dll.lower(), frozenset())
        out += [f"{dll}!{n}" for n in names if n not in known]
    return out


def problems(data: bytes) -> list[str]:
    """Why a program or DLL might not load on Windows 10, in words ([] when nothing does): a header that asks for
    a newer Windows, each import not vetted.  FormatError when it is not a PE file."""
    out = []
    wants = pe.required_os(data)
    if wants > OLDEST_WINDOWS:
        out.append(f"its header asks for Windows {wants[0]}.{wants[1]} or later")
    out += [f"it imports {name}, which is not checked to exist on Windows 10" for name in missing(data)]
    return out
