# Read-only overlay snapshots and semantic merge

`riftstone vfs` prepares an in-memory snapshot and reports its content hashes,
source layers and field conflicts. It never installs into the game, changes an
original archive or claims that the command has mounted a running process.

Create `vfs.json` in a working folder. Paths may be absolute or relative to that
folder; `base` is the directory containing the original filesystem files to view,
not a resource name. Each layer uses the same relative filesystem layout:

```json
{
  "schema": "riftstone.vfs/1",
  "base": "base",
  "semantic_merge": true,
  "layers": [
    {"name": "balance", "priority": 10, "path": "mods/balance"},
    {"name": "encounters", "priority": 20, "path": "mods/encounters"}
  ]
}
```

```
riftstone vfs --mount C:\work\view
riftstone vfs --mount C:\work\view --read rom\enemy\em0100.arc --out C:\work\merged.arc
riftstone vfs --mount C:\work\view --bundle C:\work\snapshot.rsv
```

Outputs must be new files outside every source directory. Omitting output options
writes only the JSON report to stdout. A snapshot's `open()` returns an independent
seekable in-memory stream; existing streams keep their bytes for their lifetime.
`list(directory)` returns the union of base and overlay names. Case aliases,
traversal, NTFS streams, reserved Windows device names, file/directory collisions
and escaped filesystem links are refused. Sources must remain quiescent while
read; this is not protection against a concurrent actor changing filesystem links.

Layers are ordered by `(priority, name)` ascending. Names are unique; higher
priority, then the lexicographically later name, wins a conflicting edit. Every
variant is compared with the same base, so an unchanged higher-priority field
cannot erase a lower-priority edit. Independent dictionary fields merge. Arrays
are atomic because index equality does not establish entity identity. Deletion
against a nested edit conflicts at their common ancestor. Identical changes are
idempotent. JSON numeric types remain distinct from Boolean values, and floating
signed-zero changes are preserved. Conflicts include the path, base, competing
edits and selected layer. This policy is deterministic but is **not a CRDT** and
is not claimed to be commutative for conflicting priorities.

Supported semantic adapters:

- JSON objects and nested dictionary fields, with strict duplicate-key and finite
  number validation when structural merging is needed.
- Editable Riftstone resources, converted with the existing `params`/`yamlish`
  parsers and rebuilt through their existing schema checks. Sequences remain
  atomic. Unchanged inputs keep their original binary bytes.
- ARC/ARCC archives, matching entries by the exact engine name and type ID,
  preserving unchanged stored payloads and merging editable conflicting resources.
  Existing resource load-order rules are reused, and rebuilt entries are reparsed
  and compared before their bytes are accepted.
- Opaque binary files and source text, including raw `.nyr`, use whole-file
  priority with an explicit `opaque-file-priority` conflict. No engine structure
  or NYR source AST is guessed from arbitrary bytes.

Missing a file in a filesystem layer means “no override,” not a deletion. Within
complete archive variants, an absent base resource is a deletion and participates
in three-way conflict handling. Zero-edit archives remain byte-exact.

The native bridge is `RSV1`: four magic bytes, little-endian uint32 file count,
then for each sorted path a uint32 UTF-8 path length, uint32 content length, path
bytes and content bytes. There are no implicit pointers or filesystem writes.
`RiftstoneRuntime_LoadBundle` validates the entire input and swaps the in-memory
view only on success; malformed/truncated input leaves the previous view intact.
The synthetic host test takes an actual Python field merge through this bundle
and reads it using real intercepted CreateFileW/ReadFile calls. Game startup
integration is a separate explicitly initialized adapter, never CLI injection.

Limits: 256 layers, 100,000 files, 256 MiB per file, 1 GiB per loaded layer and per
resulting snapshot, and 128 levels/1,000,000 nodes per merge tree. These limits do
not expand the native process's address space. Tests: `python -m unittest
test_vfs` from `tests`; invariant fuzz target `vfs` includes identity, input
immutability and unchanged-layer preservation.
