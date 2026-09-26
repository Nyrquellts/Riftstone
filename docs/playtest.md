# In-game checklist (`riftstone playtest`)

Most of Riftstone is proven without the game: harnesses run the game's own code and the corpus proofs
cover every file. What only a play session can show is listed here. Play once with these steps, close
the game, then run:

```bat
Riftstone.cmd playtest
```

It reads what the session left in `<game>\riftstone` and grades each item:

- **OK:** this session showed it working.
- **FAILED:** it showed it failing, with the log line that says so.
- **not exercised:** the session never tested it, and the command says what to do next time.

It launches nothing and changes nothing. Useful options:

- `--previous` checks the session before the last one.
- `--json` gives the same result as data.

## Before you start

1. Close the game.
2. Install the current loader with `Riftstone.cmd loader install`. It keeps your `riftstone_loader.ini`
   values. `Riftstone.cmd doctor` says which version is installed and which one this Riftstone has.
3. Switch on the plugins you want tested, in Studio's Mods tab or with
   `Riftstone.cmd loader plugin add free_sprint`.

## Dark Arisen

| Item | What to do in the game | What `playtest` checks |
|---|---|---|
| Loader and hooks | Start the game. | `loader.log` names the version and the build. Every hook is installed, including the ones put back after the DRM wrapper reset them. |
| Plugins | Nothing extra. | Each plugin loaded, and its own log says it patched, or why it refused. |
| More enemies at once (`enemy_cap`) | Go where many enemies spawn, for example the Gran Soren horde mod (stage 100, cell 56m52n). | `enemy_cap.log`: the most enemies at once and how long the pool was full. OK when more than 10 were active. |
| F10 panel under load | With 20 or more enemies active, press **F10**. Keep it open for a few seconds, then close it. Open it again somewhere quiet. | Each opening is noted in `loader.log` (loader 0.3.3). The panel's enemy count and slots are compared with `enemy_cap`'s own record at the same second. Frame rate, address space and stage are shown. |
| Missing-texture guard | Run `Riftstone.cmd playtest guard-mod --mod "Texture guard test"`, then `Riftstone.cmd install "Texture guard test"`. Find goblins: their skin draws grey. Afterwards run `Riftstone.cmd uninstall "Texture guard test"`. | `loader.log` names the texture that was missing, and the exit summary counts the stand-ins. The game kept running. |
| Free sprint (`free_sprint`) | Sprint across a field: the stamina bar holds. Sprint during a fight: it drains as usual. | `free_sprint.log` says once when a sprint went free and once when one was charged in battle. |
| Memory | Play a heavy scene for a while. | The exit summary's verdict: headroom, tight or bound (loader 0.3.2 and later). |
| How it ended | Close the game however you like. | The loader names what closed it. If a program closed it from outside, loader 0.3.3 also notes which program was in front at that moment. The result is a crash, a fatal error, or a normal close. |
| Safe mode (optional) | Only if you want to see it. Copy the loader's test plugin `native\loader\out\crash_plugin.asi` into `<game>\riftstone\plugins`. Start the game twice: it crashes at start both times. The third start runs in safe mode and says so. Then delete `crash_plugin.asi` and run `Riftstone.cmd loader safe-mode off`. | `loader.log`'s `safe` lines, the quarantine list, and the crash reports from those starts. The loader's own harness already proves this (`native/loader/test`, the `safe` and `quarantine` tests). |

Why a test mod rather than deleting a texture archive: renaming or deleting game files could leave the
install broken. The mod leaves every game file as it is. It points the goblins' base colour map at a
texture that does not exist, so the game asks for that texture as a loose file, which is exactly the
case the guard handles. Uninstalling it puts the originals back. If the guard is off
(`[guard] missing_textures = 0`), the game stops with "Failed open file", as it would without Riftstone.

## Dragon's Dogma Online (no loader)

`playtest` reads only the Dark Arisen loader's logs. For Online, check these by hand, with the local
server running (`docs/ONBOARDING.md`, "Dragon's Dogma Online"):

- **First login:**
  - the solo launcher reaches character select;
  - the character loads into the hub;
  - the server's console shows the login.
- **English text:** menus, item names and descriptions show in English, with no missing glyphs and no
  text running out of its box.
- **Solo access:** a mission's entry board lets one player start it. `Riftstone.cmd ddo access`
  already shows every mission on this server at `minimum_members: 1`.

## A package on a clean install

`Riftstone.cmd package "<your mod>" --out test.zip` makes one zip (add `--plugin <file.asi>` for each plugin).
To test it:

1. Use a game folder with nothing of Riftstone in it (a fresh copy, or the Steam folder after
   `Riftstone.cmd restore` and removing the loader).
2. Extract the zip into that folder.
3. Start the game.
4. Check `riftstone\logs\loader.log`: it exists, the plugins load, and F10 shows the panel.
5. If Riftstone is on that machine, `Riftstone.cmd playtest` reads that folder like any other.

All of the above stays UNKNOWN in the docs until someone plays it.
