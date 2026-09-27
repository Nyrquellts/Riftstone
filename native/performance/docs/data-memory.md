# Hot data, SIMD integration and guarded frame storage

These C++20 components operate on caller-owned data in a synthetic host. They do
not hook DDDA, migrate engine AI state, remove its enemy cap, or establish any
game FPS gain. The data layout has no relationship to a measured engine ABI.

## Build and run

```console
python native/performance/build_data.py --arch both
python native/performance/test/run_data.py --arch both --fuzz-seconds 120
```

The build uses the installed MSVC directory specified by `--vs` (default
`<path>`). It reuses the existing runtime
builder's vcvars/environment-cache approach, with architecture-specific caches
and normalized environment names. No download, install, game process or window
is started. Outputs are `out-data-x86` and `out-data-x64`: `data_kernels.lib`,
`data_host.exe`, `build.json`, `build.log`, `test-benchmark.json` and `fuzz.json`.
The library and host use static CRT `/MT`, `/O2`, `/fp:strict`, and the MSVC C++20
ABI; link callers with compatible compiler/runtime settings. This is a static
library, not a C-compatible DLL interface.

`hot_data.cpp` targets baseline SSE2 and marks the scalar kernel `no_vector`.
Only `hot_data_avx2.cpp` uses `/arch:AVX2`. No link-time cross-module optimization
is enabled. Dispatch checks CPU AVX/AVX2, OSXSAVE, and the XCR0 XMM/YMM state bits
before calling that object. Unsupported explicitly requested AVX2 returns an
error. The scalar backend is callable even on an AVX2 machine. Testing on this
machine does not constitute execution on hardware without AVX2.

## Data API and numerical contract

`riftstone::perf::HotData` owns nine independently allocated, 64-byte-aligned
streams: `x/y/z`, `vx/vy/vz`, `hp`, `radius`, and `flags`. `ColdData` separately
owns names, dialogue, asset IDs and inventories. Applications associate indices
and publish snapshots themselves; resizing, deleting or reordering entities is
not a concurrent operation provided by this library.

```cpp
using namespace riftstone::perf;
HotData hot(64);
// Initialize velocities, positions, hp, radius and flags from an owned snapshot.
auto result = integrate(hot.view(), hot.positions(), 1.0f / 60.0f);
```

Every stream must have the same length; empty streams are accepted. All span
storage must remain valid for the call. `integrate` supports separate outputs or
exactly matching in-place position spans. It rejects output/output overlap,
cross-axis overlap and output overlap with velocity, hp, radius or flag storage.
Span validity and freedom from concurrent writes remain the caller's obligation.

Inputs and `dt` must be finite binary32; radius must be nonnegative. A bit test
rejects NaNs, including signaling NaNs, without using them in comparisons. All
validation completes before any output write. Allocation is outside the kernel.
Unknown flag bits are preserved/ignored; `frozen` and `staggered` disable motion.
The operation is, independently for each coordinate:

```
active = hp > 0 && (flags & (frozen | staggered)) == 0
output = active ? position + round_binary32(velocity * dt) : position
```

Multiplication and addition are separate; no FMA contraction is used. Scalar and
AVX2 paths preserve identical output bits, including inactive signed zero.
Finite inputs may produce IEEE infinity through overflow, or subnormal/zero
through underflow. Those outputs are permitted; a later call treating infinity
as an input is refused. The API requires round-to-nearest, masked FP exceptions,
and disabled FTZ/DAZ. It checks MXCSR and refuses other modes without changing
the caller's controls. Sticky exception flags are not part of the equivalence
claim: SIMD may evaluate arithmetic for inactive lanes before selecting their
unchanged positions. No trap behavior or NaN-payload equivalence is claimed.

The SIMD loop uses unaligned loads/stores and processes only full groups of
eight; the remaining elements use the scalar implementation. Alignment of owned
streams helps layout but is not required of borrowed views. Functions in
`detail` bypass validation for implementation/benchmark purposes; application
code should use `integrate`.

## FrameArena lifetime contract

`FrameArena(bytes)` obtains one aligned bounded allocation. The capacity cannot
exceed `PTRDIFF_MAX`; allocation failure remains an ordinary exception. It does
not reserve memory outside the process address space or bypass 32-bit limits.

```cpp
FrameArena frame(32 * 1024 * 1024);
auto block = frame.allocate<float>(3073);
if (block) {
    auto lease = block->lease();
    // Access lease->span() only while this move-only lease remains alive.
}
auto reset = frame.reset(); // succeeds only after every lease has been released
```

Allocation/reset belong to the creating thread. Handles can acquire leases on
other threads; acquisition, reset and release are synchronized. A live lease
keeps storage allocated and makes reset return `busy`. After reset, old handles
fail their generation check. Destroying the arena closes it to new leases while
existing leases keep their backing allocation alive. This does not synchronize
two workers writing the same element. Join jobs or otherwise establish exclusive
element ownership before reading their results.

