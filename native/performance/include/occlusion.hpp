#pragma once
#include <array>
#include <cstdint>
#include <span>
#include <vector>

namespace riftstone::perf {
struct ClipVertex { double x{}, y{}, z{}, w{1}; };
struct ClipTriangle { std::array<ClipVertex, 3> vertices; };
struct Aabb { std::array<double, 3> minimum{}, maximum{}; };
struct Mat4 { std::array<double, 16> row_major{}; };
// Pixel-edge coordinates, top-left origin; D3D normal Z (near0, far1).
struct ScreenRect { double min_x{}, min_y{}, max_x{}, max_y{}, nearest_depth{}; bool valid{false}; };
ScreenRect project_bounds(const Aabb&, const Mat4&, std::uint32_t width, std::uint32_t height) noexcept;
struct DepthLevel { std::uint32_t width{}, height{}; std::vector<float> depth; };
class DepthPyramid {
public:
    // Caller-supplied depth must upper-bound the closest opaque surface over
    // the ENTIRE pixel cell. A low-resolution center sample is insufficient.
    void build(std::uint32_t width, std::uint32_t height, std::span<const float> depth);
    // Only fully covered cells are written, with an upper depth bound. Invalid
    // or near-plane-crossing occluders are ignored, never guessed or clipped.
    void rasterize(std::uint32_t width, std::uint32_t height, std::span<const ClipTriangle> triangles);
    bool occluded(const ScreenRect&) const noexcept;
    const std::vector<DepthLevel>& levels() const noexcept { return levels_; }
    static constexpr float depth_bias = 0.00001F;
private:
    std::vector<DepthLevel> levels_;
};
}
