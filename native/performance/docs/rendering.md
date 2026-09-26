# Conservative occlusion and offscreen indirect rendering

This component contains a C++20 CPU occlusion library and a standalone x64 D3D12
compute/ExecuteIndirect correctness demo. It does not hook a game or modify draw
submission. DDDA uses D3D9; its renderer does not acquire D3D12 ExecuteIndirect
through an API rename. DDDA integration, engine occluder extraction, real scene
draw-call reduction, image stability, and gameplay FPS remain **UNKNOWN**.

## Build and run

From the repository root, using an installed Python and MSVC x64/Windows SDK:

```text
python native/performance/build_render.py
python native/performance/run_render.py --seconds 120 --warp
```

The builder discovers the installed compiler with `vswhere`, normalizes Windows
environment key casing, and uses bounded hidden subprocesses. It does not
download, install, deploy, open a window, or start a game. Outputs go under the
ignored `native/performance/out/`: `occlusion.lib`, `render_host.exe`,
`indirect_demo.exe`, build log/receipt and structured validation reports. The
receipt records source and binary SHA-256 hashes. The library and hosts use
`/O2 /MT /fp:strict`, C++20, and warnings as errors. The static library is x64
and must be linked with compatible compiler/runtime settings.

Before launching hosts, the runner checks every owned source hash and required
artifact hash in the build receipt. A stale binary or changed source fails
closed and requires a rebuild. `source_sha256` paths are relative to
`native/performance`; `artifacts` keys are basenames relative to `out/`.

`render_host --seconds 0` runs a smoke property scene and the fixed regressions.
The default runner instead performs a 120-second seeded property test.
`indirect_demo` selects a hardware D3D12 adapter. `indirect_demo --warp`
explicitly requests WARP and reports a software adapter. Hardware failure to
provide D3D12 is `UNAVAILABLE`, not a successful hardware test. The combined
runner preserves that distinction even if WARP succeeds. All drawing targets
an offscreen texture; there is no HWND or swap chain.

## CPU contract

Public declarations are in `include/occlusion.hpp`, namespace
`riftstone::perf`:

* `project_bounds(Aabb, Mat4, width, height)` projects all eight corners of a
  world-space AABB with a row-major matrix multiplying column vectors. It
  returns pixel-edge coordinates with a top-left origin, plus the nearest
  normal-Z depth. Dot-product roundoff bounds expand projected ranges. An
  uncertain near/far-plane crossing, nonpositive or ill-conditioned W, invalid
  matrix, nonfinite input, or excessive projection returns an invalid rectangle.
* `DepthPyramid::rasterize(width, height, triangles)` accepts opaque triangles
  in D3D clip space. Normal Z is zero at the near plane and one at the far plane.
  Invalid or clipped triangles are ignored as occluders. Every written cell
  must lie wholly inside one triangle, including all four corners with a
  conservative numerical margin. Its depth is the triangle's farthest vertex
  depth plus a bias. Uncovered cells stay at one. This deliberately sacrifices
  occlusion opportunities at triangle seams and on sloped geometry.
* `DepthPyramid::build(width, height, depths)` accepts the same **complete-cell
  conservative depth contract** from another producer. Each input value must
  upper-bound the nearest opaque surface depth at every point of that cell.
  Ordinary coarse rasterizer center samples or averaged depth do not satisfy
  this contract. Nonfinite/out-of-range values become uncovered depth one.
* `DepthPyramid::occluded(ScreenRect)` returns true only when a conservative
  expanded rectangle is strictly behind every selected depth cell. Invalid,
  degenerate, offscreen, near-plane or uncertain inputs return false (visible).

MAX reduction is required for normal Z: a hole must propagate toward the root.
Odd-sized mip levels retain valid edge cells. Queries expand by one full cell
and choose a mip whose cells cover a superset of the rectangle. This can retain
objects that a full-resolution scan would cull; it cannot justify culling across
a hole under the stated input contract. Reversed Z is unsupported. Dimensions
are bounded to 16,384 per axis and 16,777,216 total cells; size mismatches throw
`std::invalid_argument` before replacing existing state. A built pyramid is
read-only and supports concurrent queries; callers must serialize rebuilding.

Occluders must be opaque and valid in the current view. Alpha-tested vegetation,
transparency, missing geometry, moving occluders, stale previous-frame depths,
or a camera/projection mismatch require an appropriate conservative producer
or must remain visible. Object bounds must enclose the actual rendered object.
This scalar implementation is inspired by hierarchical Z, not a reproduction
of Intel's masked SIMD algorithm. It makes no AVX2/AVX-512 throughput claim.

## GPU contract and what the demo proves

