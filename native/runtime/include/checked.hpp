#pragma once
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <span>
#include <vector>

namespace riftstone {
inline bool checked_add(std::size_t a, std::size_t b, std::size_t& result) noexcept {
    if (b > (std::numeric_limits<std::size_t>::max)() - a) return false;
    result = a + b; return true;
}
inline bool checked_mul(std::size_t a, std::size_t b, std::size_t& result) noexcept {
    if (a && b > (std::numeric_limits<std::size_t>::max)() / a) return false;
    result = a * b; return true;
}
inline bool pointer_range(std::uintptr_t start, std::size_t size, std::uintptr_t ceiling) noexcept {
    return start <= ceiling && (!size || size - 1 <= ceiling - start);
}
struct Pattern { std::vector<std::uint8_t> bytes; std::vector<bool> exact; };
enum class LocateResult { absent, unique, ambiguous, invalid };
inline LocateResult unique_signature(std::span<const std::uint8_t> image, const Pattern& pattern,
                                     std::size_t& offset) noexcept {
    if (pattern.bytes.empty() || pattern.bytes.size() != pattern.exact.size() ||
        std::none_of(pattern.exact.begin(), pattern.exact.end(), [](bool b) { return b; })) return LocateResult::invalid;
    bool found = false;
    if (pattern.bytes.size() > image.size()) return LocateResult::absent;
    for (std::size_t at = 0; at <= image.size() - pattern.bytes.size(); ++at) {
        bool match = true;
        for (std::size_t i = 0; i < pattern.bytes.size(); ++i)
            if (pattern.exact[i] && image[at + i] != pattern.bytes[i]) { match = false; break; }
        if (!match) continue;
        if (found) return LocateResult::ambiguous;
        found = true; offset = at;
    }
    return found ? LocateResult::unique : LocateResult::absent;
}
struct Patch { std::size_t offset; std::vector<std::uint8_t> expected, replacement; };
// This mutates an explicitly supplied writable buffer only. The caller must
// quiesce all readers. It never changes page protection or discovers an engine.
inline bool apply_verified(std::span<std::uint8_t> image, const std::vector<Patch>& patches) noexcept {
    for (std::size_t i = 0; i < patches.size(); ++i) {
        const auto& p = patches[i];
        if (p.expected.empty() || p.expected.size() != p.replacement.size() ||
            p.offset > image.size() || p.expected.size() > image.size() - p.offset ||
            std::memcmp(image.data() + p.offset, p.expected.data(), p.expected.size())) return false;
        for (std::size_t j = 0; j < i; ++j) {
            const auto& q = patches[j];
            if (p.offset < q.offset + q.expected.size() && q.offset < p.offset + p.expected.size()) return false;
        }
    }
    for (const auto& p : patches) std::memcpy(image.data() + p.offset, p.replacement.data(), p.replacement.size());
    return true;
}
struct Expansion { std::size_t offset{}, bytes{}, count{}, stride{}; };
inline bool expansion_plan(std::size_t original_size, std::size_t original_offset,
                           std::size_t old_count, std::size_t stride, std::size_t requested,
                           std::size_t measured_maximum, std::uintptr_t ceiling, Expansion& out) noexcept {
    std::size_t old_bytes, end, bytes, total;
    if (!stride || !old_count || requested < old_count || requested > measured_maximum ||
        !checked_mul(old_count, stride, old_bytes) || !checked_add(original_offset, old_bytes, end) ||
        end > original_size || !checked_mul(requested, stride, bytes) ||
        !checked_add(original_size, bytes, total) || !pointer_range(0, total, ceiling)) return false;
    out = {original_size, total, requested, stride}; return true;
}
// Handle-based storage avoids returning pointers invalidated by growth. This
// is staging storage, not a replacement for a game's unmodified pointer ABI.
class SlotArena {
public:
    struct Handle { std::uint32_t index{}, generation{}; };
    explicit SlotArena(std::size_t stride, std::size_t maximum) : stride_(stride), maximum_(maximum) {}
    bool grow(std::size_t count) {
        std::size_t bytes;
        if (!stride_ || count < generations_.size() || count > maximum_ || count > UINT32_MAX ||
            !checked_mul(count, stride_, bytes)) return false;
        auto storage = data_; auto generations = generations_; auto occupied = occupied_;
        storage.resize(bytes); generations.resize(count, 1); occupied.resize(count, false);
        data_.swap(storage); generations_.swap(generations); occupied_.swap(occupied); return true;
    }
    bool acquire(Handle& out) noexcept {
        for (std::size_t i = 0; i < occupied_.size(); ++i) if (!occupied_[i] && generations_[i]) {
            occupied_[i] = true; out = {static_cast<std::uint32_t>(i), generations_[i]}; return true;
        }
        return false;
    }
    bool release(Handle handle) noexcept {
        if (!valid(handle)) return false;
        occupied_[handle.index] = false;
        // Exhausted generations retire the slot instead of making stale handles valid.
        ++generations_[handle.index];
        std::memset(data_.data() + handle.index * stride_, 0, stride_); return true;
    }
    bool write(Handle h, std::span<const std::uint8_t> bytes) noexcept {
        if (!valid(h) || bytes.size() != stride_) return false;
        std::memcpy(data_.data() + h.index * stride_, bytes.data(), stride_); return true;
    }
    bool read(Handle h, std::span<std::uint8_t> bytes) const noexcept {
        if (!valid(h) || bytes.size() != stride_) return false;
        std::memcpy(bytes.data(), data_.data() + h.index * stride_, stride_); return true;
    }
private:
    bool valid(Handle h) const noexcept { return h.index < occupied_.size() && occupied_[h.index] && generations_[h.index] == h.generation; }
    std::size_t stride_, maximum_;
    std::vector<std::uint8_t> data_;
    std::vector<std::uint32_t> generations_;
    std::vector<bool> occupied_;
};
} // namespace riftstone
