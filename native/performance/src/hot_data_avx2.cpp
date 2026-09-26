#include "hot_data.hpp"
#include <immintrin.h>
namespace riftstone::perf::detail {
void integrate_avx2(HotView in, PositionView out, float dt) noexcept {
    const auto delta=_mm256_set1_ps(dt), zero=_mm256_setzero_ps();
    const auto disabled=_mm256_set1_epi32(static_cast<int>(frozen|staggered));
    std::size_t i=0;
    for (;in.size()-i>=8;i+=8) {
        const auto flags=_mm256_loadu_si256(reinterpret_cast<const __m256i*>(in.flags.data()+i));
        const auto enabled=_mm256_cmpeq_epi32(_mm256_and_si256(flags,disabled),_mm256_setzero_si256());
        const auto active=_mm256_and_ps(_mm256_cmp_ps(_mm256_loadu_ps(in.hp.data()+i),zero,_CMP_GT_OQ),_mm256_castsi256_ps(enabled));
        const auto x=_mm256_loadu_ps(in.x.data()+i), y=_mm256_loadu_ps(in.y.data()+i), z=_mm256_loadu_ps(in.z.data()+i);
        const auto dx=_mm256_mul_ps(_mm256_loadu_ps(in.vx.data()+i),delta);
        const auto dy=_mm256_mul_ps(_mm256_loadu_ps(in.vy.data()+i),delta);
        const auto dz=_mm256_mul_ps(_mm256_loadu_ps(in.vz.data()+i),delta);
        _mm256_storeu_ps(out.x.data()+i,_mm256_blendv_ps(x,_mm256_add_ps(x,dx),active));
        _mm256_storeu_ps(out.y.data()+i,_mm256_blendv_ps(y,_mm256_add_ps(y,dy),active));
        _mm256_storeu_ps(out.z.data()+i,_mm256_blendv_ps(z,_mm256_add_ps(z,dz),active));
    }
    _mm256_zeroupper();
    if (i!=in.size()) {
        HotView tail{in.x.subspan(i),in.y.subspan(i),in.z.subspan(i),in.vx.subspan(i),in.vy.subspan(i),in.vz.subspan(i),in.hp.subspan(i),in.radius.subspan(i),in.flags.subspan(i)};
        integrate_scalar(tail,{out.x.subspan(i),out.y.subspan(i),out.z.subspan(i)},dt);
    }
}
}
