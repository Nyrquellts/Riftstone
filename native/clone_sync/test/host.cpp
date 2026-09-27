#include "clone_sync.hpp"
#include <cstdio>
#include <cmath>
#include <cstring>
#include <thread>
#include <limits>

using namespace riftstone::clone;
static_assert(sizeof(void*)==4);
int checks=0,failures=0;
void check(bool value,const char* message) {
    ++checks;
    if (!value) { ++failures; std::printf("FAIL: %s\n",message); }
}
// What the factory does inside one call before it builds its actor (clone_host.exe nested <mode>): call
// the hooked factory again on this thread or on another it waits for, or disable the hook from this thread
// or from another it waits for.  One level deep: the nested call builds a plain actor.
enum Nested : int { none, same_thread, other_thread, disable_here, disable_thread };
static std::atomic<int> g_nested{none};
static Factory g_factory=nullptr;                  // the export, reached through the hook while it is on
static std::atomic<int> g_innerSync{-1}, g_disableResult{-1};
static void Inner(Actor* source) {
    std::unique_ptr<Actor> inner(g_factory(source,false));
    g_innerSync=inner?static_cast<int>(inner->last_sync.load()):-2;
}
extern "C" const char RsSyntheticCloneAbiV1[]="riftstone.synthetic-clone/1";
extern "C" __declspec(noinline) Actor* __cdecl RsSyntheticCreateCloneV1(Actor* source,bool incompatible) {
    if (!source) return nullptr;
    switch (g_nested.exchange(none)) {
    case same_thread: Inner(source); break;
    case other_thread: { std::thread t(Inner,source); t.join(); break; }
    case disable_here: g_disableResult=static_cast<int>(RsClone_Disable()); break;
    case disable_thread: { std::thread t([] { g_disableResult=static_cast<int>(RsClone_Disable()); }); t.join(); break; }
    default: break;
    }
    auto actor=std::make_unique<Actor>();
    { std::lock_guard lock(source->mutex); actor->skeleton=source->skeleton; }
    if (incompatible) actor->skeleton[0].id+=100;
    actor->entity_id=991; actor->animation_frame=12.5f;
    return actor.release();
}
std::shared_ptr<Appearance> appearance(uint64_t id=42) {
    auto value=std::make_shared<Appearance>();
    value->skeleton={{11,-1},{12,0}};
    Part part;
    part.mesh=std::make_shared<Resource>(Resource{Kind::mesh,id,{1,2,3}});
    part.material=std::make_shared<Resource>(Resource{Kind::material,id+1000,{4,5,6}});
    part.weights.push_back(Weight{{0,1,0,0},{0.25f,0.75f,0,0}});
    value->equipment.push_back(std::move(part)); value->morphs={float(id),0.125f};
    return value;
}
// clone_host.exe nested <same-thread|other-thread|disable-here|disable-thread>: one factory call through
// the hook that does that inside itself.  run_tests.py gives each its own process and a time limit, so a
// deadlock is a failure, not a hang.
static int NestedMode(const char* mode) {
    const int want=!std::strcmp(mode,"same-thread")?same_thread:!std::strcmp(mode,"other-thread")?other_thread:
                   !std::strcmp(mode,"disable-here")?disable_here:!std::strcmp(mode,"disable-thread")?disable_thread:none;
    if (want==none) { std::printf("NESTED %s unknown\n",mode); return 2; }
    Actor player; player.appearance=appearance(); player.skeleton=player.appearance->skeleton; player.generation=42;
    g_factory=reinterpret_cast<Factory>(GetProcAddress(GetModuleHandleW(nullptr),"RsSyntheticCreateCloneV1"));
    check(g_factory&&RsClone_EnableSyntheticHost(GetModuleHandleW(nullptr))==Status::ok,"factory interception on");
    if (failures) { std::printf("NESTED %s checks=%d failures=%d\n",mode,checks,failures); return 1; }
    g_nested=want;
    std::unique_ptr<Actor> outer(g_factory(&player,false));
    if (want==same_thread||want==other_thread) {
        check(g_innerSync==static_cast<int>(Status::ok),"the factory call made inside a factory call is synchronized");
        check(outer&&outer->last_sync==Status::ok&&outer->appearance&&outer->appearance->equipment[0].mesh->identity==42,
              "the call around it is synchronized too");
    } else {
        check(g_disableResult==static_cast<int>(Status::ok),"RsClone_Disable inside a factory call returns ok");
        check(outer&&outer->last_sync==Status::unavailable&&!outer->appearance&&!outer->render_ready,
              "the result that came back after the hook went off is left unsynchronized");
        std::unique_ptr<Actor> after(g_factory(&player,false));
        check(after&&!after->appearance&&after->last_sync==Status::unavailable,"later calls reach the factory directly");
    }
    std::printf("NESTED %s checks=%d failures=%d\n",mode,checks,failures);
    return failures?1:0;
}

