#include "hot_data.hpp"
#include <array>
#include <bit>
#include <intrin.h>
#include <immintrin.h>

namespace riftstone::perf {
CpuFeatures cpu_features() noexcept {
    int info[4]{}; __cpuid(info, 0); const int maximum = info[0];
    if (maximum < 1) return {};
    __cpuidex(info, 1, 0);
    CpuFeatures result{(info[2] & (1 << 28)) != 0, (info[2] & (1 << 27)) != 0, false, false};
    if (result.avx && result.osxsave) result.ymm_state = (_xgetbv(0) & 6) == 6;
    if (maximum >= 7) { __cpuidex(info, 7, 0); result.avx2 = (info[1] & (1 << 5)) != 0; }
    return result;
}
bool avx2_available() noexcept {
    static const bool supported = supports_avx2(cpu_features());
    return supported;
}
Backend selected_backend(Backend requested) noexcept {
    return requested == Backend::automatic ? (avx2_available() ? Backend::avx2 : Backend::scalar) : requested;
}
bool supported_fp_environment() noexcept {
    // Round-to-nearest, all exception masks set, DAZ/FTZ disabled. Sticky flags
    // are deliberately ignored. Do not silently change the caller's environment.
    return (_mm_getcsr() & 0xffc0u) == 0x1f80u;
}
namespace {
struct Range { std::uintptr_t begin, end; };
template<class T> bool range(std::span<T> s, Range& result) noexcept {
    const auto start=reinterpret_cast<std::uintptr_t>(s.data());
    if (s.size() && !s.data()) return false;
    if (s.size() > (std::numeric_limits<std::size_t>::max)()/sizeof(T)) return false;
    const auto bytes=s.size()*sizeof(T);
    if (start > (std::numeric_limits<std::uintptr_t>::max)()-bytes) return false;
    result={start,start+bytes}; return true;
}
bool overlap(Range a, Range b) noexcept { return a.begin<a.end && b.begin<b.end && a.begin<b.end && b.begin<a.end; }
bool finite(float value) noexcept { return (std::bit_cast<std::uint32_t>(value)&0x7f800000u)!=0x7f800000u; }
}
DataStatus integrate(HotView in, PositionView out, float dt, Backend requested) noexcept {
    const auto n=in.size();
    std::array<std::span<const float>,8> inputs{in.x,in.y,in.z,in.vx,in.vy,in.vz,in.hp,in.radius};
    std::array<std::span<float>,3> outputs{out.x,out.y,out.z};
    std::array<Range,9> reads{}; std::array<Range,3> writes{};
    for (std::size_t i=0;i<inputs.size();++i) if (inputs[i].size()!=n || !range(inputs[i],reads[i])) return DataStatus::bad_shape;
    if (in.flags.size()!=n || !range(in.flags,reads[8])) return DataStatus::bad_shape;
    for (std::size_t i=0;i<outputs.size();++i) if (outputs[i].size()!=n || !range(outputs[i],writes[i])) return DataStatus::bad_shape;
    for (std::size_t i=0;i<writes.size();++i) {
        for (std::size_t j=i+1;j<writes.size();++j) if (overlap(writes[i],writes[j])) return DataStatus::aliasing;
        for (std::size_t j=0;j<reads.size();++j) {
            if (i==j && writes[i].begin==reads[j].begin && writes[i].end==reads[j].end) continue;
            if (overlap(writes[i],reads[j])) return DataStatus::aliasing;
        }
    }
    if (!supported_fp_environment()) return DataStatus::bad_fp_environment;
    if (!finite(dt)) return DataStatus::nonfinite;
    const Backend backend=selected_backend(requested);
    if (backend!=Backend::scalar && backend!=Backend::avx2) return DataStatus::unsupported_backend;
    if (backend==Backend::avx2 && !avx2_available()) return DataStatus::unsupported_backend;
    for (std::size_t i=0;i<n;++i) {
        for (const auto input:inputs) if (!finite(input[i])) return DataStatus::nonfinite;
        if (in.radius[i]<0) return DataStatus::bad_radius;
    }
    if (backend==Backend::avx2) detail::integrate_avx2(in,out,dt); else detail::integrate_scalar(in,out,dt);
    return DataStatus::ok;
}
namespace detail {
void integrate_scalar(HotView in, PositionView out, float dt) noexcept {
    #pragma loop(no_vector)
    for (std::size_t i=0;i<in.size();++i) {
        const bool active=in.hp[i]>0 && !(in.flags[i]&(frozen|staggered));
        if (active) {
            const float dx=in.vx[i]*dt, dy=in.vy[i]*dt, dz=in.vz[i]*dt;
            out.x[i]=in.x[i]+dx; out.y[i]=in.y[i]+dy; out.z[i]=in.z[i]+dz;
        } else { out.x[i]=in.x[i]; out.y[i]=in.y[i]; out.z[i]=in.z[i]; }
    }
}
}
}
