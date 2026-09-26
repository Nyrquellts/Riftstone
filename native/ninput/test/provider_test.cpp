// Offline proof that the real enemy_cap and lod_tuner plugins register as Ninput engine providers
// and that the consumer setters route to them. No game: enemy_cap's byte-check refuses off the real
// exe (the honest offline result -- it applies in game), while lod_tuner's runtime multiplier is a
// plain variable the hook reads, so it is fully exercised here.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstdio>

#include "hook_registry.hpp"
#include "ninput.h"

static int g_fails = 0;
static void check(bool ok, const char* label) {
    std::printf("  %s  %s\n", ok ? "pass" : "FAIL", label);
    if (!ok) ++g_fails;
}

using Init_t = int (*)(const NinputInterface*);
using LodMult_t = float (*)();

int main() {
    wchar_t exe[MAX_PATH];
    GetModuleFileNameW(nullptr, exe, MAX_PATH);
    if (wchar_t* s = wcsrchr(exe, L'\\')) *s = 0;
    SetCurrentDirectoryW(exe);  // the .asi files sit next to this harness

    const NinputInterface* n = ninput::make_interface(NINPUT_GAME_DDDA_2364871, 0x00400000,
                                                      [](const char*) {});

    std::puts("before any provider loads");
    check(n->engine->set_enemy_cap(30) == NINPUT_ERR_UNIMPLEMENTED, "set_enemy_cap is UNIMPLEMENTED");

    std::puts("enemy_cap registers as the enemy-cap provider");
    HMODULE ec = LoadLibraryW(L"enemy_cap.asi");
    check(ec != nullptr, "enemy_cap.asi loaded");
    auto ec_init = ec ? reinterpret_cast<Init_t>(GetProcAddress(ec, "Ninput_Initialize")) : nullptr;
    check(ec_init && ec_init(n) == 1, "enemy_cap Ninput_Initialize registered a provider");
    int r = n->engine->set_enemy_cap(30);
    check(r != NINPUT_ERR_UNIMPLEMENTED, "set_enemy_cap now routes to enemy_cap (no longer UNIMPLEMENTED)");
    std::printf("    offline set_enemy_cap(30) -> %d (its byte-check refuses off the real exe; applies in game)\n", r);

    std::puts("lod_tuner registers as the LOD-multiplier provider (runtime-adjustable)");
    HMODULE lt = LoadLibraryW(L"lod_tuner.asi");
    check(lt != nullptr, "lod_tuner.asi loaded");
    auto lt_init = lt ? reinterpret_cast<Init_t>(GetProcAddress(lt, "Ninput_Initialize")) : nullptr;
    check(lt_init && lt_init(n) == 1, "lod_tuner Ninput_Initialize registered a provider");
    auto mult = lt ? reinterpret_cast<LodMult_t>(GetProcAddress(lt, "LodTuner_Multiplier")) : nullptr;
    check(n->engine->set_lod_distance_multiplier(3.0f) == NINPUT_OK, "set_lod_distance_multiplier routes and returns OK");
    check(mult && mult() == 3.0f, "the multiplier the game's model loader will read is now x3.0");
    check(n->engine->set_lod_distance_multiplier(1.5f) == NINPUT_OK && mult && mult() == 1.5f,
          "it can be changed again at runtime");

    std::puts("an unprovided capability is still honest");
    check(n->engine->set_shadow_map_size(4096) == NINPUT_ERR_UNIMPLEMENTED, "set_shadow_map_size stays UNIMPLEMENTED");

    std::printf("\n%s (%d checks failed)\n", g_fails ? "FAILED" : "ALL PASSED", g_fails);
    return g_fails ? 1 : 0;
}
