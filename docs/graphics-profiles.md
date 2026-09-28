# Graphics profiles (`riftstone graphics`)

A graphics tool for Dark Arisen, such as an ENB and its preset, puts files in the game folder and wants the game's
own settings set a certain way. A **graphics profile** is that set of files plus those settings, put in and taken
out as one, with every file checked by its SHA-256 and everything it replaces kept.

```
riftstone graphics                 what is in the game folder now, and every profile with what would stop it
riftstone graphics list            the profiles (and where they live)
riftstone graphics check <name>    a profile's files are the bytes it names; every setting names a key that exists
riftstone graphics apply <name>    put it in (the profile there before comes out first); the game must be closed
riftstone graphics off             take it out and put back what it replaced
```

## A profile

A folder in the profiles folder: `$RIFTSTONE_PROFILES`, else Riftstone's own `profiles\graphics` when it exists,
else `Documents\Riftstone\profiles\graphics`.

| Path | What |
|---|---|
| `profile.json` | schema `riftstone-graphics/1`: the files by SHA-256, the settings, notes. Kept in git. |
| `files\` | the files exactly as their authors ship them. **Never in git and never in a package**: ENBSeries forbids republishing its binaries, and a preset is its author's (`.gitignore`: `profiles/**/files/`). |

```json
{"schema": "riftstone-graphics/1", "title": "...", "notes": ["..."],
 "files": {"d3d9.dll": {"sha256": "...", "from": "enbseries_dragonsdogma_v0300.zip: WrapperVersion/d3d9.dll"}},
 "ini": {"enblocal.ini": {"PROXY": {"EnableProxyLibrary": "true", "ProxyLibrary": "riftstone\\dxvk\\d3d9.dll"}}},
 "config": {"GRAPHICS": {"HDR": "FLOAT"}, "DISPLAY": {"VSYNC": "OFF"}},
 "loader": {"d3d9": {"chain": ""}}}
