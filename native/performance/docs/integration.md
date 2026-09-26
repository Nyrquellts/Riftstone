# Immutable snapshot, task graph and frame arena

This example links the actual `scheduler.lib` and `data_kernels.lib`. Its C++20
host operates entirely on owned synthetic data. No callback contains an engine
pointer or calls a game API. This demonstrates a usable integration boundary;
it does not establish that DDDA AI or render work may run on these workers.

```console
python native/performance/build_integration.py --arch both --test
```

The builder verifies component source/artifact hashes and reuses current builds.
Missing or stale components are rebuilt through their existing build scripts.
It builds hidden x86/x64 hosts with static CRT, `/GT` and `/fp:strict`, then writes
source, library and executable hashes to `out-integration-{x86,x64}/build.json`.
`validation.json` preserves test output and failures. No broad fuzz pass or
benchmark is repeated by this focused integration check.

Read `test/integration_host.cpp` for the complete example. Its sequence is:

1. Construct a **const** SoA snapshot and a serial reference output. Build one
   reusable graph outside the frame loop. Partition every entity into disjoint
   13-element chunks, including a final partial chunk.
2. Allocate all three output streams in one frame-arena block. Hold one bulk
   lease on the control thread and bind the graph's borrowed output spans before
   calling `run`. Graph callbacks use only their own output slices.
3. Jobs cooperatively yield, invoke the selected checked integration kernel and
   record per-entity visits. A dependent validation task sees all predecessor
   writes and compares the output with the scalar reference bit for bit.
4. Treat synchronous `run` return as the worker barrier. Read/verify the outputs
   while the lease is still alive; the arena continues to refuse reset even
   though the jobs have finished.
5. Clear the graph's borrowed output view, release the lease, then reset the
   arena. The prior frame's handle cannot acquire another lease. Bind a new
   allocation before reusing the graph.

The graph captures stable control variables by reference. Only the control
thread changes them, between completed runs. Running that graph without binding
valid current-frame outputs is outside this example's usage contract. A lease
guards lifetime; disjoint slices and the barrier provide the write-ownership
and publication rules.

The host covers 30, 64 and 3,073 entities, scalar and automatic dispatch, one and
four workers, and twenty frames per combination: 240 frames per architecture.
It verifies exact results, signed-zero/subnormal inputs, untouched snapshot
fingerprints, output sentinels, exactly one visit per entity, lease/reset order,
and stale handles. An injected callback exception also checks that a suspended
job's arena lease unwinds before the scheduler returns the error; the arena and
pool are reusable afterwards.

These are correctness and lifetime tests, not a claim that the combined pipeline
beats the serial baseline at these sizes. Component benchmarks retain their
measured small-workload regressions in [data-memory.md](data-memory.md) and the
scheduler reports. Actual engine adaptation still needs independently verified
ownership, thread affinity and a measured gameplay regression test.
