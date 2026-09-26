# Dragon's Dogma Online's vocations, from the client's own data

What defines each DDO vocation (job) in client 03.04.003: the archives, the tables that tie a skill to
its animation, hit data, sounds and effects, and what Riftstone can read and rebuild today. Measured
read-only on the installed client (2026-09-25; the survey's scripts and JSON sit outside git because they
hold game text). How any of it plays is **UNKNOWN** until someone plays it.

## Eleven vocations

The client has **11** vocations. Every source agrees: the text has `JOB_NAME_1` .. `JOB_NAME_11`
(0 = "any"), the archives run `rom/job01` .. `rom/job11`, the per-job tables hold 11 records, DDO.exe
has classes `cJob01*` .. `cJob11*`, and its table of skill archive names covers 11 jobs x 20 skill
slots x 3 variants. No job 12-14 exists anywhere.

| Id | Vocation | 日本語 | Main weapon | Sub weapon | Custom skills (base + EX) | Core skills | Motion lists | Attack params |
|---|---|---|---|---|---|---|---|---|
| 1 | Fighter | ファイター | One-handed Sword (1) | Shield (2) | 14 + 8 | 11 | 26 | 148 |
| 2 | Seeker | シーカー | Daggers (6) | - | 14 + 8 | 14 | 21 | 138 |
| 3 | Hunter | ハンター | Bow (7) | - | 14 + 8 | 12 | 5 | 138 |
| 4 | Priest | プリースト | Staff (11) | - | 14 + 8 | 9 | 16 | 95 |
| 5 | Shield Sage | シールドセージ | Greatshield (4) | Rod (5) | 14 + 8 | 13 | 21 | 127 |
| 6 | Sorcerer | ソーサラー | Archistaff (12) | - | 14 + 8 | 7 | 26 | 148 |
| 7 | Warrior | ウォリアー | Greatsword (3) | - | 14 + 8 | 10 | 21 | 138 |
| 8 | Element Archer | エレメントアーチャー | Magick Bow (9) | - | 14 + 8 | 13 | 2 | 148 |
| 9 | Alchemist | アルケミスト | Magick Gauntlet (8) | - | 12 + 8 | 13 | 18 | 128 |
| 10 | Spirit Lancer | スピリットランサー | Spirit Lance (13) | - | 10 + 8 | 13 | 18 | 108 |
| 11 | High Scepter | ハイセプター | Magick Sword (15) | - | 8 + 0 | 13 | 11 | 80 |

Weapon categories are `ui\00_message\common\weapon_ctgr` labels; a category's models live in
`rom/wp/wp3CCnnn` with CC = category - 1 (`etc\wepCateResTbl.wcrt`). Jobs 1-9's weapons come from the
character-creation presets (`rom/equip_preset_jobNN`: weapon models and item ids); jobs 10-11 have no
preset, so their weapons come from the job description text and the server's item list (which restricts
every weapon category to exactly one job, agreeing with all nine presets). "Sub weapon" follows the
server's slot numbering (an inference). Category 10 is Hunter bow gear and 14 the Seeker's rope.
Motion lists / attack params count the distinct resources named for the job.

## Where a vocation lives

