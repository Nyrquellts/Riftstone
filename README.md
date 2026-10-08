# NryQ // Riftstone

[![Discord — Bug Reports & Community](https://img.shields.io/badge/Discord-Bug%20Reports%20%26%20Community-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/xSY8kyECKt) [![Status: Alpha](https://img.shields.io/badge/status-ALPHA-00f0ff?style=for-the-badge)](#) [![License: MIT](https://img.shields.io/badge/License-MIT-3fb950?style=for-the-badge)](LICENSE)

**🐞 Found a bug?** Report it on the [Discord](https://discord.gg/xSY8kyECKt).

**A modding & modernization suite for Dragon's Dogma: Dark Arisen** (and a preservation toolkit for the
shut-down Dragon's Dogma Online). Free, open-source (MIT), and it ships **zero Capcom assets** — it only
reads and rewrites files in the copy of the game *you own*, on your own machine.

> ⚠️ **Alpha — early, in-progress release.** The modding toolkit is byte-for-byte proven on the whole
> game, but the in-game plugins are still being playtested, so expect bugs and rough edges. It is
> **non-destructive and fully reversible** — it never modifies your game files (mods load from an
> overlay; delete three items and the game is exactly as it was), so it's safe to try.
>
> 🐞 **Report bugs & get help on Discord:** https://discord.gg/xSY8kyECKt

---

## What it does

**1. Modernizes Dark Arisen** — a drop-in loader (`dinput8.dll`) plus native plugins that lift old engine
limits. **The loader and plugins never modify your game files** — your mods load from an overlay folder,
and `nativePC` is left untouched: since 1.0.1 Dark Arisen mods install only through that overlay.
Since 1.0.3 the player package ships every gameplay feature below **switched off** (the crash guards and the
save backup are on); `Riftstone - Start Here.cmd` turns each one on when you want it.

- **Bigger battles** — the engine's hard 10-enemy limit becomes **30 by default, up to 64**.
- **Pawn inclination lock** — Come! / Help! / Go! stop dragging your main pawn toward Guardian.
- **Free out-of-combat sprint** — no stamina drain while you're not in a fight.
- **Less pop-in**, **longer draw distance**, a **six-skill Warrior**, and automatic **save backups**.
- **Missing-texture guard** — a missing texture draws a neutral placeholder and is logged instead of
  crashing to desktop (catches a common crash, not all of them).
- **Archive guard** (1.0.1) — when the game asks for a file before its archive has been read (a skipped
  cutscene, a slow drive), it gets that file's own bytes from the archive instead of stopping with
  "Failed open file".
- **Diagnostics panel** (press **Insert**) — active enemies, frame rate, memory headroom, plugin status.
  For about 12 seconds after the game starts, a small **RUNNING** notice in the top-right corner shows that
  Riftstone is loaded.
- **DXVK integration** — chains DXVK's `d3d9.dll` to relieve the 32-bit game's address-space pressure.
- **Collision crash guard** (1.0.4, on) — a frame with more hit shapes than the game's table of 800 (a city
  full of Archydras) made the game read past its table and crash; the table now holds 4,096 and the read stops
  at its end.
- **Any enemy in any stage** (1.0.4) — the game loads enemy models per stage, so an Archydra placed in the
  Tower never appeared. `stage_enemies` makes a stage also load the enemies it is given (up to 16 a stage: an
  Archydra, Chimeras and Drakes together, say), and installing a mod writes those lines for it.
- **Bigger memory pools** (1.0.4, off) — the game's fixed pools for enemies, physics, collision and effects
  made larger before the game builds them, and every request a full pool refuses logged.

> **Honesty:** every plugin is verified in a test harness against the *real* game code; their *in-game
> feel* is still being playtested. The file-format toolkit below, by contrast, is byte-for-byte proven
> across the entire game. The start-up notice (1.0.3) has been drawn in the actual game (`loader.log`,
> 2026-10-07). Of 1.0.4's: the collision guard has patched the game in play (its log); an Archydra, Chimeras
> and Drakes loading together in a stage that never had them are proven in the test harness, not yet seen in
> game.

**2. A full modding toolkit (Studio)** — a local browser app (sends nothing anywhere), or the CLI:

- Unpack/repack any archive **byte-for-byte** (verified on all 8,536), and edit parameters, all 7
  languages of text, and every texture (11,221) as plain files.
- Place enemies, encounters, scripted waves, and **whole procedurally-generated dungeons** on a stage's
  real walkable ground (its navigation mesh, decoded from the exe), with every spawn point checked
  reachable on foot.
- Set out enemies you pasted on one spot in a shape (scatter, ring, camp, line, wedge, flank) on that
  ground, each facing the way the shape says, as far apart as the game spaces them (1.0.4). Or export mods
  as files to copy over the game's own, for your own game (to share a mod, make a package).
- Add custom items (shops, recipes, drops); rebalance any enemy.
- Port resources and body-matched monsters between Dark Arisen and DDO.
- Share mods as **packages with no game data**: deltas and recipes that each player's Riftstone turns back
  into the mod from their own game. Mods that change the same group lists or layouts **merge** instead of
  hiding each other. Installing someone else's mod also loads the enemies it places in stages that never had
  them (1.0.4).

**3. Dragon's Dogma Online preservation** (if you own DDO) — solo rebalance, a monster bridge, and gear
dyes, all generated from *your own* DDO files. See [docs/ddo-solo.md](docs/ddo-solo.md).

---

## Requirements

- **Dragon's Dogma: Dark Arisen** (Steam, PC) — a legit copy. Please support the devs.
- **Windows 10/11 (64-bit)** and the **Visual C++ 2015–2022 Redistributable (x86)**.
- **Python 3.11+** — only for the toolkit/Studio; the player package needs no Python.

## Install (players)

Download the player package and unzip everything into your game folder (where `DDDA.exe` is). Then
double-click **`Riftstone - Start Here.cmd`** (it needs no Python): **1** checks the install and says what
to fix, **2** turns features on, one at a time (they start off; the save backup and the collision crash guard
are on). Start the
game: for about 12 seconds a small **RUNNING** notice shows in the top-right corner, and **Insert** opens
the diagnostics panel. To uninstall, delete `dinput8.dll`, `riftstone_loader.ini`, the `riftstone` folder
and the Start Here file. Want one feature only? Each is also a zip of its own on the release page
(`Riftstone-Plugin-<name>-<version>.zip`, the loader and that feature): unzip it the same way, on its own or
over the player package. `optional\ninput` holds Ninput, an experimental plugin host: it stays off unless you
copy its `xinput1_3.dll` next to `DDDA.exe` (its README says more).

## Use the toolkit

```
Riftstone.cmd             # a double-click works too: a menu (Studio, check my setup, features, logs)
Riftstone.cmd studio      # the browser app
Riftstone.cmd doctor      # check your setup
Riftstone.cmd plugins     # turn Riftstone's features on and off
```

New here? Start with **[docs/tutorial.md](docs/tutorial.md)**. Format details are in
[docs/formats.md](docs/formats.md); the loader/runtime in [docs/runtime.md](docs/runtime.md).

## Build from source

```
native\loader\build.cmd                 # the loader (needs Visual Studio C++ tools, x86)
native\plugins\<name>\build.cmd         # each plugin
tools\test_all.cmd                      # the test gate (unit tests + corpus proofs)
```

---

## Is it safe?

- **Open-source (MIT)** — read every line. Studio runs on your PC only and sends nothing anywhere.
- **No piracy / no game rips** — Riftstone contains zero Capcom assets. It reads and rewrites files in
  the copy of the game you own.
- **Single-player** — nothing to get banned from.

## Legal

Riftstone is an independent, open-source project. It is not affiliated with, endorsed by, sponsored by, or
produced by Capcom Co., Ltd. "Dragon's Dogma", "Dragon's Dogma Online" and "Capcom" are trademarks or
registered trademarks of Capcom Co., Ltd. All game assets remain the property of their respective owners.
Riftstone's own code is released under the **MIT License** ([LICENSE](LICENSE)); see
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

## Credits

Designed and written by **NryQ**. Special thanks and heavy kudos to **LDKSuperDante (Austin Shelton)**
— author of **Dragon's Dogma Remastered** and **Project: Dragonforged** — for foundational research on
Grigori combat state machines, shadow-table architecture, and enemy-wave concepts that helped inform
Riftstone's encounter systems. Support his work and watch for his releases:
[Patreon](https://www.patreon.com/DragonsDogmaRemastered) ·
[Nexus](https://www.nexusmods.com/dragonsdogma/users/23347084) ·
[GitHub](https://github.com/LDKSuperDante) · [Discord](https://discord.gg/wUAq2mbcyK) ·
[YouTube](https://www.youtube.com/@austinshelton8438). Thanks also to everyone whose open tools and
research this learned from, and to the Dragon's Dogma modding community.

**Bug reports & community:** [Discord](https://discord.gg/xSY8kyECKt)  ·  **Support:** [Ko-fi](https://ko-fi.com/nryquellts) · [Patreon](https://patreon.com/c/NryQuellts)
