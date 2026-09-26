#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "fiber_scheduler.hpp"
#include "work_stealing.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cfenv>
#include <cstdlib>
#include <iostream>
#include <intrin.h>
#include <cstring>
#include <limits>
#include <memory>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
using namespace riftstone::perf;
using Clock = std::chrono::steady_clock;
static unsigned checks = 0, failures = 0;
static void check(bool ok, const char* label) { ++checks; if (!ok) { ++failures; std::cerr << "FAIL " << label << '\n'; } }
template<class F> static bool rejects(F&& fn) { try { fn(); } catch (const std::exception&) { return true; } return false; }

static std::uint64_t deque_case(std::size_t count, std::size_t thieves, std::size_t capacity) {
    struct Token { std::size_t id; };
    WorkDeque<Token*> q(capacity);
    auto tokens = std::make_unique<Token[]>(count);
    auto visits = std::make_unique<std::atomic<unsigned>[]>(count);
    for (std::size_t i = 0; i < count; ++i) tokens[i].id = i;
    std::atomic<bool> done{false};
    std::atomic<std::uint64_t> seen{0};
    auto consume = [&](Token* p) { if (p) { visits[p->id].fetch_add(1); seen.fetch_add(1); } };
    std::vector<std::thread> threads;
    for (std::size_t i = 0; i < thieves; ++i) threads.emplace_back([&] {
        while (!done.load(std::memory_order_acquire)) { consume(q.steal()); std::this_thread::yield(); }
        while (auto* p = q.steal()) consume(p);
    });
    for (std::size_t i = 0; i < count; ++i) {
        while (!q.push(&tokens[i])) consume(q.pop());
        if ((i % 7) == 0) consume(q.pop());
    }
    while (auto* p = q.pop()) consume(p);
    done.store(true, std::memory_order_release);
    for (auto& thread : threads) thread.join();
    bool once = seen.load() == count;
    for (std::size_t i = 0; i < count; ++i) once = once && visits[i].load() == 1;
    check(once, "deque exactly once under wrap and last-item races");
    check(q.pop() == nullptr && q.steal() == nullptr, "deque empty after race");
    return seen.load();
}

static void deque_tests() {
    int a=1, b=2, c=3, d=4;
    WorkDeque<int*> q(2);
    check(q.pop() == nullptr, "initial empty pop has no unsigned underflow");
    check(!q.push(nullptr), "null refused");
    check(q.push(&a) && q.push(&b) && !q.push(&c), "bounded capacity");
    check(q.steal() == &a && q.pop() == &b, "opposite deque ends");
    check(q.push(&c) && q.push(&d) && q.pop() == &d && q.pop() == &c, "owner LIFO after wrap");
    WorkDeque<int*> edge(2, (std::numeric_limits<std::int64_t>::max)()-1);
    check(edge.push(&a) && !edge.push(&b) && edge.steal() == &a && !edge.push(&b), "index overflow refused");
    check(edge.pop() == nullptr, "empty near index ceiling");
    check(rejects([] { WorkDeque<int*> invalid(3); }), "non power-of-two capacity refused");
    deque_case(100000, 4, 8);
}

