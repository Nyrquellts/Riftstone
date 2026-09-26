# Six skills for the Warrior (`six_skill_warrior` plugin)

The community wish: a Warrior equips three weapon skills, where every other vocation has six (three on
the main weapon, three on the secondary weapon, or six on a Mage's or Sorcerer's staff). This page says
what the game really does, measured statically on the PC exe (build 2364871, `tools/re_dd.py`) through
the PS3 build's names, and describes the plugin that gives the
Warrior six. Addresses are PC unless marked PS3. **In game: UNKNOWN** until played.

The brief's hypothesis was "hook the secondary palette shift toggle (L1/LB) into the two-handed Warrior
vocation table in memory". Neither exists as described. The second palette is the game action
`STG_SUB_WEP`, the secondary-weapon skill button, which every vocation already sends (section 3). There
is no per-vocation slot count either: the staff's six are special cases in code, keyed on the staff's
weapon and menu category numbers (sections 2 and 3). The Warrior takes that same path with the plugin.

## 1. Where a player's skills live, and why the save already has room

- **Six slots per weapon, for every weapon.** `cPlayerInfo::mWeaponSkill` is `s32[13][6]`, one row per
  weapon category (PS3 build; PC `+0x120`, PS3 `+0x114`). The two palettes the game fires from are
  `mMainSkill` (`+0x270`) and `mSubSkill` (`+0x27C`), three each. The PC property names are strings in
  the exe: `"mWeaponSkill[nWeapon::GSWORD]"` at `0x01595134`, `"mWeaponSkill[nWeapon::HAMMER]"` at
  `0x015951B0`, `"mSubSkill"` at `0x015950DC`.
