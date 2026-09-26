# Owned clone appearance synchronization

This C++20 PE32 DLL synchronizes a typed host-side actor's equipment appearance.
It also intercepts a real exported entity factory with MinHook in `clone_host.exe`.
Its `Actor`, `Resource`, and `Appearance` types are **not DDDA structure layouts**.
`RsClone_GameProfile` always returns UNAVAILABLE for a nonempty game profile.
No game address, `mEventActor` offset, material reference-count function, or render
barrier is guessed, and the DLL is never injected or installed by these commands.

## Build and run

```
python native/clone_sync/build.py --minhook PATH/TO/minhook-1.3.4
python native/clone_sync/test/run_tests.py
```

Installed MSVC x86 is required. `--msvc-cache PATH` optionally reuses the runtime
build's compiler-path cache after checking its vcvars timestamp. Builds do not
download dependencies or deploy. MinHook's approved source receipt and license
are in `native/runtime/dependencies.json` and `native/runtime/licenses/MinHook.txt`.
DLL and host share the dynamic CRT. Logs, artifact hashes and results are written
to `native/clone_sync/out`.

## Contract

The primary actor publishes an immutable `Appearance` owner under its mutex.
The synchronizer locks both actors together, verifies matching joint IDs and
parent topology, validates finite morph values and normalized bounded skin weights,
and prepares a complete copy before committing it. Weights and morph vectors have
independent storage. Mesh and material buffers retain shared immutable owners, so
retiring the primary actor cannot leave the clone pointing at freed storage.
No engine pointers are borrowed or cast to these types. The mesh/material `Kind`
tag is host metadata, not validation of an arbitrary MOD/MRL binary.

Entity identity and animation frame are preserved. Source generation, equipment,
morphs and weights are published together. Invalid input, skeleton mismatch and
retired actors leave the previous destination appearance untouched. Limits bound
parts, joints, morphs and total weight rows. The caller must keep each Actor object
alive during calls and must use the mutex when publishing a new appearance;
retirement protects resource ownership, not a caller deleting an Actor concurrently.

Only the current executable's explicit `RsSyntheticCreateCloneV1` export and
`RsSyntheticCloneAbiV1` marker opt into the fixture ABI. The factory returns a new,
unready Actor with the requested skeleton; its hook synchronizes that result.
An incompatible result stays unready. DLL initialization does no hook work in
DllMain. Enable/Disable are explicit, accepted factory calls are serialized during
lifecycle transitions, and disabled trampolines/module code remain retained until
process exit. Reinstallation is refused. This does not interoperate with an engine
allocator or promise DLL unloading.

The regression host demonstrates the missing appearance before interception and
the synchronized result after interception. It checks resource lifetime, deep-copy
storage, skeleton and weight rejection, immutable snapshots, player retirement,
and concurrent source updates/clone requests while disabling the hook. The runner
starts five hidden fresh processes, each with 29 checks and 600 concurrent factory
requests. Gameplay remains UNKNOWN; these are synthetic-host execution claims.