The CPU uploads a flattened conservative depth pyramid and bounds. Compute
tests those bounds, atomically compacts 20-byte command records, and writes a
draw count. Each record contains an object-index root constant followed by
`D3D12_DRAW_ARGUMENTS`. Output capacity equals the admitted input count.
Explicit UAV barriers and transitions to `INDIRECT_ARGUMENT` precede
`ExecuteIndirect`. The count buffer limits consumed records; no CPU readback
is used to choose the draw count. The root signature matches the command
signature. Rendering uses an offscreen RGBA8 texture and a fence before readback
or resource release. GPU wait failures fail-stop the owned host. Root SRVs are
only used after the depth/view dimensions and object capacities are checked.

The demo draws solid-white object bounding rectangles as two triangles, with
depth testing disabled. It verifies culling and command execution, not textured
meshes, scene-graph traversal, LOD selection, or production material binding.
The depth pyramid is produced on the CPU; compute does not build it on the GPU.
The records are unordered after atomic compaction, so tests compare sorted IDs
and use order-independent solid-white union coverage. Every generated command
field and every RGBA pixel are read back and checked against CPU expectations.
Empty, fully visible, fully occluded, hole, triangle-rasterized and invalid-input
scenes exercise count/reset, dispatch tails, coverage and conservative cases.
NaN/invalid bounds remain in the visible list; invalid geometry is suppressed
in the demonstration vertex shader to avoid issuing nonfinite clip positions.

HLSL is compiled with IEEE strictness and warnings as errors. The demo tries
the D3D12 debug layer if already installed; absence is reported, and the error
count is null rather than an observed zero. Readback correctness still runs.
It does not install Graphics Tools or change system settings.

## Evidence and interpretation

The initial local correctness run passed on **NVIDIA GeForce RTX 5070** and on
explicitly requested **Microsoft Basic Render Driver / WARP**: 20 scenes,
17,884 input objects, 12,802 visible command records and 737,280 RGBA pixels per
adapter. The D3D12 debug layer was unavailable. Final reports under `out/`
contain the exact executable hashes, property duration, case counts and timing
samples for the tested build; the initial measurements below are not universal
budgets or game measurements.

The synthetic CPU benchmark uses 256x144 depth, two opaque wall triangles and
4,096 random bounds. Initial measured rasterization plus pyramid cost was
about **0.42 ms**, exceeding the pasted 0.3 ms claim. Hi-Z query time was about
**0.10 ms**, versus **0.19 ms** for an optimized full-resolution scalar scan of
the same bounds. Hi-Z culled **1,371/4,096 (33.5%)**, while the scalar oracle
culled 1,470; the hierarchy intentionally kept 99 additional objects visible.
The test retains those outcomes rather than assuming a 60% reduction.

The first complete 120-second run passed **643,881 randomized scenes,
54,086,004 queries and 2,759,405,762 checks**, including full-resolution scalar
visibility, independent triangle depth/coverage samples at centers and cell
corners, odd dimensions, holes and invalid values. That run measured a slower
**0.660 ms** raster-plus-pyramid pass, **0.104 ms** Hi-Z queries and **0.224 ms**
scalar queries. It is retained as `out/render-validation-first-120s.json`;
the final receipt-validated run is separate. No false occlusion was found in
these sampled cases; this is test evidence, not an exhaustive numeric proof.

Initial hardware GPU compute-plus-draw timestamps averaged approximately
**0.027 ms**, with CPU queue submission and fence wait averaging **0.31 ms**.
WARP averaged approximately **5.05 ms** for its reported compute-plus-draw
interval. Timing excludes CPU scene generation, uploads/resource allocation,
shader compilation and later validation; it includes synthetic rectangle
rendering, not a game frame. Cold starts, concurrent load and adapter scheduling
affect these samples. The host intentionally allocates resources per test and
synchronizes for correctness, rather than serving as a production frame loop.

CPU overhead is not zero: the CPU still creates resources/pipelines, supplies
scene data, records/submits commands and manages synchronization. Indirect draws
can reduce per-object submission work when a renderer is designed for them;
neither this mechanism nor occlusion guarantees a particular FPS, draw-distance
increase, culling percentage, or exclusive rendering of visible pixels.

## Primary references

* Greene, Kass and Miller, [Hierarchical Z-Buffer Visibility (SIGGRAPH 1993)](https://www.cs.princeton.edu/courses/archive/spr01/cs598b/papers/greene93.pdf).
* Intel, [MaskedOcclusionCulling implementation and research references](https://github.com/GameTechDev/MaskedOcclusionCulling). Its specialized masked representation and SIMD implementation differ from this conservative scalar baseline.
* Microsoft, [Indirect drawing](https://learn.microsoft.com/en-us/windows/win32/direct3d12/indirect-drawing): root arguments, command signatures and tightly packed indirect argument records.
* Microsoft, [ID3D12GraphicsCommandList::ExecuteIndirect](https://learn.microsoft.com/en-us/windows/win32/api/d3d12/nf-d3d12-id3d12graphicscommandlist-executeindirect): count limits, buffer state requirements and root-signature compatibility.
