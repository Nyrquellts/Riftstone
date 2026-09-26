#pragma once
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <vector>

#ifdef RS_CLONE_BUILD
#define RS_CLONE_API extern "C" __declspec(dllexport)
#else
#define RS_CLONE_API extern "C" __declspec(dllimport)
#endif

namespace riftstone::clone {
enum class Status : int { ok, invalid, skeleton_mismatch, retired, no_memory, unavailable, hook_failed, busy };
// This is an owned host-side ABI. None of these fields are offsets in DDDA.
struct Joint { uint32_t id; int32_t parent; bool operator==(const Joint&) const = default; };
enum class Kind { mesh, material };
struct Resource { Kind kind; uint64_t identity; std::vector<uint8_t> bytes; };
struct Weight { uint16_t joint[4]; float value[4]; };
struct Part {
    std::shared_ptr<const Resource> mesh;
    std::shared_ptr<const Resource> material;
    std::vector<Weight> weights;
};
struct Appearance {
    std::vector<Joint> skeleton;
    std::vector<Part> equipment;
    std::vector<float> morphs;
};
struct Actor {
    std::mutex mutex;
    std::vector<Joint> skeleton;
    std::shared_ptr<const Appearance> appearance;
    uint64_t generation=0;
    uint64_t entity_id=0;
    float animation_frame=0;
    bool alive=true, render_ready=false;
    std::atomic<Status> last_sync=Status::unavailable;
};
using Factory = Actor* (__cdecl*)(Actor*, bool);
}

RS_CLONE_API riftstone::clone::Status __cdecl RsClone_Synchronize(riftstone::clone::Actor* source, riftstone::clone::Actor* destination) noexcept;
RS_CLONE_API riftstone::clone::Status __cdecl RsClone_Retire(riftstone::clone::Actor* actor) noexcept;
// The only installable profile is the deliberately exported synthetic host ABI.
RS_CLONE_API riftstone::clone::Status __cdecl RsClone_EnableSyntheticHost(HMODULE host) noexcept;
RS_CLONE_API riftstone::clone::Status __cdecl RsClone_Disable() noexcept;
RS_CLONE_API riftstone::clone::Status __cdecl RsClone_GameProfile(const char* profile) noexcept;
