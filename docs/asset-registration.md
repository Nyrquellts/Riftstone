# Offline asset registration and clone synchronization

This lane contains a working offline item/parameter registrar and a native clone
synchronizer tested through a real MinHook factory interception in a synthetic
host. **It does not fix DDDA's cutscene clone in the game or register new native
enemy archetypes.** Those two operations have no verified executable profile.

## What the local game actually contains

`tools/registration_audit.py --out reports/registration-audit.json` reads the
installed executable and existing resource index without starting the game.
For the measured build (`19facc2642f79a09d14ce55eace5f228161f2f8bab60ec67eb3c6196cc6277b8`):

- Item records are `etc\\item\\itemList.itl`: `ITL2`, 1,901 fixed 128-byte records,
  13-bit embedded IDs matching record indices. The measured holder is `rom/bbs_rpg`.
- Names and descriptions are `id\\message\\item\\itemName_<lang>.gmd` and
  `itemInfo_<lang>.gmd`, in seven languages; both `rom/bbs_rpg` and `rom/bbsrpg_core`
  hold them. The registrar changes every supplied matching copy together.
- Enemy stats are PRP/XFS resources under `charparam\\em`, not an observed global
  `enemy_param.bin`. The audit found 733 indexed archive entries under that prefix.
- The indexed resource names contain no `item_parameter`, `enemy_param`, or
  `quest_data` matches. Exact NUL-terminated `mEventActor` and `uCharacterCutscene`
  strings are absent from this executable. This is an inventory finding, not proof
  that the underlying behavior or unnamed structures do not exist.

The existing [native RE notes](re-native.md) and [animation codec notes](animation.md)
remain the sources for this repository's measured engine behavior. Public primary
implementations include [ArisenTools](https://github.com/mhvuze/ArisenTools) and
[RevilLib](https://github.com/PredatorCZ/RevilLib), which document archive/motion
tooling. They provide no verified clone-ownership ABI for this implementation.
No third-party format code was copied into this lane.

The available PS3 build provides a stronger research lead: `uActorModelPl`.
It describes player/edit resources, equipment, skin-joint index maps,
bake-model state and a finish-build flag. Direct calls in the face/body/human edit
setters and `setPlInfo` include `cResource::release` and `cResource::addRef`.
The measured PC executable contains the same actor-model class names, but the
PS3 layout and code addresses are not PC offsets. See the bounded
[clone research audit](cutscene-clone-research.md) and its rerunnable command.
This evidence narrows the research target while keeping the game hook unavailable.

## Standalone archive output without a DLL

```
Riftstone.cmd register --plan registration.json --source copied-originals
Riftstone.cmd register --plan registration.json --source copied-originals --out new-package
```

The first command validates and builds in memory. The second creates a new package
directory containing `nativePC/rom/...arc` files and `registration.json`. It never
installs the output, edits an input archive, or writes under a game installation.
Input archive names are relative to `--source`; each is pinned by its actual
SHA-256 in the request. Use original archive copies, including every holder of a
shared resource that should be updated. An omitted holder is outside the request's
coverage and is stated as such in the report.

Request schema: `riftstone.registration/1`.

| Field | Content |
|---|---|
| `archives` | List of `{name: "rom/bbs_rpg", sha256: actual_hash}` records |
| `items` | List of `{key, template, name, description?, id?, buy?, sell?, weight?, translations?}` |
| `param_copies` | List of `{from_archive, from_resource, to_archive, to_resource, yaml?}` |
| `enemy_archetypes` | Must be absent or empty; a new native enemy class is unavailable |

`template` is an existing item ID. The allocator chooses the smallest remaining
English `Unknown Item` slot whose buy and sell prices are zero, or checks the
requested `id`. IDs are reserved for the whole batch. Existing records are cloned
with unnamed bits preserved; only the ID and requested fields change. Names cannot
retain the unused-slot placeholder. Seven language tables must be present and
cover the chosen slots. `translations` maps language suffixes (`eng`, `jpn`, `fre`,
`spa`, `ger`, `ita`, `zht`) to optional `name`/`description` overrides. Unspecified
translations use the request's default text. Missing/conflicting resource copies,
occupied IDs, duplicate request fields, unknown fields, and invalid values refuse
the entire build. This does not prove all equipment-specific tables recognize a
new item; template behavior and gameplay remain UNKNOWN.

`param_copies` creates a new named **PRP resource variant** inside an explicitly
selected archive. Resource identifiers use Riftstone's `fsmap` spelling, such as
`charparam/em/em0100_cmn.prp`. Optional `yaml` uses the existing PRP/XFS codec and
must preserve the source root class. The new name must not already exist. This
does not assign a new native enemy ID, register a class, or alter spawn lookup code.
For each changed directory, supplied ARCS references are rebuilt using the real
ordered `(JAMCRC(resource name), type ID)` layout. References outside the pinned
archive set remain outside the claim. ARC offsets and compressed lengths are
rebuilt by the existing ARC codec, and every decoded output resource is hash checked.

Output appears only after all archive builds and source/hash checks succeed.
Writes use a new sibling staging directory followed by rename; a filesystem
failure may leave that private staging directory for diagnosis. Hostile concurrent
filesystem replacement is outside the contract. No multi-file live installation
transaction is attempted.

## Validation and limits

`python tools/registration_verify.py --out reports/registration-vanilla.json`
checks an item registration on the original archives in memory. The measured run
covered both indexed item holders and preserved 608 unrelated entries including
their compressed bytes, with original archive hashes unchanged. No generated game
archives or extracted game bytes are committed or distributed as test fixtures.

`tests/test_registration.py` covers allocation, localization, duplicate-resource
consistency, PRP overrides, ARCS references, transactional failure, output isolation,
and size limits. The `registration` fuzz target checks unique bounded IDs,
deterministic builds and immutable input bytes. Run it for 120 seconds as required
by the repository gate.

See [the native clone contract](../native/clone_sync/README.md) for the tested
ownership and synchronization API. LMT motion parsing alone does not identify an
actor-instantiation hook, resource reference-count ABI, or render-thread barrier.
Those must be measured before the DDDA game profile can become available.