- **Weapon categories** (`nWeapon::WEAPON_CATEGORY`, PS3 build): 1 sword, 2 mace, 3 longsword (`GSWORD`),
  4 dagger, 5 staff (`WAND`), 6 archistaff (`WAND_DX`), 7 warhammer (`HAMMER`), 8 shield, 9 magick shield,
  10 bow, 11 longbow, 12 magick bow. The Warrior is vocation 7 (`JOB_BATTLER`); longsword and warhammer
  share the custom skills 100 to 109: `64 00 00 00` at `0x014F6C8C` (longsword's first), `64 00 00 00` at
  `0x014F6C9C` (warhammer's first), `0A 00 00 00` at `0x014F6CC0` (ten of them). The staff's are 210 to
  239: `D2 00 00 00` at `0x014F6C94`, `1E 00 00 00` at `0x014F6CC8`.
- **Each skill's record** is 32 bytes of `nPlayer::mActIdTbl` (`0x014F3658`): id, action number, the
  vocations that may equip it, the weapons it belongs to. Skill 100 is action `0x01030028`, Warrior only
  (`0x80` = 1 << 7), longsword and warhammer (`0x88`): `64 00 00 00 28 00 03 01 80 00 00 00 88 00 00 00`
  at `0x014F42D8`. Skill 210 is action `0x01070000` for vocations 3, 4, 6 and 9 on both staffs:
  `D2 00 00 00 00 00 07 01 58 02 00 00 60 00 00 00` at `0x014F5098`.
- **The main weapon fills both palettes.** `setSkillFromEquipWeapon` (`0x00780350`, `this` in `eax`) empties
  both palettes, copies the main weapon's row slots 1-3 into `mMainSkill` and slots 4-6 into `mSubSkill`,
  then a secondary weapon's slots 1-3 over `mSubSkill`:
  `56 83 CE FF 89 B0 70 02 00 00 89 B0 7C 02 00 00` at `0x00780350` (both palettes to -1),
  `8D 54 49 24 8B 14 D0` at `0x00780391` (the row: `+0x120 + 0x18 x category`),
  `8B 94 88 2C 01 00 00 3B D6 74 06 89 90 7C 02 00 00` at `0x007803C9` (the main row's slot 4 into
  `mSubSkill`). So a longsword row with six skills already fills both palettes; only the staff ever has one.
- **The save keeps six per weapon.** PS3 `cSAVE_DATA_PARAM::mWeaponSkill` is `s16[13][6]`; the owner's
  `DDDA.sav` (unpacked read-only with `src/riftstone/saves.py`, 2026-09-26) holds twelve arrays
  `mWeaponSkill[nWeapon::SWORD]` ... `[nWeapon::BOW_MG]` of six `s16` each for the Arisen and every pawn,
  `GSWORD` and `HAMMER` included. PS3 `sGameSys::setParamToPlayerInfo` copies all six of every row, then
  calls `setSkillFromEquipWeapon`. **Nothing in the save grows** for six Warrior skills: slots 4-6 of the
  longsword and warhammer rows exist and are simply never written by the unmodded menu.

## 2. How the skill menu decides three or six

- **Menu categories by vocation.** The Inn's skill menu (`uGUISkillLearn`, with `uGUISkillBase`) lists
  categories per vocation, seven entries a row: `8B 04 8D 58 6D 51 01` at `0x006F80A1` reads
  `0x01516D58 + 4 x (7 x vocation + i)`. The Warrior's row is category 1 only:
  `01 00 00 00 FF FF FF FF` at `0x01516E1C`; the Mage's is 3 (`03 00 00 00 FF FF FF FF` at `0x01516DAC`), the
  Sorcerer's 4 (`04 00 00 00 FF FF FF FF` at `0x01516E54`), a Fighter's 0 and 5 (`00 00 00 00 05 00 00 00` at
  `0x01516D74`). A category's weapons are bits: category 1 is longsword and warhammer,
  `88 00 00 00` at `0x01516BDC`. No other vocation has category 1.
- **Six slots are a staff special case** (menu categories 3 and 4), in code, not data. In the PS3 build
  the checks sit in ten functions of `uGUISkillLearn`/`uGUISkillBase`; on PC the compiler copied
  `makeEquipLineup` into nine event handlers and moved the lock checks into one helper (below), so there
  are 19 compares, each `cmp REG, 3; je <six>; cmp REG, 4; je <six>`:

  | PC evidence | What the six-slot path does there |
  |---|---|
  | `83 FE 03 74 17 83 FE 04 74 12` at `0x006F9CDE` | `makeEquipLineup`: the slot list gets six items (`6A 06` at `0x006F9D01`) instead of three (`6A 03` at `0x006F9CEF`) |
  | `83 F9 03 74 0A BA 03 00 00 00 83 F9 04 75 05 BA 06 00 00 00` at `0x006FA9B5` | "is this skill equipped": looks through six slots, not three |
  | `83 FF 03 74 11 83 FF 04 74 0C` at `0x006F8695` | a copy of `makeEquipLineup` |
  | `83 FF 03 74 11 83 FF 04 74 0C` at `0x006F8F05` | a copy of `makeEquipLineup` |
  | `83 FF 03 74 11 83 FF 04 74 0C` at `0x006F9288` | a copy of `makeEquipLineup` |
  | `83 FF 03 74 11 83 FF 04 74 0C` at `0x006F93C8` | a copy of `makeEquipLineup` |
  | `83 F8 03 74 1F 83 F8 04 74 1A` at `0x006FB858` | `setup`: the six-slot layout when the menu opens on equipping |
  | `83 F8 03 74 3C 83 F8 04 74 37` at `0x006FC023` | `update`: the six slot frames, every frame |
  | `83 F8 03 74 1E 83 F8 04 74 19` at `0x006FCC37` | the six-slot layout on entering the slots |
  | `83 F8 03 74 29 83 F8 04 74 24` at `0x006FD0F1` | the six-slot layout on leaving them |
  | `83 F8 03 74 0E 83 F8 04 74 09` at `0x006FF5EE` | `dispEquipSlot`: the staff's six-slot display (`E8 48 0D 00 00` at `0x006FF603`), not the three-slot one (`E8 31 07 00 00` at `0x006FF5FA`) |
  | `83 F8 03 0F 84 DF 00 00 00 83 F8 04 0F 84 D6 00 00 00` at `0x007039CC` | a slot chosen: the cursor over two rows of three |
  | `83 FF 03 74 11 83 FF 04 74 0C` at `0x00703F8F` | a copy of `makeEquipLineup` |
  | `83 FF 03 74 11 83 FF 04 74 0C` at `0x0070412E` | a copy of `makeEquipLineup` |
  | `83 FF 03 74 11 83 FF 04 74 0C` at `0x007042D0` | a copy of `makeEquipLineup` |
  | `83 FF 03 74 11 83 FF 04 74 0C` at `0x00704746` | a copy of `makeEquipLineup`, after a skill is equipped |
  | `83 F8 03 74 59 83 F8 04 74 54` at `0x00704A70` | the cursor after a skill is equipped |
  | `83 FF 03 74 11 83 FF 04 74 0C` at `0x00704D05` | a copy of `makeEquipLineup`, after a skill is equipped |
  | `83 F9 03 74 2F 83 F9 04 74 2A` at `0x007057AF` | the highlight over six slots |

- One more staff check locks slots 4-6 for a Mystic Knight or Magick Archer holding a staff (vocations 4
  and 6): `83 F8 04 74 05 83 F8 06 75 0C 83 7C 24 04 02 76 05` at `0x00705CA4`. It never locks category 1.
- **The equip itself is generic.** Choosing a skill writes the chosen slot (any of six) of the category's
  first weapon row and mirrors a longsword write into the warhammer row:
  `89 84 91 C8 01 00 00` at `0x00704C24` (`+0x1C8` = row 7), after clearing the same skill from any other
  slot, the warhammer's included (`8B 54 24 1C C7 02 FF FF FF FF` at `0x00704A1B`).
- **Verdict:** the Warrior's missing second row is code, not data. Its menu category is not "no secondary
  weapon"; the menu simply gives six rows only to categories 3 and 4.

## 3. How a skill button fires a skill

- **The buttons are the same for every vocation** (the button layer, `0x00B52A00`; PS3
  `cPlInterface::updateBtnInfoSub` with `sPadExt::PAD_BTN_TYPE` names). Holding the main-weapon skill
  button `STG_MAIN_WEP` (`B8 1C 00 00 00` at `0x00B52B44`) sets bit 4 (`81 0F 04 00 80 00` at
  `0x00B52B57`); holding the secondary-weapon skill button `STG_SUB_WEP` (`B8 1D 00 00 00` at
  `0x00B52B99`) sets bit 8 (`83 0F 08` at `0x00B52C21`). A face button `STG_ATTACK_0` (`B8 17 00 00 00` at
  `0x00B52D88`) sets bit 1 (`83 0F 01` at `0x00B52D96`), plus slot bit 0x10 with the main button held
  (`F6 45 30 04` at `0x00B52D99`, `83 C8 10` at `0x00B52DA1`) or 0x80 with the secondary one
  (`F6 45 30 08` at `0x00B52DA6`, `81 0F 80 00 00 00` at `0x00B52DAC`); `STG_ATTACK_1`/`_2` likewise give
  0x20/0x40 and 0x100/0x200. Nothing here asks for a secondary weapon: a Warrior pressing the secondary
  button with a face button already sends "secondary slot N".
- **The palettes become action numbers** in `initJob` (`0x00B5EF90`): `mMainSkill` into `uPlayerBase+0x35F4`
  (`8B 87 70 02 00 00` at `0x00B5EFC3`, `89 96 F4 35 00 00` at `0x00B5F00F`) and `mSubSkill` into `+0x3600`
  (`8B 87 7C 02 00 00` at `0x00B5F021`, `89 8E 00 36 00 00` at `0x00B5F060`). A skill in secondary slot N
  answers bit 0x80/0x100/0x200 (`81 C1 00 36 00 00` at `0x00B79B72`, `B8 80 00 00 00` at `0x00B79BA4`), one
  in main slot N 0x10/0x20/0x40 (`81 C1 F4 35 00 00` at `0x00B79AE2`, `B8 10 00 00 00` at `0x00B79B14`).
- **Which action starts** is `cPlActCheckTbl::getNextAction` (`0x00ABA690`): the main weapon's skill table,
  the secondary weapon's, the main weapon's normal table, the common table. For each entry
  `checkCmdType` (`0x00ABA810`) switches on the entry's type (`FF 24 85 D0 A9 AB 00` at `0x00ABA89B`):
  type 3 "main palette" needs the main button held (`F6 86 E4 32 00 00 04` at `0x00ABA8B2`), type 4
  "secondary weapon" the secondary one (`F6 86 E4 32 00 00 08` at `0x00ABA8EA`), and type 5 "either
  palette" (`85 F6 74 23 F6 86 6D 20 00 00 02` at `0x00ABA91F`) tries the main button
  (`F6 86 E4 32 00 00 04` at `0x00ABA946`), then the secondary one (`F6 86 E4 32 00 00 08` at
  `0x00ABA973`), always with the **main** weapon. The jump table sends type 3 to `B2 A8 AB 00` at
  `0x00ABA9D8` and type 5 to `1F A9 AB 00` at `0x00ABA9E0`.
- **The Warrior's skills are type 3, the staff's type 5.** The longsword/warhammer table (one table for
  both, `cPlActCheckTbl::mCheckActSkillGSwordTbl`) lists the ten skill actions as type 3:
  `28 00 03 01 FF FF FF FF 03 00 00 00` at `0x014F7538` (skill 100) ...
  `2D 00 03 01 FF FF FF FF 03 00 00 00 00 00 00 00 01 03 00 00 19 00 00 00` at `0x014F75D8` (skill 105);
  the staff's thirty are type 5: `00 00 07 01 FF FF FF FF 05 00 00 00` at `0x014F7958`. So even a skill in
  the Warrior's secondary palette would never start: the harness measures that a vanilla Warrior's
  secondary button with Square starts the light attack (`0x01030000`), with Triangle the heavy attack
  (`0x01030003`), with Circle nothing.
- **Everything after the start is palette-blind**, as the staff needs: `canUseSkill`/`checkSkillSet`
  (`0x00B79330`/`0x00B793A0`) find the action in either palette; PS3 `cPlAction::setCstmMotionList` takes a
  main-weapon skill's motion list from whichever palette holds it; `getSkillEachBtn` (holding a charge)
  handles both; PS3 `sSoundExt::setSkillMotionSeResource` loads sounds for all six slots. Per-skill effect
  providers (PS3 `uPlayerBase::initEffect`) exist only for staff spells, magick shield and magick bow
  skills, not for longsword skills.
- **What the staff gets and the Warrior does not**, besides the type: five weapon-category compares
  (`cmp REG, 5; je; cmp REG, 6`):
  - `removeIllegalCstmSkill` (`0x00780590`), run at every player setup: a staff with no secondary weapon
    keeps and range-checks its secondary palette (`83 F8 05 74 09 83 F8 06 0F 85 30 01 00 00` at
    `0x007805E9`); anything else with no secondary weapon has it emptied (`83 F8 05 74 2F 83 F8 06 74 2A` at
    `0x007806EF`, `89 A9 7C 02 00 00` at `0x00780701`). A Warrior's slots 4-6 would be wiped here.
  - the skill-archive loader: slots 4-6 ask for their archive with the main weapon only for a staff
    (`83 FE 05 74 05 83 FE 06 75 09 83 7C 24 20 00 75 02 8B C3` at `0x0078916C`), else with the secondary
    weapon; for a Warrior, none, so `getSkillArcTag` (`E8 67 0A 00 00` at `0x00789184`) returns "no archive"
    (`B8 8A 84 00 00` at `0x00789C88`), not the longsword's (`B8 E3 41 00 00` at `0x00789C28`).
  - `initMainWpnMotion` (`0x00B5B400`) loads the secondary palette's motion lists into
    `uPlayerBase+0x4C44` (`8D 9D 44 4C 00 00` at `0x00B5B5B0`) only for a staff
    (`83 FA 05 74 09 83 FA 06 0F 85 8A 01 00 00` at `0x00B5B4EE`), and `initSubWpnMotion` (`0x00B5B740`)
    keeps them only for a staff (`83 FB 05 74 32 83 FB 06 74 2D` at `0x00B5B903`), releasing them otherwise
    (`81 C5 44 4C 00 00` at `0x00B5B916`).
- `removeJobMismatchCstmSkill` (`0x00780840`) empties staff slots 4-6 for vocations other than Mage and
  Sorcerer (`83 F8 03 74 1A 83 F8 09 74 15` at `0x00780A31`, `83 FB 05 74 05 83 FB 06 75 0B 83 FE 03 7C 06`
  at `0x00780A3B`), and never empties slots 4-6 of the longsword or warhammer rows.

## 4. The design: the Warrior takes the staff's path

`native/plugins/six_skill_warrior` makes the Warrior's numbers take every staff branch above, and nothing
else. Each compare site's first instruction pair (`cmp REG, imm8; je`, 5 bytes, 9 at `0x007039CC`) becomes
a `jmp` to a small generated thunk that repeats it, adds the Warrior's value and continues at the original
second compare, so the rest of the game's code runs unchanged:

| Where | Added value | For whom |
|---|---|---|
| the 19 menu compares (section 2) | category 1 | anyone using the menu (you and your pawns) |
| the 5 weapon compares (section 3) | weapon categories 3 and 7 | the party only |
| `checkCmdType`'s type-3 jump-table entry (`0x00ABA9D8`) | a thunk sends a longsword/warhammer table entry to the game's own type-5 code | the party only |

- **The party** is the four `cPlayerInfo` records `sGameSys` keeps: the Arisen's at `+0xA76D0`
  (`8B 0D BC A4 8F 01 81 C1 D0 76 0A 00` at `0x00B5A833`) and the pawns' at `+0xA7EC0 + 0x1660 x index`
  (`69 C9 60 16 00 00` at `0x00B5A84C`, `8D 8C 31 C0 7E 0A 00` at `0x00B5A859`); other humans use their
  own record inside the object (`8D 88 70 58 00 00` at `0x00B5A868`) and stay vanilla, so an enemy with a
  longsword cannot pick up a second palette. The check compares addresses only.
- **Save compatibility.** No array grows: the plugin only lets the menu write slots 4-6 of the longsword
  and warhammer rows, which the save already carries. Removed, the plugin leaves those six values in the
  save; the unmodded game copies them into the secondary palette and `removeIllegalCstmSkill` empties it
  again at the next setup, so the Warrior plays with three as before, and the menu shows three.
- **Safety.** Before patching, the plugin compares every site's full compare pair (and each `je` target
  with its table), the jump table's type-3 and type-5 entries, the type-3/5 code, the ten type-3 entries of
  the longsword table and its end, the Warrior's menu row and weapon bits, the skill ranges,
  `setSkillFromEquipWeapon`'s slot-4 copy and the party offsets. Any difference, or a second copy of the
  plugin, patches nothing, and `riftstone\logs\six_skill_warrior.log` says why. All sites or none. The
  thunks keep every register and the stack; nothing runs per frame beyond the compares.
- **Settings:** `six_skill_warrior.ini`, `[warrior] Mode = six` (default) or `off`.

## 5. Proof (harness)

`python native/plugins/six_skill_warrior/test/run_tests.py` maps `DDDA.exe` into a stub process at its
fixed base, loads the built plugin (which verifies and patches that copy) and runs the game's own code.
Four profiles: the shipped ini (`six`), `off`, an unknown mode, and an exe with one byte of a menu compare
changed (refused). **405 checks pass** (102 with the plugin active, 101 in each other profile):

- **Every patched compare, from its first byte**, with every category value 0-12, for a party member and
  for anyone else; captures on the two paths say which it took, and every register and the stack come back
  as they went in. The staff path is taken for 3 and 4 (menu) and 5 and 6 (weapons) in every profile, and
  also for 1 (menu) and for 3 and 7 for the party (weapons) with the plugin. The type-3 dispatch runs the
  type-5 code for a longsword entry of the Arisen or a pawn, and the type-3 code for anyone else, a sword
  entry or past the table's end.
- **`setSkillFromEquipWeapon`**: a longsword row {100..105} gives main {100, 101, 102}, secondary
  {103, 104, 105} (unchanged code).
- **`removeIllegalCstmSkill`**: the Arisen's and a pawn's Warrior secondary palette survives (longsword and
  warhammer), anyone else's is emptied, a non-longsword skill among them is removed; a Mage's staff and a
  Fighter's shield palettes are untouched. Without the plugin the Warrior's is emptied.
- **`removeJobMismatchCstmSkill`**: the Warrior's rows keep all six; a Mystic Knight's staff slots 4-6 are
  emptied by the game itself.
- **`getNextAction`** on a fake Warrior with the real action tables: the secondary-weapon skill button with
  Square/Triangle/Circle starts skills 103/104/105 for the Arisen and a pawn with the plugin, and the
  game's own attack (light, heavy, nothing) without it or for anyone else; the main button starts 100 and
  102 in every case; a Mage's staff spell works either way.
- **`initMainWpnMotion` then `initSubWpnMotion`**: the six skill motion lists load and stay for the party's
  Warrior (longsword and warhammer); three load for anyone else or without the plugin; a staff keeps six.
- **The skill-archive loader**: the Arisen's six slots ask for the longsword archives `0x41E3`-`0x41E8`;
  without the plugin, or for anyone else, slots 4-6 ask for none (`0x848A`).
- **The menu's counts**: "is it equipped" finds a skill in slot 5 of category 1 and `makeEquipLineup` gives
  category 1 six slots with the plugin, three without; categories 3 and 4 get six and the others three in
  every profile.

## 6. What is still UNKNOWN in game

- That the plugin patches the real process (its log lists every site) and that the menu draws two rows for
  the Warrior as it does for the staff: the six-slot layout and its frames are the staff's own GUI
  elements, chosen by the same branches; how the second row's captions read for a Warrior is untested.
- The HUD's skill palette while the secondary button is held: PS3 `uIdCockpit::checkCustomSkill` reads
  `mSubSkill` for any weapon; the PC HUD code was not traced.
- How the Warrior's second-row skills play (charged skills held on the secondary button, pawns choosing
  them in combat: the pawn AI asks for the same button bits), and whether a pawn with six Warrior skills
  shared through the Rift behaves in an unmodded game as the static reading says (slots 4-6 ignored).
- Which physical button `STG_SUB_WEP` is depends on the key configuration; the code has no L1/LB toggle.
