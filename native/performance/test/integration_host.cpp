#include "fiber_scheduler.hpp"
#include "frame_arena.hpp"
#include "hot_data.hpp"
#include <algorithm>
#include <atomic>
#include <bit>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <vector>

using namespace riftstone::perf;
namespace {
std::uint64_t checks=0;
void check(bool condition,const char* label){++checks;if(!condition)throw std::runtime_error(label);}
bool equal(std::span<const float> a,std::span<const float> b){return a.size()==b.size() && (a.empty() || !std::memcmp(a.data(),b.data(),a.size_bytes()));}
HotData snapshot(std::size_t n){
    HotData data(n);
    for(std::size_t i=0;i<n;++i){
        data.x[i]=i%7==0?-0.0f:static_cast<float>(i)/8;
        data.y[i]=-static_cast<float>(i)/16;data.z[i]=std::bit_cast<float>(static_cast<std::uint32_t>(i+1));
        data.vx[i]=static_cast<float>(i%11)/4;data.vy[i]=-static_cast<float>(i%13)/2;data.vz[i]=static_cast<float>(i%3);
        data.hp[i]=i%5==0?0.0f:100.0f;data.radius[i]=1.0f;data.flags[i]=i%9==0?frozen:(i%7==0?staggered:0);
    }
    return data;
}
std::uint64_t fingerprint(HotView view){
    std::uint64_t hash=1469598103934665603ull;
    for(auto stream:{view.x,view.y,view.z,view.vx,view.vy,view.vz,view.hp,view.radius})for(float value:stream){hash^=std::bit_cast<std::uint32_t>(value);hash*=1099511628211ull;}
    for(auto value:view.flags){hash^=value;hash*=1099511628211ull;}
    return hash;
}
HotView slice(HotView view,std::size_t first,std::size_t count){
    return {view.x.subspan(first,count),view.y.subspan(first,count),view.z.subspan(first,count),view.vx.subspan(first,count),view.vy.subspan(first,count),view.vz.subspan(first,count),view.hp.subspan(first,count),view.radius.subspan(first,count),view.flags.subspan(first,count)};
}
std::uint64_t positions_hash(PositionView view){
    std::uint64_t hash=1469598103934665603ull;
    for(auto stream:{view.x,view.y,view.z})for(float value:stream){hash^=std::bit_cast<std::uint32_t>(value);hash*=1099511628211ull;}
    return hash;
}
void run_case(std::size_t n,Backend backend,std::size_t worker_count){
    // This const snapshot has no engine pointers and remains alive for the graph.
    const HotData input=snapshot(n);
    const auto original_hash=fingerprint(input.view());
    HotData expected(n);
    FrameArena arena((3*n+2)*sizeof(float));
    Scheduler scheduler({worker_count,512,4,131072});
    TaskGraph graph;
    PositionView output{}; // Rebound only by the control thread between barriers.
    float dt=0;
    std::vector<std::atomic<unsigned>> visits(n);
    std::vector<TaskId> jobs;
    constexpr std::size_t chunk=13; // Full vector groups plus tails, even at30/64.
    for(std::size_t first=0;first<n;first+=chunk){
        const auto count=(std::min)(chunk,n-first);
        const HotView source=slice(input.view(),first,count);
        jobs.push_back(graph.add([&,first,count,source,backend](FiberContext& context){
            context.yield();
            const PositionView destination{output.x.subspan(first,count),output.y.subspan(first,count),output.z.subspan(first,count)};
            if(integrate(source,destination,dt,backend)!=DataStatus::ok)throw std::runtime_error("scheduled integration refused");
            for(std::size_t i=first;i<first+count;++i)visits[i].fetch_add(1,std::memory_order_relaxed);
            context.yield();
        }));
    }
    bool dependency_verified=false;
    std::uint64_t dependency_checksum=0;
    graph.add([&](FiberContext&){
        for(const auto& count:visits)if(count.load(std::memory_order_relaxed)!=1)throw std::runtime_error("chunk missed or wrote twice");
        if(!equal(output.x,expected.x)||!equal(output.y,expected.y)||!equal(output.z,expected.z))throw std::runtime_error("dependency barrier observed wrong output");
        dependency_checksum=positions_hash(output);dependency_verified=true;
    },jobs);
    constexpr unsigned frames=20;
    std::uint64_t total_tasks=0,last_checksum=0;
    for(unsigned frame=0;frame<frames;++frame){
        for(auto& count:visits)count.store(0,std::memory_order_relaxed);
        dependency_verified=false;dt=static_cast<float>(frame+1)/64.0f;
        check(integrate(input.view(),expected.positions(),dt,Backend::scalar)==DataStatus::ok,"serial reference");
        const auto handle=arena.allocate<float>(3*n+2);check(handle.has_value(),"bulk output allocation");
        auto lease=handle->lease();check(lease.has_value(),"bulk lease");
        const auto storage=lease->span();
        constexpr std::uint32_t marker=0x7fc12345u;
        std::fill(storage.begin(),storage.end(),std::bit_cast<float>(marker));
        output={storage.subspan(1,n),storage.subspan(1+n,n),storage.subspan(1+2*n,n)};
        check(arena.reset()==ArenaStatus::busy,"reset blocked before dispatch");
        // Synchronous return is the only point after which job borrows can end.
        const auto stats=scheduler.run(graph);total_tasks+=stats.completed;
        check(stats.completed==graph.size(),"every graph task completed");
        check(dependency_verified,"dependent validation task completed");
        check(equal(output.x,expected.x)&&equal(output.y,expected.y)&&equal(output.z,expected.z),"serial scheduled bitwise equivalence");
        check(std::bit_cast<std::uint32_t>(storage.front())==marker&&std::bit_cast<std::uint32_t>(storage.back())==marker,"arena output sentinels");
        check(dependency_checksum==positions_hash(output),"barrier publishes checksum");last_checksum=dependency_checksum;
        check(fingerprint(input.view())==original_hash,"immutable input snapshot");
        check(arena.reset()==ArenaStatus::busy,"barrier alone does not release lease");
        output={}; // The reusable graph cannot retain a dangling output span.
        lease.reset();
        check(arena.reset()==ArenaStatus::ok && arena.used()==0,"lease released after barrier then reset");
        check(!handle->lease(),"prior frame handle is stale");
    }
    std::printf("{\"entities\":%zu,\"workers\":%zu,\"backend\":\"%s\",\"frames\":%u,\"chunk_size\":%zu,\"tasks_completed\":%llu,\"input_fingerprint\":%llu,\"last_output_fingerprint\":%llu}",n,worker_count,backend==Backend::scalar?"scalar":"automatic",frames,chunk,total_tasks,original_hash,last_checksum);
}
void exception_lifetime(){
    FrameArena arena(128);const auto handle=arena.allocate<float>(32);check(handle.has_value(),"exception bulk allocation");
    Scheduler scheduler({1,8,2,131072});TaskGraph graph;
    std::atomic<bool> entered=false;
    // Owner-LIFO starts the leasing job first; its suspended frame must unwind.
    graph.add([](FiberContext&){throw std::runtime_error("intentional task failure");});
    graph.add([&](FiberContext& context){auto lease=handle->lease();if(!lease)throw std::runtime_error("job lease failed");lease->span()[0]=42;entered=true;for(unsigned i=0;i<10;++i)context.yield();});
    bool failed=false;try{scheduler.run(graph);}catch(const std::runtime_error&){failed=true;}
    check(failed&&entered.load(),"exception occurred after lease acquired");
    check(arena.reset()==ArenaStatus::ok,"exception barrier unwound job lease");check(!handle->lease(),"exception output handle invalidated");
    TaskGraph recovery;recovery.add([](FiberContext& context){context.yield();});check(scheduler.run(recovery).completed==1,"pool reusable after exception");
}
}
int main(){
    try{
        check(supported_fp_environment(),"supported FP environment");exception_lifetime();
        std::printf("{\"schema\":\"riftstone.performance-integration/1\",\"pointer_bits\":%zu,\"avx2_available\":%s,\"cases\":[",sizeof(void*)*8,avx2_available()?"true":"false");
        bool first=true;
        for(auto workers:{1u,4u})for(auto n:{30u,64u,3073u})for(auto backend:{Backend::scalar,Backend::automatic}){
            if(!first)std::printf(",");first=false;run_case(n,backend,workers);
        }
        std::printf("],\"outcome\":\"PASS\",\"checks\":%llu,\"frames\":240,\"game_started\":false,\"engine_pointers\":false}\n",checks);return 0;
    }catch(const std::exception& error){std::fprintf(stderr,"FAIL: %s\n",error.what());return 1;}
}
