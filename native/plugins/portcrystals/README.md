# portcrystals

Riftstone's own loader plugin: more Portcrystals placed at once. Dragon's Dogma: Dark Arisen lets you place 10.
With this plugin the limit is 15 by default, or anything from 10 to 32 in `portcrystals.ini`:

```ini
[portcrystals]
slots = 15
```

The ten are built into several places in the game: its list of placed crystals, a count and a clear, the
Ferrystone's destinations, the stage load that puts crystals in the world, the map's icons, and the save. So the
plugin moves each of those somewhere bigger rather than changing one number. `docs/re-portcrystals.md` describes
all of it.

**Your save stays a normal save.** It keeps the first ten crystals exactly as the game writes them. The crystals
past ten go to `riftstone\portcrystals.bin` when the game saves. Each record is keyed by the ten slots that save
holds, so reloading an older save or switching accounts brings back the right ones. Without the plugin, or with a
smaller `slots`, the crystals past the limit are not in the game. The next save then forgets them, and with them the
Portcrystal items they stand for. **Pick them up first** if you lower `slots` or remove the plugin.

DDDA.exe build 2364871 only. Before patching, the plugin compares byte for byte all 46 patched instructions, the
four replaced runs and the three hook sites. The game's `sGameSys` must not exist yet, because it is built at the
enlarged size. If either check fails, nothing is patched and `riftstone\logs\portcrystals.log` says why. The same
log records each save and load of the crystals past ten.

```bat
native\plugins\portcrystals\build.cmd
riftstone loader plugin add native\plugins\portcrystals\out\portcrystals.asi
```

`riftstone loader plugin add` also copies `portcrystals.ini` the first time; edit the copy in
`<game>\riftstone\plugins\`. Remove with `riftstone loader plugin remove portcrystals.asi`, after picking up the
crystals past ten.