static void graph_tests() {
    check(rejects([] { Scheduler invalid({0,8,2,65536}); }), "zero workers refused");
    TaskGraph invalid;
    const TaskId future=0;
    check(rejects([&] { invalid.add([](FiberContext&){}, std::span(&future,1)); }), "future dependency refused");
    invalid.add([](FiberContext&){});
    const TaskId duplicated[]={0,0};
    check(rejects([&] { invalid.add([](FiberContext&){}, duplicated); }), "duplicate dependency refused");
    check(invalid.size()==1, "invalid add leaves graph intact");
    check(rejects([&] { invalid.add({}); }), "empty callback refused");
    for (std::size_t nworkers : {1u,2u,4u}) {
        Scheduler scheduler({nworkers,256,4,131072});
        std::mt19937 random(72841);
        for (unsigned frame=0; frame<25; ++frame) {
            TaskGraph graph;
            std::vector<std::uint64_t> expected(100), output(100);
            for (std::size_t i=0; i<output.size(); ++i) {
                std::vector<TaskId> deps;
                if (i) { deps.push_back(random()%i); if (i>2) deps.push_back(random()%i); }
                std::sort(deps.begin(),deps.end()); deps.erase(std::unique(deps.begin(),deps.end()),deps.end());
                expected[i]=i+1; for (auto d:deps) expected[i]+=expected[d];
                graph.add([&,i,deps](FiberContext& ctx) {
                    const DWORD thread=GetCurrentThreadId();
                    for (int k=0;k<3;++k) ctx.yield();
                    if (thread!=GetCurrentThreadId() || ctx.worker_index()>=nworkers) throw std::runtime_error("fiber migrated");
                    output[i]=i+1; for (auto d:deps) output[i]+=output[d];
                },deps);
            }
            auto stats=scheduler.run(graph);
            check(output==expected && stats.completed==100 && stats.fiber_switches==400, "dependency visibility, yields, repeated epochs");
        }
        check(scheduler.run(TaskGraph{}).completed==0, "empty graph");
        TaskGraph nested;
        nested.add([&](FiberContext&) { scheduler.run(TaskGraph{}); });
        check(rejects([&] { scheduler.run(nested); }), "recursive run refused and propagated");
        check(scheduler.run(invalid).completed==1, "scheduler recovers after failed job");
    }
    Scheduler scheduler({1,16,2,131072});
    std::atomic<int> constructed{0}, destroyed{0};
    struct Lifetime { std::atomic<int>& d; ~Lifetime(){++d;} };
    TaskGraph exceptional;
    exceptional.add([](FiberContext&) { throw std::runtime_error("expected failure"); });
    exceptional.add([&](FiberContext& ctx) { ++constructed; Lifetime guard{destroyed}; for(int i=0;i<20;++i)ctx.yield(); });
    check(rejects([&] { scheduler.run(exceptional); }), "exception reaches control thread");
    check(constructed.load()==1 && destroyed.load()==1, "cancel unwinds suspended C++ stack");
    TaskGraph rounding;
    bool stable[2]={false,false};
    for (int i=0;i<2;++i) rounding.add([&,i](FiberContext& ctx) {
        const int old=std::fegetround(), selected=i?FE_DOWNWARD:FE_UPWARD;
        std::fesetround(selected); for(int k=0;k<5;++k)ctx.yield();
        stable[i]=std::fegetround()==selected; std::fesetround(old);
    });
    scheduler.run(rounding);
    check(stable[0] && stable[1], "fiber floating-point mode isolated");
    std::atomic<bool> entered{false}, release{false};
    TaskGraph hold;
    hold.add([&](FiberContext& ctx) { entered.store(true); while(!release.load())ctx.yield(); });
    std::thread controller([&] { scheduler.run(hold); });
    while(!entered.load())std::this_thread::yield();
    check(rejects([&] { scheduler.run(TaskGraph{}); }), "concurrent run refused");
    release.store(true); controller.join();
    check(scheduler.run(invalid).completed==1, "frame barrier reusable");
}

