# Riftstone: legal notices

## Not affiliated with Capcom

Riftstone is an independent, open-source project. It is not affiliated with, endorsed by, sponsored by,
or produced by Capcom Co., Ltd. "Dragon's Dogma", "Dragon's Dogma Online" and "Capcom" are trademarks or
registered trademarks of Capcom Co., Ltd. All game assets remain the property of their respective owners.
The games' names are used only to say which games Riftstone works with; Riftstone's name, marks, drawings
and fonts are its own.

## No game data

Riftstone ships no game data: no archives, models, textures, text, sound or executables from either
game. It reads and rewrites the files of a copy of the game its user owns, on that user's computer, and
never bypasses copy protection. The tables in `src/riftstone/data` hold engine identifiers, hashes and
file layouts measured from the games, not their content.

A mod package made with `riftstone package` contains archives rebuilt on the maker's PC from their own
game: they carry the game's own data with the mod's changes. Such a package is for players who own the
game, and whoever shares it is responsible for doing so within the law and the game's terms. The
package's README says so.

## Riftstone itself

Riftstone's own code -- the Python toolchain, Studio, the native loader (`native/loader`) and Riftstone's
own plugins (`native/plugins/enemy_cap`, `enemy_skins`, `lod_tuner`, `inclination_lock`, `free_sprint`,
`save_backup`, `draw_distance`, `six_skill_warrior`)
-- is released under the MIT License (`LICENSE`), Copyright (c) 2026 NryQ.

## Fonts

Studio's display face, **Riftstone Blade** (`src/riftstone/studio/fonts/riftstone-blade.woff2`, a
subset), is licensed under the **SIL Open Font License, Version 1.1** (no Reserved Font Name). Its Latin
letters are original Riftstone designs; its Japanese characters are carved from Noto Serif JP and a few
symbols come from Noto Serif and Noto Sans Math.

- Copyright 2012 Google Inc. All Rights Reserved.
- Copyright 2022 The Noto Project Authors.
- Riftstone Blade modifications and original Latin designs Copyright 2026 NryQ.

The licence texts travel with the font: `src/riftstone/studio/fonts/NotoSerifJP-OFL.txt`,
`NotoSerif-OFL.txt`, `NotoSansMath-OFL.txt` and `README.txt`. The OFL allows the font to be used,
studied, modified and redistributed, bundled with software, but not sold by itself.

## Community work Riftstone learned from

Riftstone re-implements file layouts and engine addresses in its own code and proves each one on the
game files; it does not ship other projects' code. It owes a lot to the Dragon's Dogma modding
community, whose tools were read as references (`docs/vendor.md` records each one, its licence and what
Riftstone took from it): Chris Purnell's dd-tools and ddda-dinput8, ArisenTools, albam_reloaded, the
umvc3 templates, RevilLib, Gibbed.MT, pawn-knowledge, the ddda-save-editor, DDDAFix (MIT), the
Arrowgene Dragon's Dogma Online server (AGPL-3.0, read for Online's data structures; not bundled) and
Dune.Emulator (MIT). Those sources stay on the maintainer's disk under `vendor/` and are not part of
Riftstone.

### Not part of Riftstone

- **The GPL unit-limit expander** (Ando's "unit_expander", which raises a stage group's three enemy
  kinds; "GPL" is the game's group-list format, not a licence): third-party source supplied without a
  licence. It is kept only as a local reference under
  `vendor/unit_expander` (not in the repository) and is never built into a release or a mod package.
  Riftstone's own encounters use one enemy kind per group and do not need it.
- **safetyhook** (Boost Software License 1.0) and **Zydis** (MIT) are fetched at build time by the
  optional Ninput host (`native/ninput`) only; neither is part of the loader, of any plugin Riftstone
  installs, of a release or of a mod package. Anyone who distributes a Ninput build must include Zydis's
  MIT notice with it.
