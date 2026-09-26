#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "hot_data.hpp"
#include "frame_arena.hpp"
#include <algorithm>
#include <array>
#include <atomic>
#include <bit>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <immintrin.h>
#include <limits>
#include <memory>
#include <random>
#include <stdexcept>
#include <thread>

using namespace riftstone::perf;
namespace {
std::uint64_t checks=0;
void check(bool value,const char* label) { ++checks;if(!value)throw std::runtime_error(label); }
std::uint32_t bits(float value){return std::bit_cast<std::uint32_t>(value);}
bool finite(float value){return (bits(value)&0x7f800000u)!=0x7f800000u;}
bool equal(std::span<const float> a,std::span<const float> b){return a.size()==b.size() && (a.empty() || !std::memcmp(a.data(),b.data(),a.size_bytes()));}
void fill(HotData& data,std::mt19937& random,bool extreme=false) {
    for(std::size_t i=0;i<data.x.size();++i){
        auto number=[&](){const auto value=static_cast<std::uint32_t>(random());return extreme ? std::bit_cast<float>(value&0xfeffffffu) : static_cast<float>(static_cast<int>(value%20001)-10000)/32.0f;};
        data.x[i]=number();data.y[i]=number();data.z[i]=number();data.vx[i]=number();data.vy[i]=number();data.vz[i]=number();
        data.hp[i]=(random()%5)==0?0.0f:1.0f;data.radius[i]=static_cast<float>(random()%128);data.flags[i]=static_cast<std::uint32_t>(random()%8);
    }
}
void golden(HotView in,PositionView out,float dt) {
    for(std::size_t i=0;i<in.size();++i){
        if(in.hp[i]>0 && !(in.flags[i]&(frozen|staggered))){
            // Independent SSE scalar operations establish exact float32 rounding.
            out.x[i]=_mm_cvtss_f32(_mm_add_ss(_mm_set_ss(in.x[i]),_mm_mul_ss(_mm_set_ss(in.vx[i]),_mm_set_ss(dt))));
            out.y[i]=_mm_cvtss_f32(_mm_add_ss(_mm_set_ss(in.y[i]),_mm_mul_ss(_mm_set_ss(in.vy[i]),_mm_set_ss(dt))));
            out.z[i]=_mm_cvtss_f32(_mm_add_ss(_mm_set_ss(in.z[i]),_mm_mul_ss(_mm_set_ss(in.vz[i]),_mm_set_ss(dt))));
        }else{out.x[i]=in.x[i];out.y[i]=in.y[i];out.z[i]=in.z[i];}
    }
}
void compare(HotView input,float dt){
    HotData expected(input.size()),scalar(input.size()),vector(input.size());golden(input,expected.positions(),dt);
    check(integrate(input,scalar.positions(),dt,Backend::scalar)==DataStatus::ok,"scalar status");
    check(equal(expected.x,scalar.x)&&equal(expected.y,scalar.y)&&equal(expected.z,scalar.z),"scalar IEEE differential");
    check(integrate(input,vector.positions(),dt)==DataStatus::ok,"dispatch status");
    check(equal(expected.x,vector.x)&&equal(expected.y,vector.y)&&equal(expected.z,vector.z),"dispatch IEEE differential");
    if(avx2_available()){
        check(integrate(input,vector.positions(),dt,Backend::avx2)==DataStatus::ok,"AVX2 status");
        check(equal(expected.x,vector.x)&&equal(expected.y,vector.y)&&equal(expected.z,vector.z),"AVX2 IEEE differential");
    }
}
struct Guarded {
    void* allocation=nullptr;float* pointer=nullptr;
    explicit Guarded(std::size_t count){
        allocation=VirtualAlloc(nullptr,12288,MEM_RESERVE,PAGE_NOACCESS);
        if(!allocation || !VirtualAlloc(static_cast<char*>(allocation)+4096,4096,MEM_COMMIT,PAGE_READWRITE))throw std::bad_alloc();
        pointer=reinterpret_cast<float*>(static_cast<char*>(allocation)+8192)-count;
    }
    ~Guarded(){if(allocation)VirtualFree(allocation,0,MEM_RELEASE);}
};
void kernel_tests(){
    check(supports_avx2({true,true,true,true}),"complete AVX2 gate");
    check(!supports_avx2({false,true,true,true})&&!supports_avx2({true,false,true,true})&&!supports_avx2({true,true,false,true})&&!supports_avx2({true,true,true,false}),"every AVX2 prerequisite required");
    std::mt19937 random(0x831ffu);
    for(auto n:{0u,1u,7u,8u,9u,15u,16u,17u,30u,64u,3073u}){
        HotData input(n);fill(input,random);compare(input.view(),0.125f);compare(input.view(),-0.0f);
        HotData expected(n);golden(input.view(),expected.positions(),0.125f);
        check(integrate(input.view(),input.positions(),0.125f)==DataStatus::ok,"in-place update");
        check(equal(input.x,expected.x)&&equal(input.y,expected.y)&&equal(input.z,expected.z),"in-place exact");
    }
    HotData input(33),output(33);fill(input,random,true);
    const std::array<std::uint32_t,9> edges{0,0x80000000u,1,0x80000001u,0x007fffffu,0x00800000u,0x7f7fffffu,0xff7fffffu,0x3f800000u};
    for(std::size_t i=0;i<input.x.size();++i){input.x[i]=std::bit_cast<float>(edges[i%edges.size()]);input.vx[i]=std::bit_cast<float>(edges[(i+2)%edges.size()]);input.flags[i]=i%3==0?frozen:0;input.hp[i]=1;}
    compare(input.view(),std::numeric_limits<float>::max());compare(input.view(),std::numeric_limits<float>::denorm_min());
    auto view=input.view();view.x=view.x.subspan(1);view.y=view.y.subspan(1);view.z=view.z.subspan(1);view.vx=view.vx.subspan(1);view.vy=view.vy.subspan(1);view.vz=view.vz.subspan(1);view.hp=view.hp.subspan(1);view.radius=view.radius.subspan(1);view.flags=view.flags.subspan(1);
    compare(view,0.5f);check(reinterpret_cast<std::uintptr_t>(input.x.data())%64==0,"owned stream alignment");
    auto before=output.x;input.hp.back()=std::bit_cast<float>(0x7f800001u);
    check(integrate(input.view(),output.positions(),1)==DataStatus::nonfinite,"signaling NaN rejected before arithmetic");check(equal(before,output.x),"nonfinite transactional");input.hp.back()=1;
    input.vz.back()=std::numeric_limits<float>::infinity();check(integrate(input.view(),output.positions(),1)==DataStatus::nonfinite,"infinity rejected");input.vz.back()=1;
    input.radius.back()=-1;check(integrate(input.view(),output.positions(),1)==DataStatus::bad_radius,"negative radius");input.radius.back()=1;
    check(integrate(input.view(),output.positions(),std::numeric_limits<float>::quiet_NaN())==DataStatus::nonfinite,"nonfinite delta");
    auto bad=input.view();bad.vz=bad.vz.first(2);check(integrate(bad,output.positions(),1)==DataStatus::bad_shape,"mismatched view");
    check(integrate(input.view(),{output.x,output.x,output.z},1)==DataStatus::aliasing,"output alias");
    check(integrate(input.view(),{input.vx,output.y,output.z},1)==DataStatus::aliasing,"input output alias");
    check(integrate(input.view(),{input.y,input.x,input.z},1)==DataStatus::aliasing,"cross-axis alias");
    check(integrate(input.view(),output.positions(),1,static_cast<Backend>(99))==DataStatus::unsupported_backend,"unknown backend");
    const auto mode=_mm_getcsr();
    for(auto changed:{mode|0x8000u,mode|0x40u,(mode&~0x6000u)|0x2000u,mode&~0x80u}){
        _mm_setcsr(changed);auto status=integrate(input.view(),output.positions(),1);_mm_setcsr(mode);
        check(status==DataStatus::bad_fp_environment,"FP environment refused");
    }
    // End-of-page buffers turn every vector/tail overread and overwrite into a fault.
    for(auto n:{1u,7u,8u,9u,15u,17u}){
        std::array<std::unique_ptr<Guarded>,11> memory;
        for(auto& item:memory)item=std::make_unique<Guarded>(n);
        for(std::size_t j=0;j<8;++j)for(std::size_t i=0;i<n;++i)memory[j]->pointer[i]=1;
        std::vector<std::uint32_t> flags(n,0);
        HotView guarded{{memory[0]->pointer,n},{memory[1]->pointer,n},{memory[2]->pointer,n},{memory[3]->pointer,n},{memory[4]->pointer,n},{memory[5]->pointer,n},{memory[6]->pointer,n},{memory[7]->pointer,n},flags};
        PositionView target{{memory[8]->pointer,n},{memory[9]->pointer,n},{memory[10]->pointer,n}};
        check(integrate(guarded,target,2)==DataStatus::ok,"guard-page tails");check(target.x.back()==3,"guard-page result");
    }
}
struct alignas(64) Aligned { std::byte bytes[64]; };
void arena_tests(){
    FrameArena arena(1024);check(arena.capacity()==1024 && arena.used()==0,"arena initial");
    check(!arena.allocate<float>(0),"zero allocation refused");check(!arena.allocate<std::uint64_t>(SIZE_MAX),"count overflow refused");
    auto byte=arena.allocate<std::byte>(1);auto aligned=arena.allocate<Aligned>(1);check(byte.has_value()&&aligned.has_value(),"arena aligned allocation");
    auto lease=aligned->lease();check(lease.has_value(),"lease acquired");
    check(reinterpret_cast<std::uintptr_t>(lease->span().data())%64==0 && arena.used()==128,"alignment padding");
    lease->span()[0].bytes[0]=std::byte{93};check(arena.reset()==ArenaStatus::busy,"live lease blocks reset");
    auto moved=std::move(*lease);lease.reset();check(arena.reset()==ArenaStatus::busy,"move retains lease guard");
    std::thread worker([owned=std::move(moved)]()mutable{check(owned.span()[0].bytes[0]==std::byte{93},"cross-thread lease data");});worker.join();
    check(arena.reset()==ArenaStatus::ok && arena.used()==0,"released lease permits reset");check(!aligned->lease(),"stale generation refused");
    auto full=arena.allocate<std::byte>(1024);check(full.has_value(),"exact capacity");check(!arena.allocate<std::byte>(1)&&arena.used()==1024,"exhaustion unchanged");
    std::atomic<bool> refused=false;std::thread foreign([&]{refused=!arena.allocate<int>(1)&&arena.reset()==ArenaStatus::wrong_thread;});foreign.join();check(refused,"owner allocation/reset boundary");
    std::optional<ArenaLease<int>> survivor;ArenaHandle<int> handle;
    {FrameArena ephemeral(64);handle=*ephemeral.allocate<int>(1);survivor=handle.lease();survivor->span()[0]=42;}
    check(survivor->span()[0]==42,"lease keeps storage after arena destruction");check(!handle.lease(),"closed arena refuses new lease");survivor.reset();check(!handle.lease(),"expired backing refused");
    bool zero=false;try{FrameArena invalid(0);}catch(const std::bad_array_new_length&){zero=true;}check(zero,"zero capacity refused");
}
std::uint64_t fuzz(double seconds){
    std::mt19937 random(0x1933u);std::uint64_t iterations=0;
    const auto until=std::chrono::steady_clock::now()+std::chrono::duration<double>(seconds);
    while(std::chrono::steady_clock::now()<until){
        const auto n=static_cast<std::size_t>(random()%145);HotData input(n);fill(input,random,true);
        const auto dt=std::bit_cast<float>(static_cast<std::uint32_t>(random())&0xfeffffffu);compare(input.view(),dt);
        FrameArena arena(512);std::vector<ArenaHandle<std::uint32_t>> handles;
        std::size_t expected=0;
        for(unsigned step=0;step<16;++step){
            const std::size_t count=1+random()%60,bytes=count*sizeof(std::uint32_t);auto allocation=arena.allocate<std::uint32_t>(count);
            check(allocation.has_value()==(bytes<=512-expected),"fuzz arena capacity");
            if(allocation){expected+=bytes;handles.push_back(*allocation);auto lease=allocation->lease();check(lease.has_value(),"fuzz lease");lease->span().front()=step;lease->span().back()=step;check(arena.reset()==ArenaStatus::busy,"fuzz live reset");}
            check(arena.used()==expected,"fuzz used accounting");
        }
        check(arena.reset()==ArenaStatus::ok,"fuzz reset");for(const auto& item:handles)check(!item.lease(),"fuzz stale handle");
        ++iterations;
    }
    return iterations;
}
// Benchmark baseline: hot fields interleaved with synthetic cold payload.
struct alignas(64) AosRow {float x,y,z,vx,vy,vz,hp,radius;std::uint32_t flags;std::uint64_t asset;char name[48];std::byte cold[160];};
static_assert(sizeof(AosRow)==256);
__declspec(noinline) DataStatus aos_checked(std::span<const AosRow> data,PositionView out,float dt){
    if(!supported_fp_environment() || !finite(dt))return DataStatus::bad_fp_environment;
    for(const auto& row:data)if(!finite(row.x)||!finite(row.y)||!finite(row.z)||!finite(row.vx)||!finite(row.vy)||!finite(row.vz)||!finite(row.hp)||!finite(row.radius)||row.radius<0)return DataStatus::nonfinite;
    for(std::size_t i=0;i<data.size();++i){const auto& row=data[i];if(row.hp>0 && !(row.flags&3)){const float dx=row.vx*dt,dy=row.vy*dt,dz=row.vz*dt;out.x[i]=row.x+dx;out.y[i]=row.y+dy;out.z[i]=row.z+dz;}else{out.x[i]=row.x;out.y[i]=row.y;out.z[i]=row.z;}}
    return DataStatus::ok;
}
template<class F> double measure(F function,std::size_t rounds){
    function();std::array<double,7> times{};
    for(auto& elapsed:times){const auto start=std::chrono::steady_clock::now();for(std::size_t i=0;i<rounds;++i)function();elapsed=std::chrono::duration<double,std::nano>(std::chrono::steady_clock::now()-start).count()/static_cast<double>(rounds);}
    std::sort(times.begin(),times.end());return times[3];
}
void benchmarks(){
    std::printf("\"benchmarks\":[");bool first=true;std::mt19937 random(7103);
    for(auto n:{30u,64u,3073u,65536u,262144u}){
        HotData input(n),output(n);fill(input,random);std::vector<AosRow> aos(n);
        for(std::size_t i=0;i<n;++i){aos[i]={input.x[i],input.y[i],input.z[i],input.vx[i],input.vy[i],input.vz[i],input.hp[i],input.radius[i],input.flags[i],i,{},{} };}
        const auto rounds=std::max<std::size_t>(8,1000000/n);
        const auto baseline=measure([&]{if(aos_checked(aos,output.positions(),0.125f)!=DataStatus::ok)throw std::runtime_error("benchmark AoS");},rounds);
        const auto scalar=measure([&]{if(integrate(input.view(),output.positions(),0.125f,Backend::scalar)!=DataStatus::ok)throw std::runtime_error("benchmark scalar");},rounds);
        const auto dispatch=measure([&]{if(integrate(input.view(),output.positions(),0.125f)!=DataStatus::ok)throw std::runtime_error("benchmark dispatch");},rounds);
        const auto kernel=measure([&]{if(avx2_available())detail::integrate_avx2(input.view(),output.positions(),0.125f);else detail::integrate_scalar(input.view(),output.positions(),0.125f);},rounds);
        std::printf("%s{\"entities\":%u,\"iterations_per_sample\":%zu,\"samples\":7,\"aos_checked_ns\":%.3f,\"soa_scalar_checked_ns\":%.3f,\"soa_dispatch_checked_ns\":%.3f,\"soa_dispatch_kernel_only_ns\":%.3f,\"dispatch_over_aos_ratio\":%.6f,\"soa_regression\":%s,\"output_checksum\":%u}",first?"":",",n,rounds,baseline,scalar,dispatch,kernel,dispatch/baseline,dispatch>baseline?"true":"false",bits(output.x.back()));first=false;
    }
    std::printf("]");
}
struct Transient {float x,y,z;std::uint32_t id;};
volatile std::uint64_t arena_sink=0;
void arena_benchmarks(){
    std::printf(",\"arena_benchmarks\":[");bool first=true;
    for(auto n:{30u,64u,3073u}){
        FrameArena arena(static_cast<std::size_t>(n)*sizeof(Transient));std::vector<Transient*> pointers(n);
        const auto rounds=std::max<std::size_t>(8,100000/n);
        const auto heap=measure([&]{
            for(std::uint32_t i=0;i<n;++i)pointers[i]=new Transient{1,2,3,i};
            std::uint64_t sum=0;for(auto* p:pointers){sum+=p->id;delete p;}arena_sink=sum;
        },rounds);
        const auto individual=measure([&]{
            std::uint64_t sum=0;
            for(std::uint32_t i=0;i<n;++i){auto handle=arena.allocate<Transient>(1);if(!handle)throw std::runtime_error("arena benchmark allocation");auto lease=handle->lease();lease->span()[0]={1,2,3,i};sum+=lease->span()[0].id;}
            if(arena.reset()!=ArenaStatus::ok)throw std::runtime_error("arena benchmark reset");arena_sink=sum;
        },rounds);
        const auto bulk=measure([&]{
            std::uint64_t sum=0;auto handle=arena.allocate<Transient>(n);if(!handle)throw std::runtime_error("arena bulk allocation");
            {auto lease=handle->lease();for(std::uint32_t i=0;i<n;++i){lease->span()[i]={1,2,3,i};sum+=lease->span()[i].id;}}
            if(arena.reset()!=ArenaStatus::ok)throw std::runtime_error("arena bulk reset");arena_sink=sum;
        },rounds);
        std::printf("%s{\"objects\":%u,\"bytes_each\":%zu,\"iterations_per_sample\":%zu,\"samples\":7,\"heap_individual_ns\":%.3f,\"arena_individual_guarded_ns\":%.3f,\"arena_bulk_guarded_ns\":%.3f,\"individual_regression\":%s,\"bulk_regression\":%s,\"checksum\":%llu}",first?"":",",n,sizeof(Transient),rounds,heap,individual,bulk,individual>heap?"true":"false",bulk>heap?"true":"false",arena_sink);first=false;
    }
    std::printf("]");
}
}
int main(int argc,char** argv){
    try{
        double seconds=0;bool bench=true;
        for(int i=1;i<argc;++i){if(!std::strcmp(argv[i],"--fuzz-seconds")&&i+1<argc)seconds=std::strtod(argv[++i],nullptr);else if(!std::strcmp(argv[i],"--no-bench"))bench=false;else throw std::runtime_error("unknown argument");}
        if(!std::isfinite(seconds)||seconds<0||seconds>600)throw std::runtime_error("fuzz seconds outside0..600");
        check(supported_fp_environment(),"host FP default");kernel_tests();arena_tests();const auto start=std::chrono::steady_clock::now();const auto iterations=fuzz(seconds);const auto elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
        const auto cpu=cpu_features();
        std::printf("{\"schema\":\"riftstone.performance-data/1\",\"outcome\":\"PASS\",\"pointer_bits\":%zu,\"checks\":%llu,\"avx2_available\":%s,\"cpu\":{\"avx\":%s,\"osxsave\":%s,\"ymm_state\":%s,\"avx2\":%s},\"fuzz_seconds\":%.3f,\"fuzz_iterations\":%llu,\"aos_stride\":%zu,\"hot_bytes_per_entity\":36,\"game_started\":false,",sizeof(void*)*8,checks,avx2_available()?"true":"false",cpu.avx?"true":"false",cpu.osxsave?"true":"false",cpu.ymm_state?"true":"false",cpu.avx2?"true":"false",elapsed,iterations,sizeof(AosRow));
        if(bench){benchmarks();arena_benchmarks();}else std::printf("\"benchmarks\":[],\"arena_benchmarks\":[]");std::printf("}\n");return 0;
    }catch(const std::exception& error){std::fprintf(stderr,"FAIL: %s\n",error.what());return 1;}
}
