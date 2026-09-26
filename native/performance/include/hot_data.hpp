#pragma once
#include <cstddef>
#include <cstdint>
#include <limits>
#include <malloc.h>
#include <new>
#include <span>
#include <string>
#include <type_traits>
#include <vector>

namespace riftstone::perf {
template<class T> struct AlignedAllocator {
    using value_type = T;
    using is_always_equal = std::true_type;
    AlignedAllocator() noexcept = default;
    template<class U> AlignedAllocator(const AlignedAllocator<U>&) noexcept {}
    [[nodiscard]] T* allocate(std::size_t n) {
        if (n > (std::numeric_limits<std::size_t>::max)() / sizeof(T)) throw std::bad_array_new_length();
        if (auto* p = _aligned_malloc(n * sizeof(T), 64)) return static_cast<T*>(p);
        throw std::bad_alloc();
    }
    void deallocate(T* p, std::size_t) noexcept { _aligned_free(p); }
    template<class U> bool operator==(const AlignedAllocator<U>&) const noexcept { return true; }
};
template<class T> using AlignedVector = std::vector<T, AlignedAllocator<T>>;
enum class Backend { automatic, scalar, avx2 };
enum class DataStatus { ok, bad_shape, aliasing, nonfinite, bad_radius, bad_fp_environment, unsupported_backend };
inline constexpr std::uint32_t frozen = 1, staggered = 2;
struct HotView {
    std::span<const float> x, y, z, vx, vy, vz, hp, radius;
    std::span<const std::uint32_t> flags;
    std::size_t size() const noexcept { return x.size(); }
};
struct PositionView { std::span<float> x, y, z; };
struct HotData {
    AlignedVector<float> x, y, z, vx, vy, vz, hp, radius;
    AlignedVector<std::uint32_t> flags;
    explicit HotData(std::size_t n = 0) : x(n), y(n), z(n), vx(n), vy(n), vz(n), hp(n), radius(n), flags(n) {}
    HotView view() const noexcept { return {x,y,z,vx,vy,vz,hp,radius,flags}; }
    PositionView positions() noexcept { return {x,y,z}; }
};
// Kept outside every hot stream. IDs associate application-owned records by index.
struct ColdRecord { std::uint64_t asset_id = 0; std::string name, dialogue; std::vector<std::uint32_t> inventory; };
struct ColdData { std::vector<ColdRecord> records; };
struct CpuFeatures { bool avx, osxsave, ymm_state, avx2; };
constexpr bool supports_avx2(CpuFeatures f) noexcept { return f.avx && f.osxsave && f.ymm_state && f.avx2; }
CpuFeatures cpu_features() noexcept;
bool avx2_available() noexcept;
Backend selected_backend(Backend requested = Backend::automatic) noexcept;
bool supported_fp_environment() noexcept;
// Finite input required; IEEE overflow/underflow results allowed. All errors precede
// writes. In-place matching xyz spans are supported; other overlaps are refused.
// Inactive positions retain their exact bits. No FP trap or sticky-flag equivalence
// is promised. Caller owns span lifetime and synchronization for the entire call.
DataStatus integrate(HotView input, PositionView output, float dt, Backend backend = Backend::automatic) noexcept;
namespace detail {
void integrate_scalar(HotView, PositionView, float) noexcept;
void integrate_avx2(HotView, PositionView, float) noexcept;
}
}