```

- `files`: each goes to the same path in the game folder. A path is refused unless it is plain and relative, is
  a graphics tool's kind of file (`.dll .exe .ini .fx .fxh .hlsl .h .png .bmp .jpg .jpeg .dds .tga .txt .conf .cfg
  .json`), and is none of the game's, Steam's or Riftstone's own (`DDDA.exe`, `dinput8.dll`, `riftstone_loader.ini`,
  `steam_api.dll`, anything under `nativePC\` or `riftstone\`). A `d3d9.dll` must be a 32-bit Direct3D 9 DLL: the game
  loads it at start.
- `ini`: keys of the profile's own `.ini` files, set in the copy that goes into the game folder. The files keep
  their authors' bytes in the profile; the overrides are the part that is yours and goes into git.
- `config`: the game's own `config.ini` (`%LOCALAPPDATA%\CAPCOM\DRAGONS DOGMA DARK ARISEN\config.ini`), sections
  `GRAPHICS`, `DISPLAY` and `CPU`.
- `loader`: `riftstone_loader.ini`, keys of the loader's own template.

Every setting names a key its file already has: a misspelt key is refused, never added. The game's settings take only
the values DDDA.exe matches them against. These are its enum names without the prefix, from the strings next to its
config reader:

| Key | Values | Note |
|---|---|---|
| `HDR` | `NONE`, `DEFAULT`, `FLOAT` | the options menu's **High** is written `FLOAT` (`HDR_FLOAT`); there is no `HIGH` |
| `AltAntiAlias` | `NONE`, `FXAA`, `FXAA3`, `FXAA3HQ` | the game's FXAA; `NONE` turns it off |
| `AntiAlias` | `NONE`, `MSAA2X`, `MSAA4X`, `MSAA8X`, `CSAA8X`, `CSAA8XQ`, `CSAA16X`, `CSAA16XQ`, `CSAA32X` | |
| `ViewRange` | `NORMAL`, `FAR`, `FARTHEST` | `docs/lod.md`, `docs/draw-distance.md` |
| `TextureFiltering` | `TRILINEAR`, `ANISO_X2`, `ANISO_X4`, `ANISO_X8`, `ANISO_X16` | |
| `GrassQuality` | `LOW`, `MEDIUM`, `HIGH` | |
| `DofFilter`, `VSYNC`, `FullScreen`, `Flush`, `SLI`, `Stereo`, `RenderingThread` | `ON`, `OFF` | |

## What `apply` and `off` do

`apply` refuses while the game runs. The game's Direct3D DLL cannot be swapped while it is loaded, and the game writes
`config.ini` when it closes. `apply` then does the following, in order:

1. Checks the profile: files present, the bytes it names, every setting's key there, an ENB proxy that is a Direct3D 9
   DLL inside the game folder and not the game folder's `d3d9.dll` itself.
2. Makes every file it will write before anything is touched.
3. Takes out the profile applied before, if there is one.
4. Moves any other file in the way into a backup (`%LOCALAPPDATA%\Riftstone\backups\graphics-<time>\`), with a copy of
   `config.ini` and `riftstone_loader.ini`.
5. Writes the files and sets the settings, then records all of it in `<game>\riftstone\graphics.json`.

If a step fails, the game folder and both settings files go back as they were.

`off` puts the game back as the first `apply` found it, with two exceptions:

- A file changed since it was written goes into the backup's `changed\` folder instead of being deleted. An ENB's
  editor (Shift+Enter) saves its settings into its `.ini` files.
- A setting changed since, in the game's options, keeps its new value.

A profile that puts `d3d9.dll` in the game folder must also empty the loader's `[d3d9] chain`. The game folder's own
`d3d9.dll` stays in charge of the game's Direct3D 9, and the loader does not load a chained one beside it
(`docs/runtime.md`). An ENB that should run over DXVK loads DXVK itself through its `[PROXY]` section. DXVK then
writes its log, `DDDA_d3d9.log`, in the game folder, not `riftstone\logs`: the loader sets `DXVK_LOG_PATH` only for
a DXVK it chains itself.

`riftstone playtest` shows the profile in the game folder. When its ENB names DXVK in `[PROXY]`, playtest also says
whether DXVK wrote its log during the session: OK if it did, FAIL if the session ended and it did not.

## ENBSeries on Dark Arisen: what its files say

Measured from `enbseries_dragonsdogma_v0300.zip`, the last ENB for this game, and the Resonant ENB 1.6 preset (both
2026-09-27):

- The wrapper version is one `d3d9.dll` (796,672 bytes) plus default shaders and settings. It has **no
  `enbhost.exe`**, and its `enblocal.ini` has no `[MEMORY]` or `[PERFORMANCE]` section. `ExpandSystemMemoryX64`,
  `ReduceSystemMemoryUsage`, `VideoMemorySizeMb` and `SpeedHack` belong to Skyrim's ENBoost.
- Its antialiasing is `[ANTIALIASING] EnableEdgeAA` only, with no temporal or sub-pixel option. Anisotropic filtering
  is `ForceAnisotropicFiltering`, not `ForceAnisotropy`.
- `[ENGINE] EnableVSync=true` by default: **ENB forces VSync** whatever `config.ini` says.
- v0.300 adds control of **the game's own depth of field** ("mostly visible as ugly bloom everywhere", its readme):
  the external shader `enbgamedepthoffield.fx` and `[GAMEDEPTHOFFIELD]` in `enbseries.ini` (`DisableInGameplay`,
  `DisableInCutScenes`, `DisableNearPlaneBlurring`, `DisableFarPlaneBlurring`, fades by time of day). This needs the
  game's `DofFilter=ON`. `[EFFECT] EnableDepthOfField` is ENB's own depth of field, a different effect.
- The readme asks for the game's settings near their maximum and HDR at High (`HDR=FLOAT`).
- A preset keeps its shaders in `enbseries\`, and only one copy of each `.fx` may exist between that folder and the game
  folder. So a profile takes the ENB package's `d3d9.dll` and the preset's files, not the package's default shaders.
- ENB with DXVK as its proxy in this game: UNKNOWN until played.

## Texture fixes that come as repack scripts

Some graphics mods ship textures and a script that unpacks a game archive with a third-party tool, drops them in and
repacks the archive in place. Don't Blind Me is one: two `.dds`, and ARCtool rebuilding
`nativePC\rom\bbsrpg_core.arc`. Riftstone keeps the game's archives stock, so such a mod becomes a Riftstone mod:

```
riftstone new "Don't Blind Me"
riftstone extract effect/tex/cm/t_cm000_00_GM.tex --mod "Don't Blind Me" --arc rom/bbsrpg_core
riftstone tex from-dds t_cm000_00_GM.dds --like <the extracted .tex> -o <the mod's copy>
riftstone install "Don't Blind Me"
```

`--arc` keeps the change to the archive the script changed (`archives\rom\bbsrpg_core.arc\...`). Don't Blind Me's two
textures are in 320 and 189 archives, and the script changes only `bbsrpg_core`. `--like` keeps the original texture's
format id and header. Keep its mip count too: Don't Blind Me's `.dds` carry 11 levels, and the originals have 10.