Only trivially default-constructible, copyable and destructible element types
with alignment at most 64 are accepted. Placement array construction explicitly
starts their lifetimes. Element initialization is the caller's responsibility.
Zero-length requests, count multiplication overflow, exhausted capacity and
foreign-thread allocations are refused without advancing the offset. Alignment
padding counts against capacity. There is no per-object free or automatic
destructor list. Retaining a raw pointer after its lease ends violates the
contract; C++ cannot prevent that escape.

The guard costs mutex/shared-owner operations. Reset does constant bookkeeping,
but is neither zero-cost nor a replacement for synchronization. A per-element
guard can be slower than the CRT heap; a bulk lease amortizes that cost. The
benchmark reports both instead of promising an unconditional allocator gain.

## Evidence and measurement limits

The host checks exact outputs against an independent SSE scalar reference,
signed zero, subnormals, overflow, inactive states, mismatched shapes, aliasing,
nonfinite inputs and nondefault FP modes. Buffers ending at an inaccessible page
exercise vector tails. Arena tests cover alignment, exhaustion, overflow, reset
with live leases, move/release, stale handles, cross-thread use and destruction.
Seeded fuzzing repeats bitwise backend comparisons and arena accounting/lifetime
invariants for the requested wall time. Failures and raw output remain in JSON.

Benchmarks use 30, 64, 3,073, 65,536 and 262,144 synthetic entities. The AoS baseline
has a 256-byte stride, including synthetic cold padding; SoA has 36 hot bytes per
entity. Both checked paths validate finite inputs and write three coordinate
streams. SoA additionally checks span shape/overlap and dispatch; its cost stays
in the reported result. A separate internal-kernel timing excludes those checks
and must not be presented as the whole API cost. Each row is the median of seven
batch averages, with iterations recorded. It is not a confidence interval.

The allocator comparison measures one frame of individually allocated heap
objects, individually guarded arena objects, and one bulk arena allocation.
Timings include resets/releases. Benchmarks run separately from this driver's
fuzz workers, without pinning or changing system priority/power configuration.
Other desktop workloads, CPU frequency and cache residency are uncontrolled.
Results include regressions and checksums. These timings are not game-frame
measurements, scheduler measurements, or evidence for an engine integration.

Measured on the Intel Core Ultra 9 285 host (24 logical processors), without
affinity control. The following is **checked SoA dispatch time / checked AoS
time**; values above 1 are regressions. Raw nanosecond measurements, scalar results
and kernel-only timings remain in each architecture's JSON report.

| Entities | x86 ratio | x64 ratio |
|---:|---:|---:|
| 30 | 1.190 | 2.854 |
| 64 | 1.377 | 1.921 |
| 3,073 | 0.825 | 1.102 |
| 65,536 | 0.411 | 0.341 |
| 262,144 | 0.199 | 0.176 |

Individually guarded arena allocation was slower than individual CRT allocation
at every measured count in both builds. Bulk arena allocation was faster in
these tests. At 3,073 objects, x86 measured 108,825 ns heap / 265,584 ns individual
arena / 1,509 ns bulk arena; x64 measured 87,272 ns / 187,234 ns / 1,506 ns respectively.
These specific synthetic results do not support a 30-enemy speedup claim.

## Research references

- [Mike Acton's original CppCon2014 presentation](https://github.com/CppCon/CppCon2014/tree/master/Presentations/Data-Oriented%20Design%20and%20C%2B%2B)
  motivates choosing layouts for the transformations performed. This component
  applies that idea to a specific position integration workload, not every object.
- [Intel architecture extensions reference](https://www.intel.com/content/dam/develop/external/us/en/documents/319433-024-697869.pdf)
  documents feature enumeration and OS extended-state checks. AVX2 availability
  requires more than observing one CPUID bit.
- [Microsoft floating-point mode reference](https://learn.microsoft.com/en-us/cpp/build/reference/fp-specify-floating-point-behavior?view=msvc-170)
  explains why the strict build avoids reassociation/contraction assumptions.
- [Tofte and Talpin, Region-Based Memory Management](https://web.cs.ucla.edu/~palsberg/tba/papers/tofte-talpin-iandc97.pdf)
  studies grouping lifetimes into regions. This runtime lease guard is a simpler
  explicit C++ mechanism; it is not their static region-inference/type system.

The supplied claims of fixed cache-miss costs, a one-cycle eight-entity update,
zero-cost allocation, and eliminating the 32-bit wall are not established by
these sources or these tests. Costs depend on hardware, layout and workload;
measured host timings and explicit unsupported boundaries take precedence.
