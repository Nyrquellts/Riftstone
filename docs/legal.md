# Capcom's rights: Riftstone's rules, and where the code keeps them

Riftstone mods two games it does not own, Capcom's *Dragon's Dogma: Dark Arisen* and *Dragon's Dogma Online*.
These are the project's rules for staying on the right side of that, each with the code or check that
enforces it. They are the project's policy, written by its authors; they are not legal advice.

The short version: **no byte of either game leaves the user's PC through Riftstone**, the game's files stay
as installed, Capcom's names are used only to say which games Riftstone works with, and everything is free.

## 1. No game data in the repository, a release or a mod package

| Where | What keeps it out |
|---|---|
| The repository | `.gitignore` keeps `mods/`, `dist/`, `vendor/`, fuzz seeds and reports out. `tests/test_ip_audit.py` runs `ipaudit.scan_tree` on every tracked file in the unit tests (the gate): no file of either game (archive or resource magic, either executable by name or SHA-256), no Online archive key, no Capcom copyright notation, no third-party code, no client download links. The tables in `src/riftstone/data` hold engine identifiers, hashes and layouts measured from the games, not their content. |
| A release | `tools/make_release.py` audits every file before it writes the zip, and stops at a finding. Source is published as `make_release.py --source`: the tracked tree at `HEAD`, never the repository's history, which holds the unlicensed unit expander (`c91d5b6`, `2b99d61`) and an old copy of Online's key. `tools/ip_audit.py <zip or folder>` audits anything else. |
| A mod package | `riftstone package` (`package.py`) writes **deltas and recipes, never the game's files**: for each resource a mod changes or adds, copies from resources the player already has (the game's own resource of that name, other resources of that kind in the same archive, what a recipe makes) plus the bytes the author wrote (`delta.py`). Each player's Riftstone makes the mods from their own game (`package install`) and checks every base and every file by SHA-256. Every member is audited as the zip is written. |
| Content from the other game | Never carried as bytes. A Dragon's Dogma Online chimera skin, a `port` or `monster convert` records its **recipe** in the mod (`sources.py`, `riftstone-sources.json`); the package carries the recipe and the player's Riftstone runs it on the player's own copy of Online. A file known to hold the other game's content (recipe outputs, textures uploaded in Online's revision, skins whose source names Online) may differ from its recipe by at most a few author's bytes, else the package is refused. A texture that is mostly new is also compared with every texture of both games (`texprints.py`, cached): the game's own under another name becomes a base; the other game's is refused. |
| Processing by rule | A texture preset (`texfx`) applied to the game's own texture is a recipe too, so the recolour is made on each player's PC. |
| Studio | Mods, **Share (package)** makes a package. **Keep a copy (.zip)** is a private backup of the whole mod folder (it holds game data) and says so. |

Measured on the owner's own mods (2026-09-26): *DDO Chimeras + Gran Soren Horde* as the old package was
353 MB (nine whole game archives, 2,060 of the game's resources and the DDO-derived chimera textures); as a
package now it is 17.5 KB, of which 4,896 bytes are the authors' own (placement numbers and group entries),
and needs both games. Made again from this PC's games, all nine archives came out byte for byte the same as
the ones built from the original mods. The first attempt caught a real gap: the mod's `skins.json` names its
sources `Dragon's Dogma Online, em015202`, not the exact titles, so the three skins went unrecognised and 9.4
MB of Online's textures would have travelled; skins are now recognised by the Online enemy id, and the texture
fingerprints catch such a copy even with no record at all.

## 2. No key to Online's encryption

Online's archives (`ARCC`) are Blowfish-encrypted. Riftstone ships no key: `cipher.py` holds only its SHA-256
and its value on one zero block, and reads the key from the player's own client (the launcher
`ddo_launcher.exe` or the bundled server's `Arrowgene.Ddon.Client.dll` hold it; `DDO.exe` itself is
Themida-packed). `RIFTSTONE_DDO_KEY_FROM` points at any other file of the player's install; a key the player supplies in `RIFTSTONE_DDO_KEY` (or `%LOCALAPPDATA%\Riftstone\ddo.key`) is used first, and must match the same SHA-256. The tests use a
stand-in key and check that no source file holds the real one.

## 3. The game's files stay as installed

Dark Arisen mods are served by the loader (`dinput8.dll`, a proxy DLL) from `riftstone\overlay`: `install`
refuses without the loader and never writes into `nativePC`, removing the loader turns the mods off until it
is back, and mods an older Riftstone installed directly are restored and moved into the overlay
(`install.py`, `loader.py`, `tests/test_install_policy.py`). Riftstone never ships or writes a changed
`DDDA.exe` or `DDO.exe`: plugins patch the running game's memory, each after checking the bytes it expects
(a short signature, so it refuses a build it does not know).

Online is the one exception: its client is Themida-packed and the loader has not run in it yet, so Online
mods still replace client archives, with each original kept and checked (`riftstone\vanilla`), until the
overlay is proven there.

## 4. Interoperability

The engine addresses, byte signatures, structure layouts and format notes in `docs/` and the code are
interface specifications and memory offsets, documented so that independently written plugins and tools work
with the games. No game code is copied beyond those short signatures. Community tools kept under `vendor/`
were read as references and re-implemented; none of their code ships (`docs/vendor.md`,
`THIRD-PARTY-NOTICES.md`).

## 5. Dragon's Dogma Online's server

Riftstone contains no server code and no server data. The local server is the community's Arrowgene server
(AGPL-3.0), set up by the DDO toolkit (`<path>`); Riftstone edits its asset files on the user's PC, and a
package carries only deltas against the player's own copies of them. Neither Riftstone nor its docs host,
bundle or link Online's client or any Capcom server binary or database.

## 6. Names and marks

- The notice (`legal.py`, shown by `riftstone --version`, Studio's Guide, the README, the notices, every
  package's README): *Riftstone is an independent, open-source project. It is not affiliated with, endorsed by,
  sponsored by, or produced by Capcom Co., Ltd. "Dragon's Dogma", "Dragon's Dogma Online" and "Capcom" are
  trademarks or registered trademarks of Capcom Co., Ltd. All game assets remain the property of their
  respective owners.*
- No Capcom copyright notation anywhere (Capcom asks fans not to use it); `ipaudit.py` fails on it.
- Studio's mark (`studio/mark.svg`, `spiral.svg`) and its display font (Riftstone Blade, OFL) are original;
  no Capcom logo, title card or font is used. The game's pictures appear only as previews made from the user's
  own files.
- The games are named only to say which games Riftstone works with ("for Dragon's Dogma: Dark Arisen and
  Dragon's Dogma Online", DDDA, DDO); Riftstone and its packages never take a game's name as their own.

## 7. Free

Riftstone is MIT-licensed and free. It is never sold; no paid builds, early access or paywalled features;
nothing made with it may be sold or put behind a paywall. Every package's README says so.

## 8. Content

Riftstone and its examples ship no adult or offensive content.

## 9. Support

A crash, fatal-error or hang report from the loader starts with who it is for: the mods' authors and
Riftstone, not Capcom's support, which does not cover modded games (`native/loader/stability.cpp`). The
fatal-error box, `riftstone crash` and Studio's Diagnostics say the same (`legal.SUPPORT`). A report cannot tell
by itself whether a mod or the game is at fault; safe mode, or the game without mods, shows that.
