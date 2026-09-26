// Ninput's xinput1_3.dll personality -- the base one, since both DDDA.exe and DDO.exe import
// XINPUT1_3.dll by ordinal. Deploy Ninput as xinput1_3.dll next to the game and the Riftstone
// loader can still own the dinput8.dll slot; the two never contend.
//
// Every export is a signature-agnostic naked jump to the real function in the *system*
// xinput1_3.dll (loaded from the real system directory, so we never recurse into ourselves).
// DllMain boots the Ninput core once, which builds the plugin interface and loads plugins.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "input_engine.hpp"
#include "ninput_core.hpp"

namespace {

// Real addresses in the system xinput1_3.dll. Named exports resolve by name; 100..103 (unnamed
// in the OS DLL) resolve by ordinal. A slot left null points at a safe stub that returns 0.
void* r_XInputGetState;
void* r_XInputSetState;
void* r_XInputGetCapabilities;
void* r_XInputEnable;
void* r_XInputGetDSoundAudioDeviceGuids;
void* r_XInputGetBatteryInformation;
void* r_XInputGetKeystroke;
void* r_XInputGetStateEx;
void* r_XInputWaitForGuideButton;
void* r_XInputCancelGuideButtonWait;
void* r_XInputPowerOffController;

// If a real export is missing, forwarders jump here. XInput calls are __stdcall returning DWORD;
// clearing eax to 0 (ERROR_SUCCESS) and returning lets the game proceed. The largest XInput
// prototype takes 3 args (12 bytes), so `ret 12` cleans the deepest stdcall frame; smaller
// callers tolerate the same epilogue in practice, and this path only runs if the OS DLL lacks an
// export it has shipped since Windows 7 -- i.e. effectively never.
__declspec(naked) void t_missing() {
    __asm {
        xor eax, eax
        ret 12
    }
}

}  // namespace

// XInputGetState is a real wrapper (not a blind forwarder): it calls the real function, then hands
// the pad to the input engine so hotkeys fire and transforms can rewrite what the game reads. This
// is what makes Ninput the input layer for the game, not just a passthrough.
extern "C" DWORD WINAPI nx_XInputGetState(DWORD index, ninput::RawXInputState* state) {
    auto real = reinterpret_cast<DWORD(WINAPI*)(DWORD, ninput::RawXInputState*)>(r_XInputGetState);
    DWORD r = real(index, state);
    ninput::input_feed_state((int)index, r, state);
    return r;
}

// Every other export is a signature-agnostic naked jump through the resolved pointer.
#define FORWARD(name) \
    extern "C" __declspec(naked) void nx_##name() { __asm { jmp dword ptr [r_##name] } }
FORWARD(XInputSetState)
FORWARD(XInputGetCapabilities)
FORWARD(XInputEnable)
FORWARD(XInputGetDSoundAudioDeviceGuids)
FORWARD(XInputGetBatteryInformation)
FORWARD(XInputGetKeystroke)
FORWARD(XInputGetStateEx)
FORWARD(XInputWaitForGuideButton)
FORWARD(XInputCancelGuideButtonWait)
FORWARD(XInputPowerOffController)
#undef FORWARD

static void resolve() {
    HMODULE sys = ninput::load_system_dll(L"xinput1_3.dll");
    if (!sys) {
        ninput::core_log("xinput   could not load the system xinput1_3.dll (error %lu); forwards stubbed",
                         GetLastError());
    }
    auto by_name = [&](const char* n) -> void* {
        void* p = sys ? (void*)GetProcAddress(sys, n) : nullptr;
        return p ? p : (void*)&t_missing;
    };
    auto by_ord = [&](WORD o) -> void* {
        void* p = sys ? (void*)GetProcAddress(sys, MAKEINTRESOURCEA(o)) : nullptr;
        return p ? p : (void*)&t_missing;
    };
    r_XInputGetState = by_name("XInputGetState");
    r_XInputSetState = by_name("XInputSetState");
    r_XInputGetCapabilities = by_name("XInputGetCapabilities");
    r_XInputEnable = by_name("XInputEnable");
    r_XInputGetDSoundAudioDeviceGuids = by_name("XInputGetDSoundAudioDeviceGuids");
    r_XInputGetBatteryInformation = by_name("XInputGetBatteryInformation");
    r_XInputGetKeystroke = by_name("XInputGetKeystroke");
    r_XInputGetStateEx = by_ord(100);
    r_XInputWaitForGuideButton = by_ord(101);
    r_XInputCancelGuideButtonWait = by_ord(102);
    r_XInputPowerOffController = by_ord(103);
}

BOOL APIENTRY DllMain(HMODULE self, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(self);
        resolve();
        ninput::core_attach(self);
    }
    return TRUE;
}
