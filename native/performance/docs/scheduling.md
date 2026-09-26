# Bounded work stealing and Windows fibers

This C++20 module schedules an application-owned task graph on persistent Windows
worker threads. It does not move DDDA actor, physics, render or VM calls to another
thread. Their synchronization and thread-affinity contracts remain unverified.

`Scheduler` preallocates a fixed pool of real Windows fibers and task records.
`TaskGraph` is built outside `run`; dependencies refer only to existing earlier
nodes, so cycles cannot be constructed. A successor becomes ready after all of
its predecessors complete. Their completion uses acquire/release ordering so
their output writes are visible to the successor. A callback can explicitly
`yield()` to let its worker run another ready job. Dependencies are represented at
task boundaries; there is no blocking `await` or dynamic spawn inside a callback.

Ready, unstarted jobs may be stolen. Once started, a fiber stays on its original
OS thread, including after suspension. Windows still schedules those threads;
the implementation neither pins CPU affinity nor claims zero kernel context
switches. A job must not hold an OS mutex or engine lock across `yield`, block on
external I/O, recursively call `run`, or retain its `FiberContext`. Cooperative
jobs must return or yield within their caller's budget. There is no safe forced
termination of arbitrary C++ callbacks.

## API and lifetime

```cpp
#include "fiber_scheduler.hpp"
using namespace riftstone::perf;
Scheduler scheduler({4, 4096, 8, 256 * 1024});
TaskGraph frame;
std::vector<TaskId> updates;
for (std::size_t chunk = 0; chunk < 8; ++chunk)
    updates.push_back(frame.add([&, chunk](FiberContext&) {
        // Only this chunk's application-owned output; no engine calls here.
        update_snapshot_chunk(chunk);
    }));
frame.add([&](FiberContext&) { validate_snapshot_result(); }, updates);
auto stats = scheduler.run(frame);
// run is a barrier: all workers are quiescent before captured storage is freed.
// Commit the validated result to the engine only through an established adapter.
```

The graph, its captured state, and the scheduler must remain alive and unmodified
through `run`. Distinct tasks must own disjoint writes or synchronize themselves.
The control thread performs the frame barrier. Concurrent or recursive runs are
refused. Reuse a graph for subsequent frames after the previous barrier. No memory
allocation occurs in the normal scheduler dispatch loop; user callbacks and
exception creation may allocate. Queue storage and fiber stacks are allocated
at scheduler construction, and task graph storage when adding nodes.

The first callback exception closes dispatch. Suspended frames resume on their
own workers and unwind through `yield`, running ordinary C++ destructors. The
control thread receives the exception after every worker leaves the epoch; the
pool can be reused. Callbacks must not swallow the scheduler's cancellation and
continue forever. Native SEH faults/stack exhaustion are not C++ recovery cases.
Destructor calls require an idle pool and must not race `run`.

The deque has exactly one concurrent push/pop owner and multiple thieves, atomic
slots, separate aligned head/tail counters, no buffer reclamation, and explicit
capacity/index-overflow refusal. Reset happens only between quiescent epochs.
It checks that the required atomics are lock-free on the executing target.
The whole scheduler is **not lock-free**: frame barriers, idle-worker wakeups and
exception collection use synchronization primitives. Idle workers sleep between
frames; during an active frame without a ready job they yield to the OS.

## Build and validate

```powershell
python native/performance/build_scheduler.py --arch both --test --fuzz-seconds 120
```

The builder uses installed MSVC with C++20, `/O2 /W4 /WX /MT /GT /fp:strict`.
It produces `scheduler.lib` and `scheduler_host.exe` for x86 and x64. `/GT` and
`FIBER_FLAG_FLOAT_SWITCH` support fiber-safe compilation and x86 floating-point
state isolation. Link the matching architecture/static-CRT library, and keep
C++ object allocation and ownership within compatible modules. This is not an
engine C ABI and does not replace Ninput's hook registry or existing adapters.

The owned hidden host verifies repeated randomized DAGs, dependency write
visibility, exact task counts, cooperative yields/thread identity, floating-point
rounding state, exception unwinding, reuse, and rejected concurrent/nested runs.
The timed fuzz pass also races deque owners and thieves at small capacities,
forcing circular-buffer reuse and last-item contention; every task must be taken
exactly once. These runs are regression/stress evidence, not a proof for all
possible schedules or architectures.

## Measurements

The host emits JSON lines and the builder saves `validation.json`. Benchmarks
compare a serial reference with four workers, individual jobs and 64-entity jobs,
at 30, 64, 3,073 and 65,536 synthetic entities. Each entity performs 64 dependent
unsigned-integer mixing operations. Input/output checksums must agree after all
31 frames; medians and p95 use the 25 frames after warm-up. Scheduler construction
time is reported separately. Frame scheduling/barrier overhead remains included.
All timings, including regressions, are retained. This workload is not DDDA AI,
animation or navigation, and neither frame rates nor cache misses are measured.
Hardware, OS load, compiler and job size influence the result.

## Primary references

- [Chase and Lev, Dynamic Circular Work-Stealing Deque](https://www.cs.wm.edu/~dcschmidt/PDF/work-stealing-dequeue.pdf):
  the owner/thief algorithm; this implementation intentionally uses bounded,
  non-growing storage and atomic slots instead of dynamic reclamation.
- [Le et al., Correct and Efficient Work-Stealing for Weak Memory Models](https://fzn.fr/readings/ppopp13.pdf):
  memory ordering matters; a paper's proof does not automatically prove a new
  implementation. No third-party source file is included here.
- [Microsoft Windows fibers](https://learn.microsoft.com/en-us/windows/win32/procthread/fibers),
  [CreateFiberEx](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createfiberex),
  and [MSVC `/GT`](https://learn.microsoft.com/en-us/cpp/build/reference/gt-support-fiber-safe-thread-local-storage?view=msvc-170):
  scheduling, TLS, floating-point flags and compiler constraints.
