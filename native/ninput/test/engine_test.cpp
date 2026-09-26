// Offline proof of the engine capability registry (pillar 5). A mock provider stands in for the
// verified enemy_cap / lod_tuner plugins: it records what it was asked and returns a code, exactly
// as a real provider would after byte-checking the build. No game involved.
#include <cstdio>

#include "hook_registry.hpp"
#include "ninput.h"

static int g_fails = 0;
static void check(bool ok, const char* label) {
    std::printf("  %s  %s\n", ok ? "pass" : "FAIL", label);
    if (!ok) ++g_fails;
}

static int g_seen_slots = -1;
static int g_cap_ret = NINPUT_OK;
static int mock_enemy_cap(int slots) {
    g_seen_slots = slots;
    return g_cap_ret;  // a real provider returns its own byte-check / apply result here
}

static float g_seen_mult = 0.0f;
static int mock_lod(float m) {
    g_seen_mult = m;
    return NINPUT_OK;
}

int main() {
    const NinputInterface* n = ninput::make_interface(NINPUT_GAME_DDDA_2364871, 0x400000,
                                                      [](const char*) {});
    check(n->engine->game == NINPUT_GAME_DDDA_2364871, "engine reports the detected game");

    std::puts("no provider yet");
    check(n->engine->set_enemy_cap(30) == NINPUT_ERR_UNIMPLEMENTED,
          "set_enemy_cap with no provider is UNIMPLEMENTED (core never writes)");

    std::puts("a verified plugin registers as the provider");
    check(n->engine->provide_enemy_cap(mock_enemy_cap) == NINPUT_OK, "provider registration accepted");
    int r = n->engine->set_enemy_cap(30);
    check(g_seen_slots == 30 && r == NINPUT_OK, "the consumer call routes to the provider with its argument");

    std::puts("the provider's own verdict propagates");
    g_cap_ret = NINPUT_ERR_HOOK_FAILED;  // e.g. the byte-check refused a non-2364871 build
    check(n->engine->set_enemy_cap(48) == NINPUT_ERR_HOOK_FAILED && g_seen_slots == 48,
          "a provider's refusal reaches the caller unchanged");

    std::puts("clearing the provider");
    n->engine->provide_enemy_cap(nullptr);
    check(n->engine->set_enemy_cap(30) == NINPUT_ERR_UNIMPLEMENTED, "cleared provider returns to UNIMPLEMENTED");

    std::puts("a float capability routes the same way");
    n->engine->provide_lod_distance_multiplier(mock_lod);
    check(n->engine->set_lod_distance_multiplier(3.0f) == NINPUT_OK && g_seen_mult == 3.0f,
          "set_lod_distance_multiplier routes the float to its provider");
    check(n->engine->set_shadow_map_size(4096) == NINPUT_ERR_UNIMPLEMENTED,
          "an unprovided capability stays UNIMPLEMENTED");

    std::printf("\n%s (%d checks failed)\n", g_fails ? "FAILED" : "ALL PASSED", g_fails);
    return g_fails ? 1 : 0;
}