| What | Where | Linked by |
|---|---|---|
| Base archive | `rom/jobNN` | DDO.exe's `job%02d` (in `cPlayerLoadManager`) |
| One archive per custom skill | `rom/jobNN_csMM` (142), EX variants `..._exKK` (96), Sorcerer human-enemy variants `..._em011041` (4) | a literal table of these names in DDO.exe |
| Skill -> resources | `obj\pl\pl000000\param\jobcustom\jobcustomNN.jcp` | loaded by job number (`uHuman::vf211`) |
| Custom skills | `ui\00_param\skill_ability\costom_skill_data_NN.csd` (`rom/skill/custom_skill_data_NN`) | loaded by job number; names: `custom_skill_name_NN.gmd` |
| Core skills | `normal_skill_data_NN.nsd` (`rom/skill/normal_skill_data_NN`) | by job number |
| Motion lists | `obj\pl\pl000000\motion\m00NN\m00NN_at`, `_co`, `_csMM`, `_at_exKK\...`; `m0000` = the shared human set (`rom/Human`) | path; `.jcp` |
| Attack params | `obj\pl\pl000000\collision\jobNN\csMM\csMM_01..10.atk` | `.jcp` |
| Key commands | `obj\pl\pl000000\keycommand\JobNN*.kcm`; presets `keycustom\key_jobNN_preset00.kcp` (jobs 1-10) | path |
| Action params | `obj\pl\pl000000\actionparamlist\jobNN_com.acp`, `jobNN_cs.acp` | path |
| Shell (projectile) params | `obj\pl\pl000000\shellparam\jobNN.shl` (XFS); groups `rom/SHL/SHL_jobNN_GR*` | path |
| Effects | `effect\efl\pl\pl00NN[_ex]\` (`pl0000` shared) | path |
| Sounds | `sound\se\job\jobNN...`; every human's motion SE is `sound\se\hm\hm.bmse` | path |
| Level-up tables | `obj\pl\pl000000\param\jobleveluptbl\jobNN.jlt2` (jobs 1-9 only) | path |
| Job master NPCs, tutorials | `etc\jobMasterCtrl.jmc`, `quest\jobTutorialQuestList.jtq` | `mJobId` |
| Damage multipliers | `obj\pl\pl000000\param\etc\dmJobAdj.dja` (job type 1-11) | field |
| Who may equip what | `etc\itemEquipJobList.eir` (bit n = job n) | field |

`jobcustomNN.jcp` is the spine: one 140-byte record per custom skill (ids 1..N base, 101.. and 201.. the
two EX variants), each naming 17 resources by (JAMCRC of the path, type id): the motion list, an
alternate motion list (only the Spirit Lancer's `m0010_boost_csMM` uses it), motion params, collision,
**10 attack params**, a sound request, an effect provider and an extra sound request. 6,079 of its 6,083
references resolve to real resources; the other 4 name `.epv` files that are not in the client.

`tools/skill_chain.py [--job N] [--skill M] [--json out.json]` follows every record to its files and one
step further: the skill's name (the `.csd` message index into `custom_skill_name_NN`), the motion list
(motions, frames, joints, whether it keys the weapon joints 150-154), the collision (hit shapes by kind
and joint), level 1's attack data (element, rates) and the effect provider's effect lists and the
textures they load. Over all 11 jobs (2026-09-25, 16 s): 426 skill records, 6,079 references resolved, 4
not in the client (the `.epv` of Priest 209, Shield Sage 108 and 208, Alchemist 104); 415 skills have
collision, 96 of them with hit shapes on weapon joints; 345 of the 348 skills with a motion list key the
weapon joints. Its output holds game text, so keep `--json` files outside git.

Hunter and Element Archer have no per-skill motion lists (Hunter except cs13-14): their bow skills use
`m0003_at` / `m0003_bow` and `m0008_at` with `job03/08_bow_param.bap`. Only Priest, Shield Sage and
Sorcerer have `.chant` files. High Scepter has EX archive slots but no EX skill definitions (its EX
archives are stub copies of the base skills).

## What Riftstone reads and rebuilds (byte-exact, `check_corpus --game ddo`)

| Format | Files | State |
|---|---|---|
| `.jcp` rJobCustomParam | 11 | byte-exact + YAML (flat `jcp`) |
| `.csd` rAcquirement::rCustomSkillData | 11 | byte-exact + YAML (`csd`): skill no, message index, base skill, level table (job level, job points) |
| `.nsd` rAcquirement::rNormalSkillData | 11 | byte-exact + YAML (`nsd`) |
| `.jmc` rJobMasterCtrl | 1 | byte-exact + YAML, DDO.exe's own field names |
| `.eir`, `.wcrt`, `.dja`, `.jlt2`, `.jtq` | 1, 2, 1, 9, 1 | byte-exact + YAML |
| `.lmt` motion lists | 1,402 | byte-exact, every key decoded, portable to DDDA (`docs/animation.md`) |
| `.shl` shell params, `.bmse` | XFS | byte-exact + YAML (existing XFS support) |
| `.atk` rAttackParam | 1,469 | byte-exact + YAML (flat `atk`, traced from DDO.exe's loader): 22,553 per-hit records; `mPathCrc` = JAMCRC of the file's own path; 00D appears to be the element (1 none, 2 fire, 3 ice, 4 thunder, 5 holy, 6 dark -- it matches every Sorcerer and Element Archer skill's name), 008 / 014 the physical / magick rates, five (u32, u16) pairs debilitation id and amount |
| `.acp` rActionParamList | 85 | byte-exact + YAML: per row an action class (its MtDTI id, looked up by the loader) and its parameters |
| `.kcm` rKeyCommand, `.kcp` rKeyCustomParam | 55, 10 | byte-exact + YAML; kcm ids 18/1C appear to be action ids (list << 12 \| row: 0 hm_common, 1 jobNN_cs, 2 jobNN_com); kcp 0E appears to be a Windows key code |
| `.motparam` rMotionParam, `.chant` rMagicChantParam | 54, 7 | byte-exact + YAML; meanings UNKNOWN (chant: three 4x4 matrices and ten per-level floats) |
| collision `.ocl` (`COL`) | 1,176 | byte-exact + YAML (`ocl_ddo.py`): each skill's hit shapes (sphere / capsule / oriented box) and attack params |
| `.jobbase`, `.abd`, `.aad`, `.wrt`, `.wpn_ofs`, `.pas` | 1, 1, 1, 2, 3, 12 | not decoded (layouts partly measured) |

Field meanings that are not the engine's own names are descriptive (`mJobPoint`, `mMsgIndex`) or
`mUnkXX` (XX = the member offset the loader stores to); the `.csd`/`.nsd` fields `mUnk04`, `mUnk08`,
`mUnk0C`, `mUnk0E` are UNKNOWN. "Appears to be" marks a correlation measured on the data, not proven by
code.

## Custom skills per vocation (base skills, the game's English names)

- **01 Fighter** (14): Blink Strike, Cymbal Attack, Skyward Lash, Tusk Toss, Sheltered Spike, Compass Slash, Hindsight Slash, Downthrust, Moving Castle, Intimate Strike, Flowing Sword Flash, Brave's Raid, Pierce Slash, Flowing Shield Spiral
- **02 Seeker** (14): Biting Wind, Toss and Trigger, Back Kick, Ensnare, Falcon Kick, Reset, Stepping Stone, Powder Charge, Whirlwind Blade, Sliding Rope, Backfire, Easy Kill, Explosive Flame Blade, Soaring Hawk Slash
- **03 Hunter** (14): Threefold Arrow, Triad Shot, Puncture Dart, Flying Din, Cloudburst Volley, Whirling Arrow, Crimson Arrow, Full Bend, Backward Retreat, Explosive Arrow Volley, Storm Arrow, Demon Arrow, Sky Burst Shot, Combined Pierce Shot
- **04 Priest** (14): Attack Riser, Defense Riser, Healing Spot, Curing Spot, Seraphim Flap, Sacred Shine, Guard Bit, Soul Explosion, Solid Riser, Energy Spot, Holy Glare, Quick Charge, Solace Riser, Blast Addition
- **05 Shield Sage** (14): Force Shield, Element Glow, Slow Light, Hypnos Light, Rampart Raid, Earth Shake, Holy Wall, Binding Anchor, Element Light, Stun Burst, Hands of God, Force Anchor, Protection Swing, Stone Light
- **06 Sorcerer** (14): Firestorm, Fulmination, Black Haze, Comestion, Frigor, Bolide, Crescent Blade, Seism, Levin, Darkness Mist, Gicel, Prominent Sphere, Icicle Pierce, Lightning Stake
- **07 Warrior** (14): Upward Strike, Pommel Strike, Savage Lunge, Escape Slash, Spark Slash, Devil Burst, Clarity, Heaven Thrust, Defensive Stance, Great Windmill, Annihilator's Wind Slash, Flying Dragon Crash, Great Gouging Fang, Earthquake Fang
- **08 Element Archer** (14): Healing Bolt, Curing Bolt, Fourfold Bolt, Ricochet Seeker, Flaming Bow, Magickal Flare, Enfeebling Bow, Crippling Bow, Energizing Bolt, Exhausting Bow, Weakening Bow, Gamble Draw, Healing Flash, Tearing Tentacle Arrow
- **09 Alchemist** (12): Alma Wave, Alma Pillar, Pile Binder, Alma Windust, Rex Elementa, Rex Catapulta, Dolus Morsus, Golda Aurum, Alchemical Burst, Dolus Aeris, Alma Sector, Regal Barrier
- **10 Spirit Lancer** (10): Aurom Fang, Aurom Slay, Corr Storm, Corr Spike, Scrios Blast, Scrios Guard, Wall Glasta, Cure Glasta, Corr Meteor, Éadrom Counter
- **11 High Scepter** (8): Mirage Shift, Wall Barrier, Phantom Edge, Full Moon Light, Black Flash Fang, Dim Slice, Eclipse Bright, Terror Blast

222 custom skills in all (142 base + 80 EX); every skill number matches the Arrowgene server's enum.
128 core skills; 33 of the 34 purchasable ones match the server's job points and level (Priest's Great
Holy Aura costs 900 JP in the client, 1,000 on the server).

## The overnight directive's descriptions, checked against the game's text

| Claim | What the client says |
|---|---|
| 14 vocations | 11 (above) |
| Alchemist: transmutation gauntlets, pile-bunker spikes | yes: Magick Gauntlet; "Pile Binder", "Alma Pillar", "Dolus Morsus" |
| Alchemist: gold platform jumping | two skills merged: "Golda Aurum" coats you in gold to block; the jump pads are "Rex Catapulta" / "Rex Leap" (not gold) |
| High Scepter: magic rapier teleports, phantom blades, dark/light drain | teleports "Mirage Shift"/"Return Shift", blades "Phantom Edge"/"Dim Slice", light and dark "Full Moon Light", "Eclipse Bright", "Black Flash Fang", "Terror Blast"; the weapon is a Magick Sword (no "rapier"); "drain" is magick absorption, no HP drain in the texts read |
| Spirit Lancer: tether buffs, dive spears, healing/boost auras | dives "Fall Thrust"/"Air Thrust", auras "Wall Glasta", "Cure Glasta", boosts; "tether" appears nowhere |
| Shield Sage: force shields, enchant bursts, taunts | all three: "Force Shield", "Force Burst"/"Element Change", "Attract"/"Force Anchor" |
| Seeker: rope grapple swinging, twin daggers, explosive traps | rope "Throw Rope"/"Ensnare"/"Sliding Rope" (no swinging in the text), daggers (a pair: UNKNOWN), "Powder Charge"/"Toss and Trigger" |

## DDO vocations in Dark Arisen

The animations port (`riftstone lmt convert` / `riftstone port`, `docs/animation.md`): DDO's player
motions drive 65 joints, 57 of which are the same joint in DDDA's player body; per vocation 80-88% of the
tracks sit on identical joints and the rest on the weapon attachment joints (150-154) and joint 55, which
the two games rig differently (`--drop-bones 55,150-154`). Skills, attack params and key commands are
DDO's own classes (`cJobNN*`, `rAttackParam`, `rKeyCommand`): Dark Arisen has no loader for them, so a
DDO vocation *playing* in DDDA needs native work (a plugin that drives DDDA's action system) -- the data
side is readable, the runtime side is UNKNOWN / not started.

## UNKNOWN

- The meanings of the unnamed `.csd`/`.nsd` fields, `jobbase`, `jlt2` and `dja` values, `pw_act_sw_*.pas`.
- Ability <-> job: `.abd` groups abilities 12 at a time in the server's job order -- an inference, no
  stored field.
- Whether pawns load `rom/jobNN`.
- Everything in game.
