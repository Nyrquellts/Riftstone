#pragma once
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <memory>
#include <stdexcept>
#include <type_traits>

namespace riftstone::perf {
#if defined(_MSC_VER)
#pragma warning(push)
#pragma warning(disable: 4324) // Intentional cache-line separation of owner/thief counters.
#endif
// Bounded Chase-Lev deque. Exactly one concurrent push/pop owner; other
// threads may steal. Storage is never resized/reclaimed while thieves run.
// Atomic slots also make a losing thief's late read race-free after wrap.
template<class T> class WorkDeque {
    static_assert(std::is_pointer_v<T>);
    alignas(64) std::atomic<std::int64_t> top_{0};
    alignas(64) std::atomic<std::int64_t> bottom_{0};
    const std::size_t capacity_;
    const std::size_t mask_;
    std::unique_ptr<std::atomic<T>[]> slots_;
public:
    explicit WorkDeque(std::size_t capacity, std::int64_t start = 0)
        : capacity_(capacity), mask_(capacity - 1) {
        if (capacity < 2 || capacity > (1u << 20) || (capacity & (capacity - 1)) || start < 0)
            throw std::invalid_argument("deque requires a power-of-two capacity in [2,1048576]");
        slots_ = std::make_unique<std::atomic<T>[]>(capacity);
        if (!top_.is_lock_free() || !bottom_.is_lock_free() || !slots_[0].is_lock_free())
            throw std::runtime_error("lock-free deque atomics unavailable on this target");
        top_.store(start); bottom_.store(start);
    }
    bool push(T value) noexcept {
        if (!value) return false;
        const auto b = bottom_.load(std::memory_order_relaxed);
        const auto t = top_.load(std::memory_order_acquire);
        if (b == (std::numeric_limits<std::int64_t>::max)() ||
            b - t >= static_cast<std::int64_t>(capacity_)) return false;
        slots_[static_cast<std::size_t>(b) & mask_].store(value, std::memory_order_relaxed);
        bottom_.store(b + 1, std::memory_order_release);
        return true;
    }
    T pop() noexcept {
        const auto b = bottom_.load(std::memory_order_relaxed) - 1;
        bottom_.store(b, std::memory_order_relaxed);
        std::atomic_thread_fence(std::memory_order_seq_cst);
        auto t = top_.load(std::memory_order_relaxed);
        T result = nullptr;
        if (t <= b) {
            result = slots_[static_cast<std::size_t>(b) & mask_].load(std::memory_order_relaxed);
            if (t == b) {
                if (!top_.compare_exchange_strong(t, t + 1, std::memory_order_seq_cst,
                                                  std::memory_order_relaxed)) result = nullptr;
                bottom_.store(b + 1, std::memory_order_relaxed);
            }
        } else bottom_.store(b + 1, std::memory_order_relaxed);
        return result;
    }
    T steal() noexcept {
        auto t = top_.load(std::memory_order_acquire);
        std::atomic_thread_fence(std::memory_order_seq_cst);
        const auto b = bottom_.load(std::memory_order_acquire);
        if (t >= b) return nullptr;
        const T result = slots_[static_cast<std::size_t>(t) & mask_].load(std::memory_order_relaxed);
        return top_.compare_exchange_strong(t, t + 1, std::memory_order_seq_cst,
                                            std::memory_order_relaxed) ? result : nullptr;
    }
    // Legal only with no owner/thief operation in flight (between joined epochs).
    void reset_quiescent() noexcept { top_.store(0); bottom_.store(0); }
};
#if defined(_MSC_VER)
#pragma warning(pop)
#endif
}
