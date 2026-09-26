/* Ninput plugin SDK -- the ABI a community plugin compiles against.
 *
 * Ninput is Riftstone's plugin host for Dragon's Dogma: Dark Arisen (DDDA.exe, x86).
 * It is one 32-bit DLL that the game loads through a proxy export (xinput1_3.dll by
 * default; dinput8.dll or version.dll as alternates), and it hands every plugin this
 * interface so plugins hook the game through one arbitrated registry instead of each
 * compiling its own dinput8 and racing the others.
 *
 * A plugin is a .dll/.asi that exports:
 *     extern "C" __declspec(dllexport) int Ninput_Initialize(const NinputInterface* nyr);
 * Return non-zero to stay loaded. Everything the plugin needs is reachable from `nyr`.
 *
 * This header is plain C and depends only on <stdint.h>/<stddef.h> so a plugin never
 * needs SafetyHook or the Ninput sources to build against it.
 *
 * Honesty: fields marked UNIMPLEMENTED return NINPUT_ERR_UNIMPLEMENTED until the exact
 * engine address is verified in game (shipping a guessed native write crashes the game).
 * A plugin should feature-detect on the return code rather than assume.
 */
#ifndef NINPUT_H
#define NINPUT_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define NINPUT_ABI_VERSION 1u

/* Which game the host is running in (build-locked; a wrong build is refused, not guessed). */
enum {
    NINPUT_GAME_UNKNOWN      = 0,
    NINPUT_GAME_DDDA_2364871 = 1,  /* Steam Dark Arisen, PE timestamp 0x5A314C31 */
    NINPUT_GAME_DDO          = 2   /* design-only for now; see README */
};

/* Return codes. >= 0 is a handle or success; < 0 is a reason. */
enum {
    NINPUT_OK               =  0,
    NINPUT_ERR_BADARG       = -1,
    NINPUT_ERR_ADDRESS_TAKEN= -2,  /* another plugin already holds an exclusive detour here */
    NINPUT_ERR_NO_SLOTS     = -3,  /* the shared mid-hook pool is full */
    NINPUT_ERR_HOOK_FAILED  = -4,  /* the trampoline could not be built at this address */
    NINPUT_ERR_UNIMPLEMENTED= -5,  /* known but not yet verified in game */
    NINPUT_ERR_ALREADY_APPLIED= -6 /* a one-shot capability was already applied at a fixed value */
};

/* x86 register block handed to a mid-hook callback. Layout mirrors the host's hook backend
 * exactly (asserted at host build time); read or write fields to steer the hooked code.
 * eip points into the trampoline that holds the relocated original instruction(s); esp is
 * read-only (assign trampoline_esp to move the stack). */
/* 16 bytes, 8-byte aligned -- laid out to match the host hook backend's XMM slot exactly. */
typedef union NinputXmm { uint8_t u8[16]; uint64_t u64[2]; double f64[2]; } NinputXmm;
typedef struct NinputRegs32 {
    NinputXmm xmm0, xmm1, xmm2, xmm3, xmm4, xmm5, xmm6, xmm7;
    uintptr_t eflags, edi, esi, edx, ecx, ebx, eax, ebp, esp, trampoline_esp, eip;
} NinputRegs32;

typedef struct NinputPad {
    uint32_t packet;   /* XInput packet number (unchanged == no new input) */
    uint16_t buttons;  /* XINPUT_GAMEPAD_* bitmask, incl. paddle bits when the pad reports them */
    uint8_t  lt, rt;   /* triggers 0..255 */
    int16_t  lx, ly, rx, ry;
} NinputPad;

typedef void (*NinputMidFn)(NinputRegs32* regs, void* user);
typedef void (*NinputDisplayFn)(void* device, void* user); /* IDirect3DDevice9* around a reset */

/* --- hooks: the one arbiter every plugin shares ------------------------------------ */
typedef struct NinputHooks {
    /* Shared mid-hook. Many plugins may register at the same address; all of their callbacks
     * run, in registration order, off a single real hook the host owns. Returns a handle >= 0. */
    int  (*register_mid)(uintptr_t address, NinputMidFn fn, void* user, const char* owner);

    /* Exclusive detour that replaces a function. *original receives a pointer callable as the
     * original (the relocated prologue + jump back). Refused with NINPUT_ERR_ADDRESS_TAKEN if
     * another plugin already detours this address -- no plugin ever silently clobbers another. */
    int  (*register_inline)(uintptr_t address, void* detour, void** original, const char* owner);

    /* Remove a registration by its handle. Removing the last mid callback at an address frees
     * the underlying hook; removing an exclusive detour restores the original bytes. */
    void (*unregister)(int handle);
} NinputHooks;

