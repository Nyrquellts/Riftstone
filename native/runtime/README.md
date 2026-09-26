# Explicit allocator and snapshot runtime

This C++20 PE32 DLL is a separately initialized runtime with a synthetic Windows
host. It is **not the installed Riftstone loader**, and is not deployed by these
commands. Existing `native/loader` overlay support and the measured `enemy_cap`
plugin remain the production adapters. All game behavior of this new runtime is
UNKNOWN; no game was launched or injected into by its test runner.

## Build and verify

Use installed MSVC x86. Dependencies are explicit source paths, never downloaded
by a build. `dependencies.json` records official source URLs, versions and ZIP
SHA-256 values. MinHook 1.3.4 and mimalloc 2.1.9 are the tested versions.

```
python native/runtime/build.py --minhook vendor/runtime/minhook-1.3.4 --mimalloc vendor/runtime/mimalloc-2.1.9
python native/runtime/test/run_tests.py
```

Outputs are local `native/runtime/out/`: DLL, native host, compiler log, build
capabilities, test results, preserved synthetic base folders and allocator logs.
The DLL uses the static CRT; the host uses UCRT to exercise its real allocation
entry points. Missing optional dependencies produce UNAVAILABLE, never a fake
successful interception test. The test runner requires MinHook and runs both
allocator backends when mimalloc was built. Source APIs are in `include/runtime.h`.

## Allocation and lifecycle contract

Call Initialize, load the snapshot, and EnableHooks explicitly after LoadLibrary
returns, before application workers start. DllMain only disables thread callbacks.
EnableHooks accepts the current executable's PE32 module and redirects new
allocations/opens only for direct calls originating in that module. No executable
name or engine address is guessed. Dependencies and other modules keep their
original allocation paths. Trampolines are installed as a MinHook queued batch;
preparation failures restore original function pointers and remove prepared hooks.

Allocations use an isolated Win32 heap or a mimalloc heap in an exclusive 64 MiB
arena. Mimalloc's arena remains reserved until process exit, uses its own sharded
free lists, and **consumes the same finite x86 address space**. This implementation
requires allocations on the initializing thread for that mimalloc heap; other
threads receive allocation failure. Win32 backend allocation is serialized and
supports multiple threads. Cross-thread frees are allowed. There is no unsupported
claim of transparent multithreaded MT Framework heap replacement or measured
performance improvement.

Owned pointers are tracked separately from foreign pointers, with allocation
family, original heap and size. Failed realloc keeps the old allocation. A wrong
heap/family never receives another backend's pointer. Direct exported free rejects
foreign or already-released pointers. HeapAlloc handles flags 0 and ZERO_MEMORY;
other allocation flags use the original API. For owned blocks, unsupported realloc
flags (including IN_PLACE_ONLY) fail without freeing the block. HeapSize and
HeapDestroy are paired with ownership; a heap cannot be destroyed while redirected
allocations exist. This selected-heap contract does not cover HeapWalk,
HeapCompact, HeapValidate, low-fragmentation settings or unknown engine allocator
ABIs. Do not opt a heap using those operations into this runtime.

UCRT malloc/free/realloc/_msize hooks are installed together when that CRT is
already loaded. Unsupported CRTs are not discovered or patched. calloc and
unselected allocations stay original and their corresponding frees pass through.
VirtualAlloc and VirtualFree are intercepted for coverage but preserve the exact
original reservation/commit/decommit/protection behavior; malloc cannot replace
these APIs. Checked arithmetic and the configured pointer ceiling reject pointer
wrap and signed-address violations. **A 32-bit pointer cannot address beyond 4 GiB.
There is no high-address remapping or promise to exceed that ceiling.**

DisableHooks refuses while owned allocations or virtual handles exist. Resource
creation rechecks lifecycle state under the same mutex, preventing an in-flight
open/allocation from appearing after disable. Shutdown requires disabled hooks,
zero owned resources and (for mimalloc) the initializing thread. Code is pinned
and disabled trampolines retained through process exit so an already-entered hook
never resumes in freed code. This is a one-hook-installation lifecycle per process.
It intentionally does not promise DLL unload/reload after hooks were installed.
A primary Win32 TLS slot is reserved before hooks activate and retained until
process exit; initialization refuses if only expansion slots remain. The guard
preserves LastError and is safe during new-thread loader allocation, before
compiler static TLS exists. The host exercises new threads and concurrent disable.
Logs must resolve outside the base directory; both existing targets and parent
directories are checked through final Win32 handle paths. Existing logs with more
than one hard link are refused. A hostile concurrent
filesystem actor changing links after initialization is outside the contract.

## Read-only in-memory files

AddFile loads copied immutable bytes with explicit integer priority; equal-priority
duplicates and file/directory collisions are refused. LoadBundle transactionally
accepts deterministic `RSV1` snapshots from `riftstone vfs --bundle`; all path,
UTF-8, byte-count, duplicate and collision checks precede installation. A failed
bundle leaves the previous snapshot intact. Hooks freeze the snapshot.

Implemented adapters: CreateFileW/A, ReadFile, CloseHandle, GetFileAttributesW,
FindFirstFileW/FindNextFileW/FindClose, GetFileSize/Ex, SetFilePointer/Ex. Reads are
synchronous, support independent positions, short reads/EOF and Win32 share-read
checks. Base files are read through unchanged; ordinary write opens under the
configured base are denied. Enumeration unions base and virtual entries with
case-insensitive priority, deduplicates names and supports `*`, `*.*`, or a literal
name. Other wildcard expressions are explicitly NOT_SUPPORTED, rather than silently
returning an incomplete view. Virtual file timestamps are zero, attributes readonly.

WriteFile/FlushFileBuffers reject virtual writes. CreateFileMappingW and overlapped
virtual opens/reads explicitly return NOT_SUPPORTED. DuplicateHandle refuses
tracked streams/enumerations, including CLOSE_SOURCE, preserving ownership.
The adapter does not implement NT native calls, alternate file APIs, hard links,
memory mapping, asynchronous I/O, network files, virtual security descriptors,
all DOS wildcard quirks or full filesystem virtualization. Unsupported virtual
operations must not be used by a host. Real event handles are private tokens;
callers must close them only through the supported hooked APIs. Changes by other
programs or unhooked native syscalls are outside this process-local contract.

## Checked expansion support, not invented engine profiles

`include/checked.hpp` supplies overflow-safe size/address checks, unique masked
signature matching (ambiguous/all-wildcard patterns fail), all-or-nothing expected
byte patching of an explicitly writable buffer, checked tail-expansion plans and a
generation-tagged handle arena. The arena supports a synthetic 256-slot pool and
six-slot staging, survives growth without exposing stale pointers, and rejects
reused handles. No game pointer ABI is transparently converted into these handles.
The caller must quiesce readers before applying a patch plan; no OS page protections
or engine pages are modified by this helper.

DDDA's measured enemy pool is 10 slots, **not 32**. The existing enemy_cap plugin
already moves it to checked tail storage for 10..64 slots with 164 exact sites and
four replaced runs; see `docs/re-enemy-cap.md`. This work does not replace that
verified patch set, raise it to 256, invent a Warrior vocation/UI/input profile,
or guess the >1,901-icon cache layout. GameProfile returns UNAVAILABLE for these
unmeasured integrations. Those requested gameplay changes need measured executable
profiles and authorized in-game verification before they can be claimed.