int main(int argc,char** argv) {
    SetErrorMode(SEM_NOGPFAULTERRORBOX|SEM_FAILCRITICALERRORS);
    if (argc>2&&!std::strcmp(argv[1],"nested")) return NestedMode(argv[2]);
    Actor primary,clone;
    primary.appearance=appearance(); primary.skeleton=primary.appearance->skeleton; primary.generation=42;
    clone.skeleton=primary.skeleton; clone.entity_id=100; clone.animation_frame=8;
    check(RsClone_Synchronize(&primary,&clone)==Status::ok,"transactional appearance synchronization");
    check(clone.appearance->equipment[0].mesh==primary.appearance->equipment[0].mesh,"mesh owner retained without raw pointer copy");
    check(clone.appearance->equipment[0].material==primary.appearance->equipment[0].material,"material owner retained");
    check(clone.appearance->equipment[0].weights.data()!=primary.appearance->equipment[0].weights.data(),"weights have independent storage");
    check(clone.appearance->morphs.data()!=primary.appearance->morphs.data()&&clone.appearance->morphs==primary.appearance->morphs,"morph data copied independently");
    check(clone.entity_id==100&&clone.animation_frame==8&&clone.generation==42&&clone.render_ready,"pose and identity preserved");
    auto saved=clone.appearance;
    primary.appearance=appearance(77); primary.generation=77;
    check(clone.appearance->equipment[0].mesh->identity==42,"later player equipment cannot mutate the committed clone");
    auto bad=appearance(); bad->equipment[0].weights[0].value[0]=std::numeric_limits<float>::quiet_NaN(); primary.appearance=bad;
    check(RsClone_Synchronize(&primary,&clone)==Status::invalid&&clone.appearance==saved,"NaN rejects without partial mutation");
    bad=appearance(); bad->equipment[0].weights[0].joint[0]=4000; primary.appearance=bad;
    check(RsClone_Synchronize(&primary,&clone)==Status::invalid&&clone.appearance==saved,"out-of-range skin joint rejects atomically");
    bad=appearance(); bad->equipment[0].weights[0].value[0]=0; primary.appearance=bad;
    check(RsClone_Synchronize(&primary,&clone)==Status::invalid,"unnormalized weights reject");
    primary.appearance=appearance(); clone.skeleton[1].parent=-1;
    check(RsClone_Synchronize(&primary,&clone)==Status::skeleton_mismatch&&clone.appearance==saved,"different skeleton topology refuses sync");
    clone.skeleton=primary.skeleton;
    check(RsClone_Synchronize(&primary,&primary)==Status::invalid&&RsClone_Synchronize(nullptr,&clone)==Status::invalid,"invalid source/destination rejected");
    std::weak_ptr<const Resource> retained=saved->equipment[0].mesh;
    saved.reset();
    check(RsClone_Retire(&primary)==Status::ok&&!retained.expired(),"clone retains resources after player retires");
    check(RsClone_Synchronize(&primary,&clone)==Status::retired,"retired source rejected");
    check(RsClone_Retire(&clone)==Status::ok&&retained.expired(),"resources released only after final owning clone retires");
    check(RsClone_Retire(&clone)==Status::retired,"double retirement reported");
    check(RsClone_GameProfile("ddda-build-2364871")==Status::unavailable,"matching game build is not proof of an instantiation ABI");
    check(RsClone_EnableSyntheticHost(GetModuleHandleW(L"riftstone_clone_sync.dll"))==Status::invalid,"arbitrary module cannot select a hook");
    Actor player; player.appearance=appearance(); player.skeleton=player.appearance->skeleton; player.generation=42;
    auto factory=reinterpret_cast<Factory>(GetProcAddress(GetModuleHandleW(nullptr),"RsSyntheticCreateCloneV1"));
    check(factory!=nullptr,"explicit host export resolves");
    if (!factory) return 1;
    { std::unique_ptr<Actor> before(factory(&player,false)); check(before&&!before->appearance,"fixture exhibits missing appearance before interception"); }
    check(RsClone_EnableSyntheticHost(GetModuleHandleW(nullptr))==Status::ok,"actual MinHook factory interception");
    { std::unique_ptr<Actor> after(factory(&player,false));
      check(after&&after->last_sync==Status::ok&&after->appearance->equipment[0].mesh->identity==42,"intercepted entity factory receives player appearance");
      check(after&&after->entity_id==991&&after->animation_frame==12.5f,"hook preserves factory identity and animation"); }
    { std::unique_ptr<Actor> wrong(factory(&player,true)); check(wrong&&wrong->last_sync==Status::skeleton_mismatch&&!wrong->render_ready,"incompatible clone remains unready"); }
    check(factory(nullptr,false)==nullptr,"null factory result preserved");
    std::atomic<int> errors=0,calls=0;
    auto reader=[&] {
        for (int i=0;i<300;++i) {
            std::unique_ptr<Actor> value(factory(&player,false));
            if (!value) ++errors;
            else if (value->last_sync==Status::ok) {
                if (!value->appearance || value->generation!=value->appearance->equipment[0].mesh->identity ||
                    value->appearance->morphs[0]!=float(value->generation)) ++errors;
            } else if (value->last_sync!=Status::unavailable) ++errors;
            ++calls;
        }
    };
    std::thread one(reader),two(reader);
    for (uint64_t i=100;i<200;++i) {
        auto next=appearance(i);
        std::lock_guard lock(player.mutex); player.appearance=next; player.generation=i;
    }
    while (calls.load()<100) std::this_thread::yield();
    check(RsClone_Disable()==Status::ok,"disable during concurrent clone requests");
    one.join(); two.join();
    check(calls==600&&errors==0,"concurrent generations never mix resource, morph and ownership snapshots");
    { std::unique_ptr<Actor> after(factory(&player,false)); check(after&&!after->appearance,"disabled factory restored"); }
    check(RsClone_EnableSyntheticHost(GetModuleHandleW(nullptr))==Status::busy,"retained-trampoline lifecycle cannot reinstall");
    std::printf("CLONE_CHECKS=%d FAILURES=%d FACTORY_CALLS=%d GAME_PROFILE=UNAVAILABLE\n",checks,failures,calls.load());
    return failures?1:0;
}
