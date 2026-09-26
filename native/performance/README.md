# Measured performance modules

These C++20 Windows modules implement application-owned data processing and
offscreen rendering. They do not hook an engine, deploy a plugin, or make a game
frame-rate claim. The associated standalone NYR-Lang candidate adds opt-in typed
selection compilation; its source and validation are distributed separately.

| Module | Working interface | Boundary |
| --- | --- | --- |
| Hot/cold data and AVX2 | `hot_data.hpp`, `data_kernels.lib` | Finite checked snapshots; CPU/OS feature detection; scalar fallback |
| Frame arena | `frame_arena.hpp` | Bounded aligned storage, generation handles and leases; reset after all users finish |
| Work stealing and fibers | `fiber_scheduler.hpp`, `scheduler.lib` | Persistent Windows workers; bounded lock-free deques; static DAG; cooperative yields |
| Combined frame example | `test/integration_host.cpp` | Disjoint snapshot chunks, bulk lease and explicit completion barrier |
| Conservative CPU Hi-Z | `occlusion.hpp`, `occlusion.lib` | Complete-cell opaque coverage; normal-Z MAX hierarchy; uncertain objects stay visible |
| GPU indirect rendering | `indirect_demo.exe`, `shaders/` | Offscreen D3D12 compute culling, indirect records and pixel readback |

## Build and validate

Use Python 3.12+ with the installed MSVC C++ tools and Windows SDK:

```powershell
python -B native/performance/build.py --test
python -B native/performance/test/run_tests.py --require-all --gpu both
```

The first command builds the data, scheduler and integration host for x86 and
x64, plus the x64 rendering library and hosts. The second also explicitly tests
the WARP software adapter. Every child process is hidden. No build downloads or
game launch are performed. Individual builders allow narrower rebuilds; data
builds accept `--vs PATH` if the default local installation differs.

For CPU modules only, use `build.py --skip-render --test`. `--arch x86` builds
32-bit data/scheduler/integration components; the separate rendering build is
x64 unless skipped. All native modules use a static CRT and strict floating
point. Build clients consistently with C++20 and `/fp:strict`; callback code on
fibers should use `/GT`. Do not pass engine-owned entities to worker jobs.

The quick combined gate saves `out/combined-validation.json`. It runs only
existing hosts and reports missing components; `--require-all` turns unavailable
builds or GPUs into a failure. `tools/test_all.cmd` includes this quick gate and
explicitly reports any missing performance builds. Component stress receipts remain separate:

```powershell
python -B native/performance/build_scheduler.py --arch both --test --fuzz-seconds 120
python -B native/performance/test/run_data.py --arch both --fuzz-seconds 120
python -B native/performance/run_render.py --seconds 120 --warp
```

## Integration contracts and measurements

Read [data/memory](docs/data-memory.md), [scheduling](docs/scheduling.md),
[integration](docs/integration.md), and [rendering](docs/rendering.md) before
using the APIs. The packaged validation reports include source/binary hashes,
negative timing results and the actual adapter used. Timing a synthetic host
does not measure engine gather/scatter costs, animation, physics, resource
lifetime, GPU frame overlap, or player-visible frame times.

Fixed cycle counts, zero kernel scheduling, 100% utilization, unlimited draw
distance, a removed 32-bit address-space limit and guaranteed 144 FPS are not
properties of these algorithms. Batching, data size and lifetime boundaries
determine whether a change helps. The measured examples deliberately include
30 and 64 objects as well as larger workloads.

DDDA's engine thread ownership and renderer integration are not established by
these tests. A D3D12 indirect command is not a drop-in call for its D3D9 renderer.
Any future engine adapter must establish those contracts and measure a real
frame capture before changing dispatch or culling behavior.
