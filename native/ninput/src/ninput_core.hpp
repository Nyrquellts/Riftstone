// Shared core for every Ninput proxy personality (xinput1_3 / dinput8 / version). Each
// personality is the same DLL with a different export set; its DllMain calls attach() once.
#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

namespace ninput {

// Called from a proxy personality's DllMain(DLL_PROCESS_ATTACH). Idempotent: the first call
// detects the game, builds the plugin interface and loads the Ninput plugins; later calls (a
// second personality in the same process, unusual) are no-ops.
void core_attach(HMODULE self);

// Load a real system DLL by name from the actual system directory -- never from the game folder,
// so a proxy named xinput1_3.dll cannot recursively load itself.
HMODULE load_system_dll(const wchar_t* name);

// One timestamped line into <game>\ninput\ninput.log.
void core_log(const char* fmt, ...);

// The game folder (the host exe's folder, no trailing slash); empty before core_attach.
const wchar_t* core_root();

}  // namespace ninput
