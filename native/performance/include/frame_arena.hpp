#pragma once
#include <cstddef>
#include <cstdint>
#include <limits>
#include <malloc.h>
#include <memory>
#include <mutex>
#include <new>
#include <optional>
#include <span>
#include <thread>
#include <type_traits>
#include <utility>

namespace riftstone::perf {
enum class ArenaStatus { ok, busy, wrong_thread, exhausted, closed };
namespace arena_detail {
struct State {
    std::mutex mutex;
    std::byte* data;
    const std::size_t capacity;
    const std::thread::id owner=std::this_thread::get_id();
    std::size_t used=0, leases=0;
    std::uint64_t generation=1;
    bool closed=false;
    explicit State(std::size_t n) : data(static_cast<std::byte*>(::operator new(n,std::align_val_t{64}))), capacity(n) {}
    ~State() { ::operator delete(data,std::align_val_t{64}); }
};
}
template<class T> class ArenaHandle;
template<class T> class ArenaLease {
    std::shared_ptr<arena_detail::State> state_;
    T* pointer_=nullptr;
    std::size_t count_=0;
    ArenaLease(std::shared_ptr<arena_detail::State> state,T* p,std::size_t n) : state_(std::move(state)),pointer_(p),count_(n) {}
    friend class ArenaHandle<T>;
    void release() noexcept {
        if (state_) { { std::lock_guard lock(state_->mutex); --state_->leases; } state_.reset(); }
        pointer_=nullptr; count_=0;
    }
public:
    ArenaLease(const ArenaLease&)=delete;
    ArenaLease& operator=(const ArenaLease&)=delete;
    ArenaLease(ArenaLease&& other) noexcept : state_(std::move(other.state_)),pointer_(std::exchange(other.pointer_,nullptr)),count_(std::exchange(other.count_,0)) {}
    ArenaLease& operator=(ArenaLease&& other) noexcept { if(this!=&other){release();state_=std::move(other.state_);pointer_=std::exchange(other.pointer_,nullptr);count_=std::exchange(other.count_,0);}return *this; }
    ~ArenaLease(){release();}
    std::span<T> span() const noexcept { return {pointer_,count_}; }
};
template<class T> class ArenaHandle {
    std::weak_ptr<arena_detail::State> state_;
    std::size_t offset_=0,count_=0;
    std::uint64_t generation_=0;
    friend class FrameArena;
    ArenaHandle(const std::shared_ptr<arena_detail::State>& state,std::size_t offset,std::size_t count) : state_(state),offset_(offset),count_(count),generation_(state->generation) {}
public:
    ArenaHandle()=default;
    std::optional<ArenaLease<T>> lease() const {
        auto state=state_.lock(); if(!state) return std::nullopt;
        std::lock_guard lock(state->mutex);
        if(state->closed || state->generation!=generation_ || state->leases==(std::numeric_limits<std::size_t>::max)()) return std::nullopt;
        ++state->leases;
        return ArenaLease<T>(state,reinterpret_cast<T*>(state->data+offset_),count_);
    }
};
class FrameArena {
    std::shared_ptr<arena_detail::State> state_;
public:
    explicit FrameArena(std::size_t bytes) {
        if(!bytes || bytes>static_cast<std::size_t>((std::numeric_limits<std::ptrdiff_t>::max)())) throw std::bad_array_new_length();
        state_=std::make_shared<arena_detail::State>(bytes);
    }
    FrameArena(const FrameArena&)=delete;
    FrameArena& operator=(const FrameArena&)=delete;
    ~FrameArena(){std::lock_guard lock(state_->mutex);state_->closed=true;}
    template<class T> std::optional<ArenaHandle<T>> allocate(std::size_t count) {
        static_assert(std::is_trivially_default_constructible_v<T> && std::is_trivially_copyable_v<T> && std::is_trivially_destructible_v<T>);
        static_assert(alignof(T)<=64,"arena supports alignment through64 bytes");
        std::lock_guard lock(state_->mutex);
        if(std::this_thread::get_id()!=state_->owner || state_->closed || !count || count>(std::numeric_limits<std::size_t>::max)()/sizeof(T)) return std::nullopt;
        const std::size_t bytes=count*sizeof(T),padding=(0-state_->used)&(alignof(T)-1),remaining=state_->capacity-state_->used;
        if(padding>remaining || bytes>remaining-padding) return std::nullopt;
        const auto offset=state_->used+padding;
        // Start one real array lifetime, not merely adjacent individual objects.
        // Non-allocating placement array-new has no cookie/overhead in C++20.
        ::new(static_cast<void*>(state_->data+offset)) T[count];
        state_->used=offset+bytes;
        return ArenaHandle<T>(state_,offset,count);
    }
    ArenaStatus reset() {
        std::lock_guard lock(state_->mutex);
        if(std::this_thread::get_id()!=state_->owner) return ArenaStatus::wrong_thread;
        if(state_->closed) return ArenaStatus::closed;
        if(state_->leases) return ArenaStatus::busy;
        if(state_->generation==(std::numeric_limits<std::uint64_t>::max)()) return ArenaStatus::exhausted;
        state_->used=0;++state_->generation;return ArenaStatus::ok;
    }
    std::size_t used() const { std::lock_guard lock(state_->mutex);return state_->used; }
    std::size_t capacity() const noexcept {return state_->capacity;}
};
}
