// Ninput's version.dll personality -- another alternate slot. DDO.exe imports version.dll (DDDA
// does not), so this is mainly for the DDO port; it also works as a generic slot. Forwards to the
// system version.dll (VerLanguageNameA/W are themselves forwarded to kernel32 there, which
// GetProcAddress resolves, so the jump still lands correctly).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "ninput_core.hpp"

namespace {
void* r_GetFileVersionInfoA;
void* r_GetFileVersionInfoByHandle;
void* r_GetFileVersionInfoExA;
void* r_GetFileVersionInfoExW;
void* r_GetFileVersionInfoSizeA;
void* r_GetFileVersionInfoSizeExA;
void* r_GetFileVersionInfoSizeExW;
void* r_GetFileVersionInfoSizeW;
void* r_GetFileVersionInfoW;
void* r_VerFindFileA;
void* r_VerFindFileW;
void* r_VerInstallFileA;
void* r_VerInstallFileW;
void* r_VerLanguageNameA;
void* r_VerLanguageNameW;
void* r_VerQueryValueA;
void* r_VerQueryValueW;
}  // namespace

#define FORWARD(name) \
    extern "C" __declspec(naked) void nv_##name() { __asm { jmp dword ptr [r_##name] } }
FORWARD(GetFileVersionInfoA)
FORWARD(GetFileVersionInfoByHandle)
FORWARD(GetFileVersionInfoExA)
FORWARD(GetFileVersionInfoExW)
FORWARD(GetFileVersionInfoSizeA)
FORWARD(GetFileVersionInfoSizeExA)
FORWARD(GetFileVersionInfoSizeExW)
FORWARD(GetFileVersionInfoSizeW)
FORWARD(GetFileVersionInfoW)
FORWARD(VerFindFileA)
FORWARD(VerFindFileW)
FORWARD(VerInstallFileA)
FORWARD(VerInstallFileW)
FORWARD(VerLanguageNameA)
FORWARD(VerLanguageNameW)
FORWARD(VerQueryValueA)
FORWARD(VerQueryValueW)
#undef FORWARD

static void resolve() {
    HMODULE sys = ninput::load_system_dll(L"version.dll");
    if (!sys) {
        ninput::core_log("version  could not load the system version.dll (error %lu)", GetLastError());
        return;
    }
#define R(name) r_##name = (void*)GetProcAddress(sys, #name)
    R(GetFileVersionInfoA); R(GetFileVersionInfoByHandle); R(GetFileVersionInfoExA);
    R(GetFileVersionInfoExW); R(GetFileVersionInfoSizeA); R(GetFileVersionInfoSizeExA);
    R(GetFileVersionInfoSizeExW); R(GetFileVersionInfoSizeW); R(GetFileVersionInfoW);
    R(VerFindFileA); R(VerFindFileW); R(VerInstallFileA); R(VerInstallFileW);
    R(VerLanguageNameA); R(VerLanguageNameW); R(VerQueryValueA); R(VerQueryValueW);
#undef R
}

BOOL APIENTRY DllMain(HMODULE self, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(self);
        resolve();
        ninput::core_attach(self);
    }
    return TRUE;
}
