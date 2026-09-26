// The D3D9 display arbiter. Ninput owns the one hook on IDirect3DDevice9::Reset; every plugin
// that draws registers a lost/reset pair and is called around each device reset, instead of each
// plugin hooking Reset itself and fighting for the device (the classic fullscreen/alt-tab crash).
//
// Reach: hook Direct3DCreate9 -> the returned IDirect3D9's CreateDevice (vtable 16) -> the created
// device's Reset (vtable 16). All done from the game's own objects; no engine address is guessed.
#pragma once

#include "ninput.h"

namespace ninput {

void display_arbiter_start();  // loads d3d9.dll and hooks Direct3DCreate9 (and the loader's [d3d9] chain's)

// The Riftstone loader's [d3d9] chain named in <root>\riftstone_loader.ini: the game's Direct3D 9 comes from
// that DLL (e.g. DXVK's d3d9.dll), so its Direct3DCreate9 is hooked too.  True when it is watched.
bool display_watch_chain(const wchar_t* root);
bool display_chain_hooked();

// Backs the NinputDisplay table handed to plugins.
int   display_register(NinputDisplayFn on_lost, NinputDisplayFn on_reset, void* user);
void  display_unregister(int handle);
void* display_device();

// Offline-harness introspection and drivers (no game).
bool display_create9_hooked();
bool display_create_device_hooked();
bool display_reset_hooked();
int  display_callback_count();
void display_simulate_reset();  // runs the lost->reset broadcast without a real device

}  // namespace ninput
