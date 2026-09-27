# NryQ // Riftstone

**A modding & modernization suite for Dragon's Dogma: Dark Arisen** (and a preservation toolkit for the
shut-down Dragon's Dogma Online). Free, open-source (MIT), and it ships **zero Capcom assets** — it only
reads and rewrites files in the copy of the game *you own*, on your own machine.

---

## What it does

**1. Modernizes Dark Arisen** — a drop-in loader (`dinput8.dll`) plus native plugins that lift old engine
limits. **The loader and plugins never modify your game files** — your mods load from an overlay folder,
and `nativePC` is left untouched. (The toolkit can *also* build conventional archive mods on request; that
path writes into the game, but backs up and restores the originals.)

- **Bigger battles** — the engine's hard 10-enemy limit becomes **30 by default, up to 64**.
- **Pawn inclination lock** — Come! / Help! / Go! stop dragging your main pawn toward Guardian.
- **Free out-of-combat sprint** — no stamina drain while you're not in a fight.
- **Less pop-in**, **longer draw distance**, a **six-skill Warrior**, and automatic **save backups**.
- **Missing-texture guard** — a missing texture draws a neutral placeholder and is logged instead of
  crashing to desktop (catches a common crash, not all of them).
- **F10 diagnostics panel** — active enemies, frame rate, memory headroom, plugin status.
- **DXVK integration** — chains DXVK's `d3d9.dll` to relieve the 32-bit game's address-space pressure.

> **Honesty:** every plugin is verified in a test harness against the *real* game code; their *in-game
> feel* is still being playtested. The file-format toolkit below, by contrast, is byte-for-byte proven
> across the entire game.

**2. A full modding toolkit (Studio)** — a local browser app (sends nothing anywhere), or the CLI:

- Unpack/repack any archive **byte-for-byte** (verified on all 8,536), and edit parameters, all 7
  languages of text, and every texture (11,221) as plain files.
- Place enemies, encounters, scripted waves, and **whole procedurally-generated dungeons** on a stage's
  real walkable ground (its navigation mesh, decoded from the exe), with every spawn point checked
  reachable on foot.
- Add custom items (shops, recipes, drops); rebalance any enemy.
- Port resources and body-matched monsters between Dark Arisen and DDO.

**3. Dragon's Dogma Online preservation** (if you own DDO) — solo rebalance, a monster bridge, and gear
dyes, all generated from *your own* DDO files. See [docs/ddo-solo.md](docs/ddo-solo.md).

---

## Requirements

- **Dragon's Dogma: Dark Arisen** (Steam, PC) — a legit copy. Please support the devs.
- **Windows 10/11 (64-bit)** and the **Visual C++ 2015–2022 Redistributable (x86)**.
- **Python 3.11+** — only for the toolkit/Studio; the player package needs no Python.

## Install (players)

Download the player package, unzip it into your game folder (where `DDDA.exe` is), and launch. Press
**F10** in-game for the diagnostics panel. To uninstall, delete `dinput8.dll`, `riftstone_loader.ini` and
the `riftstone` folder.

## Use the toolkit

```
Riftstone.cmd studio      # the browser app
Riftstone.cmd doctor      # check your setup
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

**Support:** [Ko-fi](https://ko-fi.com/nryquellts) · [Patreon](https://patreon.com/c/NryQuellts)
