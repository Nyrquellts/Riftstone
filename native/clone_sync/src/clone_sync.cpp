#define RS_CLONE_BUILD
#include "clone_sync.hpp"
#include <MinHook.h>
#include <cmath>
#include <cstring>
#include <set>

using namespace riftstone::clone;
namespace {
std::mutex hook_mutex;
Factory original=nullptr;
void* target=nullptr;
bool installed=false, enabled=false;

bool valid(const Appearance& appearance) {
    if (appearance.skeleton.empty() || appearance.skeleton.size()>4096 || appearance.equipment.empty() ||
        appearance.equipment.size()>128 || appearance.morphs.size()>65536) return false;
    std::set<uint32_t> ids;
    for (size_t i=0;i<appearance.skeleton.size();++i) {
        const auto joint=appearance.skeleton[i];
        if (!ids.insert(joint.id).second || joint.parent < -1 || joint.parent>=static_cast<int32_t>(i)) return false;
    }
    for (float morph:appearance.morphs) if (!std::isfinite(morph)) return false;
    size_t total=0;
    for (const auto& part:appearance.equipment) {
        if (!part.mesh || !part.material || part.mesh->kind!=Kind::mesh || part.material->kind!=Kind::material ||
            part.mesh->bytes.empty() || part.material->bytes.empty() || part.weights.empty()) return false;
        if (part.weights.size()>1000000-total) return false;
        total+=part.weights.size();
        for (const auto& weights:part.weights) {
            double sum=0;
            for (int i=0;i<4;++i) {
                const float value=weights.value[i];
                if (!std::isfinite(value) || value<0 || value>1 || weights.joint[i]>=appearance.skeleton.size()) return false;
                sum+=value;
            }
            if (std::abs(sum-1)>0.00001) return false;
        }
    }
    return true;
}
Actor* __cdecl intercept(Actor* source, bool incompatible) noexcept {
    // Lifecycle lock covers the call. Disable waits for all accepted calls and
    // leaves the trampoline allocated for entries already dispatched by Windows.
    std::lock_guard lock(hook_mutex);
    Actor* actor=nullptr;
    try { actor=original(source,incompatible); } catch (...) { return nullptr; }
    if (enabled && actor) actor->last_sync=RsClone_Synchronize(source,actor);
    return actor;
}
}

RS_CLONE_API Status __cdecl RsClone_Synchronize(Actor* source,Actor* destination) noexcept {
    if (!source || !destination || source==destination) return Status::invalid;
    try {
        std::scoped_lock lock(source->mutex,destination->mutex);
        if (!source->alive || !destination->alive) return Status::retired;
        if (!source->appearance || !valid(*source->appearance)) return Status::invalid;
        if (source->skeleton!=source->appearance->skeleton || destination->skeleton!=source->skeleton) return Status::skeleton_mismatch;
        // Deep-copy mutable morph/weight vectors; retain immutable resource owners.
        auto copied=std::make_shared<const Appearance>(*source->appearance);
        destination->appearance=std::move(copied);
        destination->generation=source->generation;
        destination->render_ready=true;
        return Status::ok;
    } catch (...) { return Status::no_memory; }
}
RS_CLONE_API Status __cdecl RsClone_Retire(Actor* actor) noexcept {
    if (!actor) return Status::invalid;
    std::lock_guard lock(actor->mutex);
    if (!actor->alive) return Status::retired;
    actor->alive=false; actor->render_ready=false; actor->appearance.reset();
    return Status::ok;
}
RS_CLONE_API Status __cdecl RsClone_GameProfile(const char* profile) noexcept {
    if (!profile || !*profile) return Status::invalid;
    // No verified PC instantiation address, refcount ABI, or render-lifecycle
    // profile exists for the requested game clone. Never interpret game memory
    // as this host-side Actor type, even if the executable SHA matches.
    return Status::unavailable;
}
RS_CLONE_API Status __cdecl RsClone_EnableSyntheticHost(HMODULE host) noexcept {
    std::lock_guard lock(hook_mutex);
    if (installed) return Status::busy;
    if (!host || host!=GetModuleHandleW(nullptr)) return Status::invalid;
    // This explicit fixture export is the entire supported ABI/profile boundary.
    target=reinterpret_cast<void*>(GetProcAddress(host,"RsSyntheticCreateCloneV1"));
    auto* marker=reinterpret_cast<const char*>(GetProcAddress(host,"RsSyntheticCloneAbiV1"));
    if (!target || !marker || std::strcmp(marker,"riftstone.synthetic-clone/1")) return Status::unavailable;
    if (MH_Initialize()!=MH_OK) return Status::hook_failed;
    if (MH_CreateHook(target,reinterpret_cast<void*>(intercept),reinterpret_cast<void**>(&original))!=MH_OK) {
        MH_Uninitialize(); return Status::hook_failed;
    }
    HMODULE pinned;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,
        reinterpret_cast<LPCWSTR>(&RsClone_EnableSyntheticHost),&pinned) || MH_EnableHook(target)!=MH_OK) {
        MH_RemoveHook(target); MH_Uninitialize(); original=nullptr; return Status::hook_failed;
    }
    installed=true; enabled=true; return Status::ok;
}
RS_CLONE_API Status __cdecl RsClone_Disable() noexcept {
    std::lock_guard lock(hook_mutex);
    if (!enabled) return Status::ok;
    if (MH_DisableHook(target)!=MH_OK) return Status::hook_failed;
    enabled=false;
    // Intentionally retain module and trampoline until process exit.
    return Status::ok;
}
BOOL APIENTRY DllMain(HMODULE module,DWORD reason,LPVOID) {
    if (reason==DLL_PROCESS_ATTACH) DisableThreadLibraryCalls(module);
    return TRUE;
}