__declspec(noinline) static void kernel(std::vector<std::uint64_t>& data, std::size_t first, std::size_t last) {
    for (std::size_t i=first;i<last;++i) {
        auto value=data[i];
        for (unsigned j=0;j<64;++j) value=(value^(value>>17))*0x9E3779B185EBCA87ull+static_cast<std::uint64_t>(j);
        data[i]=value;
    }
}
static double median(std::vector<double> values) { std::sort(values.begin(),values.end());return values[values.size()/2]; }
static double p95(std::vector<double> values) { std::sort(values.begin(),values.end());return values[(values.size()-1)*95/100]; }
static void benchmark() {
    const auto start=Clock::now(); Scheduler scheduler({4,4096,8,131072});
    const double setup=std::chrono::duration<double,std::milli>(Clock::now()-start).count();
    for (std::size_t count:{30u,64u,3073u,65536u}) for(std::size_t chunk:{1u,64u}) {
        if ((count+chunk-1)/chunk>4096)continue;
        std::vector<std::uint64_t> serial(count,23), parallel(count,23);
        TaskGraph graph;
        for(std::size_t first=0;first<count;first+=chunk)graph.add([&,first](FiberContext&) { kernel(parallel,first,(std::min)(count,first+chunk)); });
        std::vector<double> baseline,times;
        SchedulerStats stats;
        for(unsigned repeat=0;repeat<31;++repeat) {
            auto t=Clock::now();kernel(serial,0,count);
            auto serial_ns=std::chrono::duration<double,std::nano>(Clock::now()-t).count();
            t=Clock::now();stats=scheduler.run(graph);
            auto parallel_ns=std::chrono::duration<double,std::nano>(Clock::now()-t).count();
            if(repeat>5) { baseline.push_back(serial_ns);times.push_back(parallel_ns); }
        }
        check(serial==parallel,"benchmark checksum equals serial");
        const auto checksum=std::accumulate(parallel.begin(),parallel.end(),std::uint64_t{0});
        std::cout<<"{\"kind\":\"benchmark\",\"entities\":"<<count<<",\"chunk\":"<<chunk
                 <<",\"serial_median_ns\":"<<median(baseline)<<",\"scheduler_median_ns\":"<<median(times)
                 <<",\"speedup\":"<<median(baseline)/median(times)<<",\"setup_ms\":"<<setup
                 <<",\"serial_p95_ns\":"<<p95(baseline)<<",\"scheduler_p95_ns\":"<<p95(times)
                 <<",\"workers\":4,\"workers_used_last_frame\":"<<stats.workers_used<<",\"checksum\":"<<checksum<<"}\n";
    }
}
int main(int argc,char** argv) {
    try {
        char brand[49]{}; int registers[4]{};
        __cpuid(registers,static_cast<int>(0x80000000u));
        if(static_cast<unsigned>(registers[0])>=0x80000004u)for(unsigned leaf=0;leaf<3;++leaf) {
            __cpuid(registers,static_cast<int>(0x80000002u+leaf));std::memcpy(brand+leaf*16,registers,16);
        }
        // CPUID brand bytes are sanitized before JSON output.
        for(char& c:brand)if(c=='"' || c=='\\' || (c && static_cast<unsigned char>(c)<32))c='?';
        std::cout<<"{\"kind\":\"environment\",\"cpu\":\""<<brand<<"\",\"logical_processors\":"
                 <<std::thread::hardware_concurrency()<<",\"pointer_bits\":"<<sizeof(void*)*8<<"}\n";
        double fuzz_seconds=0;
        if(argc==3 && std::string(argv[1])=="--fuzz-seconds") {
            std::size_t parsed=0;fuzz_seconds=std::stod(argv[2],&parsed);
            if(parsed!=std::string(argv[2]).size() || !std::isfinite(fuzz_seconds) || fuzz_seconds<0 || fuzz_seconds>600)
                throw std::invalid_argument("fuzz duration must be 0..600 seconds");
        } else if(argc!=1) throw std::invalid_argument("usage: scheduler_host [--fuzz-seconds N]");
        deque_tests(); graph_tests(); benchmark();
        std::uint64_t consumed=0, rounds=0;
        Scheduler stress({4,256,4,131072});
        std::mt19937 random(483811);
        const auto start=Clock::now();
        while(std::chrono::duration<double>(Clock::now()-start).count()<fuzz_seconds) {
            consumed+=deque_case(4096+(rounds%7)*127,1+(rounds%6),std::size_t{2}<<(rounds%8));++rounds;
            if(rounds%16==0) {
                TaskGraph graph; const std::size_t n=32+random()%96;
                std::vector<std::uint64_t> expected(n),output(n);
                for(std::size_t i=0;i<n;++i) {
                    std::vector<TaskId> dependencies;
                    if(i)dependencies.push_back(random()%i);
                    expected[i]=i+1;for(auto d:dependencies)expected[i]+=expected[d];
                    graph.add([&,i,dependencies](FiberContext& context) {
                        for(std::size_t j=0;j<i%4;++j)context.yield();
                        output[i]=i+1;for(auto d:dependencies)output[i]+=output[d];
                    },dependencies);
                }
                const auto measured=stress.run(graph);
                check(output==expected && measured.completed==n,"random DAG memory visibility and exactly-once execution");
            }
        }
        std::cout<<"{\"kind\":\"summary\",\"checks\":"<<checks<<",\"failures\":"<<failures
                 <<",\"fuzz_rounds\":"<<rounds<<",\"fuzz_tasks\":"<<consumed<<",\"fuzz_seconds\":"
                 <<std::chrono::duration<double>(Clock::now()-start).count()<<",\"gameplay\":\"UNKNOWN\"}\n";
        return failures?1:0;
    } catch(const std::exception& error) { std::cerr<<error.what()<<'\n';return 2; }
}