/* --- engine: the reversed systems, exposed as feature-detectable calls --------------
 *
 * A capability is split in two: a *provider* plugin (one that has the verified, build-locked
 * native write -- e.g. enemy_cap, lod_tuner) registers its implementation; any *consumer* plugin
 * then calls the matching setter, which routes to the provider. If no provider registered, the
 * setter returns NINPUT_ERR_UNIMPLEMENTED, so Ninput core never performs a guessed native write
 * itself -- the crash risk stays inside the verified provider, which byte-checks the build. */
typedef int (*NinputEnemyCapFn)(int slots);
typedef int (*NinputShadowSizeFn)(uint32_t px);
typedef int (*NinputShadowDistFn)(float meters);
typedef int (*NinputLodMultFn)(float multiplier);

typedef struct NinputEngine {
    int       game;        /* NINPUT_GAME_* */
    uintptr_t image_base;  /* base of the game module */

    /* consumer side: route to the registered provider, or NINPUT_ERR_UNIMPLEMENTED if none. */
    int (*set_enemy_cap)(int slots);
    int (*set_shadow_map_size)(uint32_t px);
    int (*set_shadow_distance)(float meters);
    int (*set_lod_distance_multiplier)(float multiplier);

    /* provider side: a verified plugin registers what it can actually do on this build.
     * Registering replaces any previous provider; passing NULL clears it. Returns NINPUT_OK. */
    int (*provide_enemy_cap)(NinputEnemyCapFn fn);
    int (*provide_shadow_map_size)(NinputShadowSizeFn fn);
    int (*provide_shadow_distance)(NinputShadowDistFn fn);
    int (*provide_lod_distance_multiplier)(NinputLodMultFn fn);
} NinputEngine;

/* --- display: the D3D9 arbiter so plugins survive a device reset -------------------- */
typedef struct NinputDisplay {
    /* The host owns the one hook on IDirect3DDevice9::Reset. Around each reset it calls on_lost
     * (release your D3DPOOL_DEFAULT resources) then on_reset (recreate them). Returns a handle. */
    int   (*register_callbacks)(NinputDisplayFn on_lost, NinputDisplayFn on_reset, void* user);
    void  (*unregister)(int handle);
    void* (*device)(void); /* current IDirect3DDevice9*, or NULL before the device exists */
} NinputDisplay;

/* A hotkey fires once when every button in its chord mask becomes held (rising edge). A transform
 * may rewrite the pad the game is about to read -- remaps, macros, dead-zone changes. Both are fed
 * by the game's own per-frame XInputGetState polling through the proxy. */
typedef void (*NinputHotkeyFn)(int slot, void* user);
typedef void (*NinputTransformFn)(int slot, NinputPad* inout, void* user);

typedef struct NinputInput {
    int (*get_state)(int slot, NinputPad* out);  /* 0 == a controller is connected on that slot */

    /* buttons is an XINPUT_GAMEPAD_* bitmask; the callback fires when all of them are held. */
    int (*register_hotkey)(uint16_t buttons, NinputHotkeyFn fn, void* user);
    /* transforms run in registration order; each may edit *inout before the game sees it. */
    int (*register_transform)(NinputTransformFn fn, void* user);
    void (*unregister)(int handle);
} NinputInput;

typedef struct NinputMemory {
    void* (*alloc)(size_t n);
    void  (*release)(void* p);
} NinputMemory;

typedef struct NinputInterface {
    uint32_t abi_version;   /* == NINPUT_ABI_VERSION; a plugin must check this first */
    uint32_t host_version;  /* Ninput build, packed 0xAABBCC */
    const NinputHooks*   hooks;
    const NinputEngine*  engine;
    const NinputDisplay* display;
    const NinputInput*   input;
    const NinputMemory*  memory;
    void (*log)(const char* message); /* writes one line to riftstone\logs\ninput.log */
} NinputInterface;

#define NINPUT_PLUGIN_INIT "Ninput_Initialize"
typedef int (*Ninput_Initialize_t)(const NinputInterface* nyr);

#ifdef __cplusplus
}
#endif
#endif /* NINPUT_H */
