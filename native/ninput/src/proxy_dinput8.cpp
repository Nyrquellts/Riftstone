// Ninput's dinput8.dll personality -- an alternate for users who cannot use the xinput1_3 slot
// (e.g. it is taken by another tool). Standalone: forwards to the system dinput8.dll. If you run
// the Riftstone loader (which itself owns dinput8.dll), deploy Ninput as xinput1_3.dll instead so
// the two do not contend for one slot.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "ninput_core.hpp"

namespace {
void* r_DirectInput8Create;
void* r_DllCanUnloadNow;
void* r_DllGetClassObject;
void* r_DllRegisterServer;
void* r_DllUnregisterServer;
void* r_GetdfDIJoystick;
}  // namespace

#define FORWARD(name) \
    extern "C" __declspec(naked) void nd_##name() { __asm { jmp dword ptr [r_##name] } }
FORWARD(DirectInput8Create)
FORWARD(DllCanUnloadNow)
FORWARD(DllGetClassObject)
FORWARD(DllRegisterServer)
FORWARD(DllUnregisterServer)
FORWARD(GetdfDIJoystick)
#undef FORWARD

static void resolve() {
    HMODULE sys = ninput::load_system_dll(L"dinput8.dll");
    if (!sys) {
        ninput::core_log("dinput8  could not load the system dinput8.dll (error %lu)", GetLastError());
        return;
    }
    r_DirectInput8Create = (void*)GetProcAddress(sys, "DirectInput8Create");
    r_DllCanUnloadNow = (void*)GetProcAddress(sys, "DllCanUnloadNow");
    r_DllGetClassObject = (void*)GetProcAddress(sys, "DllGetClassObject");
    r_DllRegisterServer = (void*)GetProcAddress(sys, "DllRegisterServer");
    r_DllUnregisterServer = (void*)GetProcAddress(sys, "DllUnregisterServer");
    r_GetdfDIJoystick = (void*)GetProcAddress(sys, "GetdfDIJoystick");
}

BOOL APIENTRY DllMain(HMODULE self, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(self);
        resolve();
        ninput::core_attach(self);
    }
    return TRUE;
}
