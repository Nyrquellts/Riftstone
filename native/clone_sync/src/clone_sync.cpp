#define RS_CLONE_BUILD
#include "clone_sync.hpp"
#include <MinHook.h>
#include <cmath>
#include <cstring>
#include <set>

using namespace riftstone::clone;
namespace {
// Enable, Disable and the state below.  No lock is ever held while the factory runs: it may call the hooked
// factory again (on its own thread, or on one it waits for) or RsClone_Disable, and a lock held across the
// call made those deadlock, or throw inside a noexcept function (std::terminate).  An SRW lock and a
// condition variable never throw.
SRWLOCK lifecycle=SRWLOCK_INIT;
CONDITION_VARIABLE settled=CONDITION_VARIABLE_INIT;    // a synchronization in progress has finished
std::atomic<Factory> original{nullptr};                // the trampoline: set before the hook goes on, never after
void* target=nullptr;
bool installed=false, enabled=false;
unsigned syncing=0;                                    // results being synchronized now (no caller code runs there)

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
    // The factory runs with no lock held.  Its result is synchronized only while the hook is still on when it
    // comes back, and that synchronization is counted, so Disable can wait for it; the trampoline stays
    // allocated for entries already dispatched by Windows.
    const Factory call=original.load(std::memory_order_acquire);
    Actor* actor=nullptr;
    try { actor=call(source,incompatible); } catch (...) { return nullptr; }
    if (!actor) return actor;
    AcquireSRWLockExclusive(&lifecycle);
    const bool sync=enabled;
    if (sync) ++syncing;
    ReleaseSRWLockExclusive(&lifecycle);
    if (!sync) return actor;
    actor->last_sync=RsClone_Synchronize(source,actor);
    AcquireSRWLockExclusive(&lifecycle);
    --syncing;
    ReleaseSRWLockExclusive(&lifecycle);
    WakeAllConditionVariable(&settled);
    return actor;
}

Status enable(HMODULE host) {                          // under lifecycle
    if (installed) return Status::busy;
    if (!host || host!=GetModuleHandleW(nullptr)) return Status::invalid;
    // This explicit fixture export is the entire supported ABI/profile boundary.
    target=reinterpret_cast<void*>(GetProcAddress(host,"RsSyntheticCreateCloneV1"));
    auto* marker=reinterpret_cast<const char*>(GetProcAddress(host,"RsSyntheticCloneAbiV1"));
    if (!target || !marker || std::strcmp(marker,"riftstone.synthetic-clone/1")) return Status::unavailable;
    if (MH_Initialize()!=MH_OK) return Status::hook_failed;
    void* trampoline=nullptr;
    if (MH_CreateHook(target,reinterpret_cast<void*>(intercept),&trampoline)!=MH_OK) {
        MH_Uninitialize(); return Status::hook_failed;
    }
    original.store(reinterpret_cast<Factory>(trampoline),std::memory_order_release);
    HMODULE pinned;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,
        reinterpret_cast<LPCWSTR>(&RsClone_EnableSyntheticHost),&pinned) || MH_EnableHook(target)!=MH_OK) {
        MH_RemoveHook(target); MH_Uninitialize(); original.store(nullptr); return Status::hook_failed;
    }
    installed=true; enabled=true; return Status::ok;
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
    AcquireSRWLockExclusive(&lifecycle);
    const Status status=enable(host);
    ReleaseSRWLockExclusive(&lifecycle);
    return status;
}
RS_CLONE_API Status __cdecl RsClone_Disable() noexcept {
    // Callable from inside a factory call, on any thread.  It waits only for results being synchronized
    // (they run no caller code); a call still inside the factory gives its result back unsynchronized.  Not
    // to be called while holding an Actor's mutex: a synchronization in progress may be waiting for it.
    AcquireSRWLockExclusive(&lifecycle);
    Status status=Status::ok;
    if (enabled) {
        if (MH_DisableHook(target)!=MH_OK) status=Status::hook_failed;
        else {
            enabled=false;
            while (syncing) SleepConditionVariableSRW(&settled,&lifecycle,INFINITE,0);
        }
    }
    ReleaseSRWLockExclusive(&lifecycle);
    // Intentionally retain module and trampoline until process exit.
    return status;
}
BOOL APIENTRY DllMain(HMODULE module,DWORD reason,LPVOID) {
    if (reason==DLL_PROCESS_ATTACH) DisableThreadLibraryCalls(module);
    return TRUE;
}
