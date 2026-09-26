"""Learn-more help in English and Japanese: Studio's tabs, every format Riftstone edits, and the fields
that matter in them.

Every sentence here comes from a measurement or the game's own code (docs/formats.md, docs/animation.md,
docs/ddo-vocations.md, docs/world-map.md); what nobody has measured says UNKNOWN. Studio serves it
(route ``help``: the editor shows the file's format and the field under the cursor), and so does
``riftstone help <topic>``.

A topic is a tab (``tab:explore``), a format extension (``gpl``, ``lmt``, ``ajp-ddo``) or ``xfs`` for
the XFS parameter files; ``topic_for`` maps a resource path or a YAML ``riftstone:`` tag to its topic.
"""
from __future__ import annotations

import re

T = dict  # {"en": ..., "ja": ...}


def _t(en: str, ja: str) -> T:
    return {"en": en, "ja": ja}


TABS: dict[str, dict] = {
    "tab:home": {
        "title": _t("Home", "ホーム"),
        "body": _t(
            "Where to start: the plugins and mods in the game now, the tools for assets, and the current game with its "
            "Launch button. Launch starts Dark Arisen through Steam, or Online through the DDO toolkit's Play Solo (the "
            "local server first). Settings opens the loader's settings.",
            "はじめの画面です：いまゲームに入っているプラグインと MOD、アセット用のツール、そして起動ボタン付きの現在の"
            "ゲーム。起動はダークアリズンを Steam 経由で、オンラインは DDO ツールキットの Play Solo（先にローカルサーバー）"
            "で開始します。設定はローダーの設定を開きます。")},
    "tab:explore": {
        "title": _t("Assets", "アセット"),
        "body": _t(
            "Search every resource of the game by name or extension (\"wolf\", \".lot\", \"em0100.shl\"), open it in "
            "its best view (YAML when Riftstone can edit it, a structured read-out otherwise), and copy it into a mod. "
            "Nothing here changes the game: extracting writes into a mod folder only.",
            "ゲーム内のすべてのリソースを名前や拡張子（「wolf」「.lot」「em0100.shl」など）で検索し、最適な表示で開き"
            "ます（Riftstone が編集できる形式は YAML、それ以外は構造化された一覧）。MOD へのコピーも可能です。"
            "このタブの操作でゲーム本体が変更されることはありません。抽出したファイルは MOD フォルダーにのみ書き込まれます。")},
    "tab:world": {
        "title": _t("World", "ワールド"),
        "body": _t(
            "The world as the game builds it: stage -> group lists -> groups -> layouts -> placements, with enemy and room "
            "names from the game's own text. \"Encounter\" adds N of an enemy as a new group using the game's horde "
            "mechanism (a spawn cap with respawn type 5); where the stage has a navigation mesh, a walking enemy's spawn "
            "points stand on it, reached on foot from the spot. \"Dungeon\" lays a whole mission (fights, a horde, a "
            "guardian, a boss) on the stage's walkable ground with the stage's own enemies and checks every spawn point "
            "(docs/level-synthesis.md). How a new encounter plays in game: UNKNOWN until tried.",
            "ゲームが実際に構築する順序でワールドを表示します：ステージ → グループリスト → グループ → レイアウト → 配置。"
            "敵名や部屋名はゲーム内のテキストから取得しています。「エンカウント」は、ゲーム本来の群れの仕組み"
            "（出現上限＋リスポーンタイプ 5）を使って、指定した敵を N 体、新しいグループとして追加します。"
            "ステージにナビゲーションメッシュがある場合、歩く敵の出現地点はその上の、指定地点から歩いて行ける場所に"
            "置かれます。「ダンジョン」は、ステージ自身の敵で一連のミッション（戦闘、群れ、守護者、ボス）をステージの"
            "歩ける地面に配置し、すべての出現地点を検証します（docs/level-synthesis.md）。"
            "追加したエンカウントのゲーム内での挙動は、実際に試すまで不明（UNKNOWN）です。")},
    "tab:skins": {
        "title": _t("Monsters", "モンスター"),
        "body": _t(
            "Between the games: every monster family of Online and of Dark Arisen with its counterpart in the other game, "
            "measured joint by joint (same body, same skeleton, partial, none). A same-body pair converts either way: the "
            "source's model, rebuilt materials and textures replace the target's, and the target's own motions move it "
            "(docs/monsters.md). Enemy skins: chosen placements wear another texture set (for example Online's White, "
            "Shadow and Blaze chimeras on Dark Arisen's chimera); the enemy_skins plugin makes the marked placements load "
            "it. Skin numbers are shared by every mod.",
            "ゲーム間：オンラインとダークアリズンの全モンスター系統と、もう一方のゲームでの対応を関節ごとに計測して表示します"
            "（同じ体・同じ骨格・一部・なし）。同じ体の組はどちらの向きにも変換でき、元のモデル・再構築したマテリアル・"
            "テクスチャが相手のものを置き換え、相手自身のモーションで動きます（docs/monsters.md）。敵スキン：指定した配置の"
            "敵に別のテクスチャセットを適用します（例：オンラインのホワイト／シャドウ／ブレイズ・キマイラを DDDA の"
            "キマイラに）。enemy_skins プラグインが印の付いた配置でそれを読み込ませます。スキン番号はすべての MOD で"
            "共有されます。")},
    "tab:mods": {
        "title": _t("Mods", "MOD 一覧"),
        "body": _t(
            "The library: the loader, its plugins (on or off, with their settings and logs) and your mod projects. Each "
            "mod's Files panel says what every file does: adds something new (never clashes) or changes a game resource "
            "(clashes with another mod changing the same one, which it names). Install rebuilds only the archives your "
            "files touch and keeps the originals; uninstall and restore are hash-checked. Plugins load when the game "
            "starts, so switching one or changing its settings takes effect at the next start.",
            "ライブラリです：ローダー、そのプラグイン（オン・オフ、設定、ログ）、そして MOD プロジェクト。各 MOD の"
            "「ファイル」パネルには、各ファイルの役割が表示されます：新規追加（競合しません）か、ゲームのリソースの変更"
            "（同じリソースを変更する他の MOD と競合し、その MOD 名を表示します）です。インストール時は、変更に関わる"
            "アーカイブだけを再構築し、元のファイルは保管されます。アンインストールと復元はハッシュで検証されます。"
            "プラグインはゲーム起動時に読み込まれるため、オン・オフや設定の変更は次回の起動から有効になります。")},
    "tab:game": {
        "title": _t("Diagnostics", "診断"),
        "body": _t(
            "The game Riftstone found and what it is doing: while it runs with the loader, the same readings as the in-game "
            "F10 panel (enemies active against the limit, address space against the loader's guard, the plugins, frame "
            "rate, stage). Below: the build, the loader, which archives Riftstone changed and whether anything else "
            "changed them since, how the last session ended, and crash reports. Close the game before installing or "
            "restoring.",
            "Riftstone が検出したゲームとその状態です。ローダー付きで実行中は、ゲーム内の F10 パネルと同じ情報（上限に"
            "対する出現中の敵の数、ローダーの警戒ラインに対するアドレス空間、プラグイン、フレームレート、ステージ）を"
            "表示します。その下に、ビルド、ローダー、Riftstone が変更したアーカイブとその後に他のツールが変更して"
            "いないか、前回のセッションの終わり方、クラッシュレポートがあります。インストールや復元の前にゲームを"
            "終了してください。")},
    "tab:guide": {
        "title": _t("Guide", "ガイド"),
        "body": _t(
            "How Riftstone works, step by step, and the legal notice: an unofficial fan tool that ships no game data and "
            "edits only a copy you own.",
            "Riftstone の使い方の手順と法的な注意事項です。本ツールは非公式のファンツールであり、ゲームデータを"
            "同梱せず、あなたが所有するコピーのみを編集します。")},
}

FORMATS: dict[str, dict] = {
    "nav": {
        "title": _t("Navigation mesh (.nav)", "ナビゲーションメッシュ（.nav）"),
        "body": _t(
            "Where a stage's AI walks: triangles linked across their edges, each link's cost the distance between the two "
            "triangles' centres in metres (Dark Arisen 41 files, Online 332, all rebuilt byte for byte). A stage may load "
            "another stage's mesh: eleven do (421, 423, 424 and 425 load 420's, for one). The game's walkers stand "
            "on it (median 0 cm). Riftstone reads it to put encounters on walkable ground and to lay whole dungeons "
            "(riftstone dungeon, docs/level-synthesis.md); the file itself is kept as it is.",
            "ステージの AI が歩ける場所です：辺でつながった三角形の集まりで、各つながりのコストは 2 つの三角形の中心間の"
            "距離（メートル）です（DDDA 41 ファイル、DDO 332 ファイル、すべてバイト単位で再構築）。ステージによっては"
            "別のステージのメッシュを読み込みます：11 ステージがそうです（たとえば 421、423、424、425 は 420 のもの）。ゲーム内で歩く敵は"
            "このメッシュの上に配置されています（中央値 0 cm）。Riftstone はこれを読み、エンカウントを歩ける地面に置き、"
            "ダンジョン全体を配置します（riftstone dungeon、docs/level-synthesis.md）。ファイル自体はそのまま保持します。")},
    "xfs": {
        "title": _t("XFS parameter file", "XFS パラメーターファイル"),
        "body": _t(
            "The engine's serialized objects: AI state machines, goal planning, player parameters, shells (projectiles), "
            "cameras, shops, quests and more (44 types in Dark Arisen, all 4,601 distinct files byte-exact; 5,907 in "
            "Online). Edit the values; keep the structure. Floats are kept as their exact 32-bit value, so an untouched "
            "file rebuilds byte for byte.",
            "エンジンがシリアライズしたオブジェクトです：AI ステートマシン、ゴールプランニング、プレイヤーパラメーター、"
            "シェル（射出物）、カメラ、ショップ、クエストなど（DDDA で 44 種類、4,601 ファイルすべてがバイト単位で一致。"
            "DDO では 5,907 ファイル）。値は編集できますが、構造は変えないでください。浮動小数点数は 32 ビットの値を"
            "そのまま保持するため、変更していないファイルは完全に同一のバイト列で再構築されます。")},
    "fsm": {
        "title": _t("AI state machine (.fsm)", "AI ステートマシン（.fsm）"),
        "body": _t(
            "rAIFSM: states, the actions each runs, and transitions guarded by condition trees. `riftstone fsm` prints a "
            "readable version and ends with what the game's own rules make of it (`--check` for just that). Each frame "
            "the states marked 'entered from any state' are tried first, then the current state's links in order; the "
            "first whose condition holds wins. A link with no condition, or with a condition id the file lacks, is "
            "never taken, and a link to an id no state has does nothing (and the links after it wait). Operators: 0 "
            "(no operands) always, 1 is set, 2 not, 3 ==, 4 !=, 5 <, 6 <=, 7 >, 8 >=, 9 all bits, 10 any bit, 16 and, "
            "17 or; confirmed by running the game's own code (native/fsm_exec).",
            "rAIFSM：各ステート、そのステートで実行されるアクション、条件ツリーで判定される遷移で構成されます。"
            "`riftstone fsm` で読みやすい形式に変換して表示し、最後にゲーム自身の規則による検査結果を表示します"
            "（検査だけなら `--check`）。毎フレーム、まず「どのステートからでも遷移」の印が付いたステートを、次に現在の"
            "ステートのリンクを順に調べ、条件が最初に成立したものが選ばれます。条件のないリンクや、ファイルにない条件 ID の"
            "リンクは決して選ばれず、存在しない ID へのリンクは何も起こしません（その後のリンクも待たされます）。"
            "演算子：0（オペランドなし）常に真、1 フラグ成立、2 否定、3 ==、4 !=、5 <、6 <=、7 >、8 >=、"
            "9 全ビットを含む、10 いずれかのビットを含む、16 AND、17 OR。ゲーム自身のコードを実行して確認済み"
            "（native/fsm_exec）。"),
        "fields": {
            "mOperator": _t("Condition operator: 0 always, 1 is set, 2 not, 3 ==, 4 !=, 5 <, 6 <=, 7 >, 8 >=, 9 all bits, "
                            "10 any bit, 16 and, 17 or. With no operands any operator is true; 0 and 11-15 with operands "
                            "are false. Over two integers 16 is bitwise, and the first operand's type decides a comparison "
                            "(run in the game's own code).",
                            "条件の演算子：0 常に真、1 フラグ成立、2 否定、3 ==、4 !=、5 <、6 <=、7 >、8 >=、9 全ビットを含む、"
                            "10 いずれかのビットを含む、16 AND、17 OR。オペランドがなければどの演算子も真、0 と 11〜15 は"
                            "オペランドがあると偽。整数どうしの 16 はビット演算で、比較の型は最初のオペランドで決まります"
                            "（ゲーム自身のコードで実行して確認）。"),
            "mExistCondition": _t("Whether the link has a condition. The game skips a link without one: it is never taken.",
                                  "リンクに条件があるかどうか。条件のないリンクはゲームが飛ばすため、決して選ばれません。"),
        }},
    "gmd": {
        "title": _t("Text (.gmd)", "テキスト（.gmd）"),
        "body": _t(
            "Dialogue, item names and descriptions, menus. Dark Arisen: 7,954 files in 7 languages (version 1.2.1); "
            "Online: 5,686 (1.3.2). Other data finds a message by its position, so keep message ids where they are; "
            "`riftstone text add` appends a new line in every language at one id.",
            "会話、アイテム名と説明、メニューのテキストです。DDDA：7 言語で 7,954 ファイル（バージョン 1.2.1）、"
            "DDO：5,686 ファイル（1.3.2）。他のデータはメッセージを位置（番号）で参照するため、メッセージ ID の順序は"
            "変えないでください。`riftstone text add` を使うと、すべての言語の同じ ID に新しい行を追加できます。")},
    "lot": {
        "title": _t("Spawn / object layout (.lot)", "配置レイアウト（.lot）"),
        "body": _t(
            "Where enemies, NPCs and objects stand. Decoded completely with DDDA.exe's own grammar (74 record classes, "
            "every field by its engine name; all 6,209 files byte-exact). Layout `st<S>_<X>m<Z>n_<t>N` is group N of the "
            "stage's group list `st<S>_<t>.gpl` in map cell (X, Z). Record ids are ids, not positions: a copy takes "
            "max+1, a removal renumbers nothing, ids stay in 0..1023.",
            "敵・NPC・オブジェクトの配置です。DDDA.exe 自身の読み込み処理から完全に解析済みです（74 種類のレコード、"
            "全フィールドをエンジン上の名前で表示。6,209 ファイルすべてバイト単位で一致）。レイアウト "
            "`st<S>_<X>m<Z>n_<t>N` は、マップセル (X, Z) にある、ステージのグループリスト `st<S>_<t>.gpl` の"
            "グループ N です。レコード ID は位置ではなく識別子です：コピーすると最大値＋1 が割り当てられ、削除しても"
            "番号は振り直されません。ID は 0〜1023 の範囲に収めてください。")},
    "lot-ddo": {
        "title": _t("Online layout (.lot v138)", "DDO レイアウト（.lot v138）"),
        "body": _t(
            "Dragon's Dogma Online's layouts, read with DDO.exe's own loaders (52 kinds, 95% of fields by engine name; "
            "22,983 files byte-exact). In Online the server decides spawns, shops and drops; these are the client's "
            "placements.",
            "DDO のレイアウトです。DDO.exe 自身の読み込み処理で解析しています（52 種類、フィールドの 95% を"
            "エンジン上の名前で表示。22,983 ファイルがバイト単位で一致）。DDO では出現・ショップ・ドロップはサーバーが"
            "決定します。これはクライアント側の配置データです。")},
    "gpl": {
        "title": _t("Enemy group list (.gpl)", "敵グループリスト（.gpl）"),
        "body": _t(
            "A stage's groups: which enemies (mUnitKindList), when they appear, how many in total, and where they live. "
            "194 files byte-exact. mGroupList is the 295-slot group table, so group numbers are 0..294.",
            "ステージのグループ定義です：どの敵か（mUnitKindList）、いつ出現するか、合計何体か、どの範囲にいるか。"
            "194 ファイルがバイト単位で一致。mGroupList は 295 スロットのグループ表なので、グループ番号は 0〜294 です。"),
        "fields": {
            "mSetCountMax": _t("The group's total output. With respawn type 5 its placements are spawn points it refills "
                               "(stage 330 group 35: 100 goblins from 7 points). The on-screen limit is still the exe's "
                               "(about 10 enemies).",
                               "グループが出現させる合計数です。リスポーンタイプ 5 の場合、配置はその数に達するまで補充される"
                               "出現地点になります（ステージ 330 のグループ 35：7 地点からゴブリン 100 体）。同時表示数の上限は"
                               "実行ファイル側の制限（約 10 体）のままです。"),
            "mAppearBgn": _t("First story scenario the group appears in. The Dragon-kill boundary is scenario 7800.",
                             "グループが出現し始めるストーリーシナリオ番号です。ドラゴン討伐の境目はシナリオ 7800 です。"),
            "mAppearEnd": _t("Last story scenario the group appears in (pre-Dragon groups end at 7799).",
                             "グループが出現する最後のシナリオ番号です（ドラゴン討伐前のグループは 7799 で終わります）。"),
            "mRspnType": _t("Respawn type. 5 = a horde capped by mSetCountMax (21 groups). 1 (767 groups) and 0 (330, "
                            "once) are common; what 2, 3, 4 and 6 do exactly: UNKNOWN.",
                            "リスポーンタイプ。5 は mSetCountMax を上限とする群れ（21 グループ）。1（767 グループ）と"
                            "0（330 グループ、1 回のみ）が一般的です。2・3・4・6 の正確な動作は不明（UNKNOWN）です。"),
            "mUnitKindList": _t("The enemies of the group (model name + belong flag). The exe reads at most 3 kinds per "
                                "group; Riftstone's encounters use one kind per group.",
                                "グループに含まれる敵（モデル名＋所属フラグ）です。実行ファイルは 1 グループにつき 3 種類まで"
                                "読み込みます。Riftstone のエンカウントは 1 グループ 1 種類です。"),
            "mIsEmGroupLink": _t("With mLinkEmGroup = M the group waits for enemy group M. Used by 9 NPC/object groups "
                                 "only, never by enemies.",
                                 "mLinkEmGroup = M と組み合わせると、このグループは敵グループ M を待ちます。NPC とオブジェクトの"
                                 "9 グループだけが使っており、敵グループでは使われていません。"),
        }},
    "itl": {
        "title": _t("Item list (.itl)", "アイテムリスト（.itl）"),
        "body": _t(
            "Item N is record N of etc/item/itemList.itl and message N of the itemName/itemInfo texts. \"Unknown Item\" "
            "with price 0 marks a free slot (175 of 1,901). `riftstone items new` fills one.",
            "アイテム N は etc/item/itemList.itl のレコード N で、itemName/itemInfo テキストのメッセージ N に対応します。"
            "価格 0 の「Unknown Item」は空きスロットです（1,901 件中 175 件）。`riftstone items new` で空きスロットに"
            "新しいアイテムを登録できます。"),
        "fields": {
            "mAttack": _t("Strength (physical attack), 0..2047. Enhancement levels change it through the item's "
                          "mLevelUpType row (riftstone items stats).",
                          "物理攻撃力（0〜2047）。強化レベルによる変化は mLevelUpType の行で決まります"
                          "（riftstone items stats）。"),
            "mMagicAttack": _t("Magick (magick attack), 0..2047.", "魔法攻撃力（0〜2047）。"),
            "mDefense": _t("Defense, 0..1023.", "物理防御力（0〜1023）。"),
            "mMagicDefense": _t("Magick Defense, 0..1023.", "魔法防御力（0〜1023）。"),
            "mShrink": _t("Stagger power (のけぞり値), 0..1023.", "のけぞり値（0〜1023）。"),
            "mBlow": _t("Knockdown power (ぶっとび値), 0..1023.", "ぶっとび値（0〜1023）。"),
            "mKind": _t("The item's kind: 7-18 weapons, 19-24 and 27 armour, 25 accessories. It picks the enhancement "
                        "table (LvParamWepon / Armor / Accessory), read in DDDA.exe's lookup.",
                        "アイテムの種類：7〜18 は武器、19〜24 と 27 は防具、25 はアクセサリー。強化テーブル"
                        "（LvParamWepon / Armor / Accessory）はこの値で選ばれます（DDDA.exe の処理で確認）。"),
            "mLevelUpType": _t("The row of the enhancement table this item uses at levels 1-6 (the stars and beyond). "
                               "An item made from a template keeps the template's row.",
                               "強化レベル 1〜6 で使う強化テーブルの行番号です。テンプレートから作ったアイテムは"
                               "その行を引き継ぎます。"),
            "mEnableEquipJob": _t("Which vocations can equip it: bit 1 Fighter, 2 Strider, 3 Mage, 4 Mystic Knight, "
                                  "5 Assassin, 6 Magick Archer, 7 Warrior, 8 Ranger, 9 Sorcerer (every weapon kind "
                                  "shows them so); bits 10 and 11 are set on every item.",
                                  "装備できるジョブ：ビット 1 ファイター、2 ストライダー、3 メイジ、4 ミスティックナイト、"
                                  "5 アサシン、6 マジックアーチャー、7 ウォリアー、8 レンジャー、9 ソーサラー（全武器種の"
                                  "データで確認）。ビット 10 と 11 は全アイテムで立っています。"),
            "mAlterItemNo": _t("The item this one turns into over time (Scrag of Beast -> Sour -> Rotten); -1 none.",
                               "時間経過で変化する先のアイテム（獣肉 → 腐りかけ → 腐った）。-1 はなし。"),
            "mSwordDefenseRate": _t("Slash resistance (斬耐性), signed.", "斬耐性（符号付き）。"),
            "mHitDefenseRate": _t("Strike resistance (打耐性), signed.", "打耐性（符号付き）。"),
            "mFireDefenseRate": _t("Fire resistance (火耐性), signed.", "火耐性（符号付き）。"),
        }},
    "ist": {
        "title": _t("Item sets / drop tables (.ist)", "アイテムセット・ドロップテーブル（.ist）"),
        "body": _t("Rows of [item, weight] slots; weights are percentages. Which set an enemy's own drops use: UNKNOWN.",
                   "[アイテム, 重み] のスロットの並びで、重みはパーセントです。敵固有のドロップがどのセットを使うかは"
                   "不明（UNKNOWN）です。")},
    "imx": {
        "title": _t("Recipes (.imx)", "調合レシピ（.imx）"),
        "body": _t("Combination recipes: two ingredients and the result. `riftstone items recipe` adds one.",
                   "調合レシピです：素材 2 つと完成品。`riftstone items recipe` で追加できます。")},
    "tex": {
        "title": _t("Texture (.tex)", "テクスチャ（.tex）"),
        "body": _t(
            "Every armour, monster, face and UI picture. Export to .dds, edit in any image tool, import back; an "
            "unedited round trip is byte for byte (11,199 flat textures). Online uses revision 0x9D with the same pixel "
            "layout. Presets (`riftstone tex preset`) make Frost, Lava, Abyssal and Weathered variants.",
            "防具・モンスター・顔・UI のすべての画像です。.dds に書き出して任意の画像ツールで編集し、読み込み直せます。"
            "編集していない往復はバイト単位で一致します（平面テクスチャ 11,199 件）。DDO は同じピクセル配置で"
            "リビジョン 0x9D を使います。プリセット（`riftstone tex preset`）で、フロスト・溶岩・深淵・風化の"
            "バリエーションを作成できます。")},
    "mrl": {
        "title": _t("Material (.mrl)", "マテリアル（.mrl）"),
        "body": _t(
            "Which shader each material of a model uses and which textures it binds (27,673 bindings in Dark Arisen). "
            "`mrl retex` repoints a texture. The two games use different material classes, so a material is rebuilt "
            "from the destination game's template when a model is ported. Per-material shader parameters: not decoded.",
            "モデルの各マテリアルが使うシェーダーと、割り当てられたテクスチャです（DDDA 全体で 27,673 箇所）。"
            "`mrl retex` でテクスチャの参照先を変更できます。2 つのゲームはマテリアルのクラスが異なるため、"
            "モデルを移植する際は移植先ゲームのテンプレートからマテリアルを作り直します。マテリアルごとの"
            "シェーダーパラメーターは未解析です。")},
    "ocl": {
        "title": _t("Object collision (.ocl)", "オブジェクトの当たり判定（.ocl）"),
        "body": _t(
            "Every field editable, by its engine name and in the order DDDA.exe reads it: groups of collision shapes "
            "(mShape 0 capsule between the two points, 1 sphere at point 0, 4 an inset capsule; mJoint0/mJoint1 the "
            "joints, mOffset0/mOffset1 the points, mRadius), the attacks a hit carries and the index tying a motion to "
            "both. All 378 Dark Arisen files byte-exact (7,309 shapes, 5,330 attacks).",
            "すべてのフィールドを、エンジンの名前と DDDA.exe が読み込む順序どおりに編集できます：当たり判定の形状のグループ"
            "（mShape 0 は 2 点間のカプセル、1 は点 0 の球、4 は端を縮めたカプセル。mJoint0/mJoint1 はジョイント、"
            "mOffset0/mOffset1 は各点、mRadius は半径）、ヒットが与える攻撃、そしてモーションを両者に結び付けるインデックス。"
            "DDDA の 378 ファイルすべてがバイト単位で一致します（形状 7,309 個、攻撃 5,330 個）。")},
    "prp": {
        "title": _t("Enemy / character parameters (.prp)", "敵・キャラクターのパラメーター（.prp）"),
        "body": _t(
            "A 12-byte PRPZ wrapper around an XFS document: attack, defence, magick attack/defence, weight, size, scale "
            "value, the elemental and status resistances, flinch/knockback guards, EXP. Field names are the developers' "
            "Japanese strings; `riftstone open` prints the recognised stats in English. 223 files byte-exact.",
            "XFS ドキュメントを 12 バイトの PRPZ ヘッダーで包んだ形式です：攻撃力、防御力、魔法攻撃力／防御力、体重、"
            "大きさ、スケール値、属性・状態異常の耐性、のけぞり／ぶっとびガード、経験値など。フィールド名は開発時の"
            "日本語文字列のままです。`riftstone open` で主要な値を英語でも表示できます。223 ファイルがバイト単位で一致。"),
        "fields": {
            "スケール値": _t("Scale value: the enemy's size multiplier (Skeleton Brutes ship at 1.8).",
                          "スケール値：敵の大きさの倍率です（スケルトンブルートは 1.8 で出荷されています）。"),
            "攻撃力": _t("Attack.", "攻撃力。"), "防御力": _t("Defence.", "防御力。"),
            "魔法攻撃力": _t("Magick attack.", "魔法攻撃力。"), "魔法防御力": _t("Magick defence.", "魔法防御力。"),
            "経験値": _t("Experience given.", "獲得経験値。"),
        }},
    "ean": {
        "title": _t("Effect UV animation (.ean)", "エフェクト UV アニメーション（.ean）"),
        "body": _t(
            "Sprite-sheet frames for effect textures. The frame payload is kept as one block, as the engine loads it; "
            "replace the whole file to retarget an effect. Dark Arisen 0x20100924 (39 files), Online 0x20120224 (50).",
            "エフェクト用テクスチャのスプライトシートのフレームです。フレームデータはエンジンが読み込むのと同じく"
            "1 つのブロックとして保持します。エフェクトを差し替えるときはファイル全体を置き換えてください。"
            "DDDA は 0x20100924（39 ファイル）、DDO は 0x20120224（50 ファイル）です。")},
    "lmt": {
        "title": _t("Animations (.lmt)", "アニメーション（.lmt）"),
        "body": _t(
            "A motion list: up to 65,535 motions, each a set of bone tracks (rotation, position or scale of one joint), "
            "an event block and optional float tracks. Byte-exact on every file of both games; every keyframe decoded. "
            "Dark Arisen is version 66, Online 67: `riftstone lmt convert` or Studio's Port button converts between them "
            "(the largest value change is 0.000122). Tracks address joints by the model's joint id; `riftstone skeleton` "
            "compares two skeletons.",
            "モーションリストです：最大 65,535 個のモーションを持ち、各モーションはボーントラック（1 つのジョイントの"
            "回転・位置・スケール）、イベントブロック、任意の float トラックで構成されます。両ゲームの全ファイルで"
            "バイト単位で一致し、すべてのキーフレームを解析済みです。DDDA はバージョン 66、DDO は 67 で、"
            "`riftstone lmt convert` または Studio の移植ボタンで相互に変換できます（値の変化は最大 0.000122）。"
            "トラックはモデルのジョイント ID でジョイントを指定します。`riftstone skeleton` で 2 つのスケルトンを"
            "比較できます。"),
        "fields": {
            "flags": _t("0x800000 events; (flags >> 16) & 0x1F = number of float-track groups; 0x1000000 = this motion "
                        "shares an earlier motion's track array (Riftstone sets it). Bit 0: Online only, meaning UNKNOWN.",
                        "0x800000 はイベントあり。(flags >> 16) & 0x1F は float トラックのグループ数。0x1000000 は"
                        "前のモーションとトラック配列を共有していることを示します（Riftstone が自動設定）。ビット 0 は"
                        "DDO のみで、意味は不明（UNKNOWN）です。"),
            "codec": _t("Keyframe codec: 1/2 constant, 3 float vector, 4/5 16/8-bit vector, 6 14-bit quaternion, 7/14/15 "
                        "7/11/9-bit quaternion, 11/12/13 one-axis rotation (decoded differently in the two games; the "
                        "port re-encodes them).",
                        "キーフレームの圧縮方式：1/2 定数、3 float ベクトル、4/5 16/8 ビットベクトル、6 14 ビット"
                        "クォータニオン、7/14/15 7/11/9 ビットクォータニオン、11/12/13 単軸回転（2 つのゲームで解釈が"
                        "異なるため、移植時に再エンコードします）。"),
            "bone": _t("The joint id the track moves (255 = root / scene).",
                       "トラックが動かすジョイント ID です（255 はルート／シーン）。"),
        }},
    "ocl-ddo": {
        "title": _t("Online collision (.ocl COL)", "DDO の当たり判定（.ocl COL）"),
        "body": _t(
            "Dragon's Dogma Online's object collision, read with DDO.exe's loaders: an index, nodes with their hit "
            "shapes (mShape 0 sphere, 1 capsule, 2 oriented box -- confirmed from the code that builds them) and "
            "attack params. All 1,176 files byte-exact, every byte decoded; attack-param field meanings are UNKNOWN.",
            "DDO のオブジェクトの当たり判定です。DDO.exe の読み込み処理で解析しています：インデックス、判定形状を持つ"
            "ノード（mShape 0 球、1 カプセル、2 有向ボックス。形状を生成するコードで確認済み）、攻撃パラメーター。"
            "1,176 ファイルすべてがバイト単位で一致し、全バイトを解析済みです。攻撃パラメーターの各フィールドの"
            "意味は不明（UNKNOWN）です。"),
        "fields": {
            "mShape": _t("Hit shape kind: 0 sphere, 1 capsule, 2 oriented box.",
                         "判定形状の種類：0 球、1 カプセル、2 有向ボックス。"),
        }},
    "gpl-ddo": {
        "title": _t("Online group list (.gpl v70)", "DDO グループリスト（.gpl v70）"),
        "body": _t(
            "Dragon's Dogma Online's groups, read with DDO.exe's own loaders (2,210 files byte-exact): a 512-slot "
            "group table, each group's layout cells, load conditions (lot / quest / layout / set-management flags), "
            "MaxCount and area shapes. There is no enemy list: Online's server decides spawns, so edit spawns "
            "through the server (riftstone world / encounter --game ddo).",
            "DDO のグループ定義です。DDO.exe 自身の読み込み処理で解析しています（2,210 ファイルがバイト単位で"
            "一致）：512 スロットのグループ表、各グループのレイアウトセル、読み込み条件（抽選／クエスト／レイアウト／"
            "セット管理のフラグ）、MaxCount、エリア形状。敵のリストはありません。DDO では出現する敵をサーバーが"
            "決めるため、出現の編集はサーバー側で行ってください（riftstone world／encounter --game ddo）。"),
        "fields": {
            "MaxCount": _t("最大数 (\"max count\"), where Dark Arisen keeps its spawn cap; DDO.exe keeps its low 8 bits. "
                           "What it limits in Online, where the server spawns enemies: UNKNOWN.",
                           "最大数。DDDA が出現上限を持つ位置にあり、DDO.exe は下位 8 ビットのみ使います。サーバーが敵を"
                           "出現させる DDO で何を制限しているかは不明（UNKNOWN）です。"),
            "mGroup": _t("The group number (9 bits); it equals the group's slot in every vanilla group.",
                         "グループ番号（9 ビット）です。すべての標準グループでスロット番号と一致します。"),
        }},
    "wep": {
        "title": _t("Weather effect colours (.wep)", "天候エフェクトの色補正（.wep）"),
        "body": _t(
            "Colour correction by time of day: lists of rows, each with a time, a colour [r, g, b, a] and blend, "
            "intensity, environment-map and shadow values; the game blends the two rows around the current hour. "
            "Dark Arisen (version 1) has six lists (CORRECT_TYPE_01..09) with mHour / mMinute; Online (version 3) "
            "has seven with mTime in milliseconds (the YAML comment shows HH:MM). All 57 + 83 files rebuild byte for "
            "byte. Which scene each list colours: UNKNOWN.",
            "時刻ごとの色補正です。各行に時刻、色 [r, g, b, a]、ブレンド・強度・環境マップ・影の値があり、ゲームは現在"
            "時刻の前後 2 行を補間します。DDDA（バージョン 1）は 6 つのリスト（CORRECT_TYPE_01〜09）で mHour／mMinute"
            "を使い、DDO（バージョン 3）は 7 つのリストで mTime（ミリ秒。YAML のコメントに HH:MM を表示）を使います。"
            "57 + 83 ファイルすべてがバイト単位で再構築されます。各リストがどの場面の色かは不明（UNKNOWN）です。"),
        "fields": {
            "mTime": _t("Online: the row's time of day in milliseconds since midnight (every vanilla value is a whole "
                        "minute).", "DDO：その行の時刻。0 時からのミリ秒です（標準の値はすべて分単位）。"),
            "mColor": _t("The colour as bytes [r, g, b, a].", "色をバイト値 [r, g, b, a] で表します。"),
        }},
    "wfp": {
        "title": _t("Fog by time of day (.wfp)", "時刻ごとのフォグ（.wfp）"),
        "body": _t(
            "Dark Arisen's fog per weather: rows of mHour, mMinute, mDensity, mExponentDensity, mStart / mEnd (the "
            "fog distances, e.g. 1000 and 110000) and mColor [r, g, b] floats. All 12 files rebuild byte for byte.",
            "DDDA の天候ごとのフォグです。各行は mHour、mMinute、mDensity、mExponentDensity、mStart／mEnd（フォグの"
            "距離。例：1000 と 110000）、mColor [r, g, b]（float）です。12 ファイルすべてがバイト単位で再構築されます。"),
        "fields": {
            "mStart": _t("Where the fog begins (distance from the camera).", "フォグが始まる距離（カメラから）。"),
            "mEnd": _t("Where the fog is full.", "フォグが最も濃くなる距離。"),
        }},
    "sky": {
        "title": _t("Physical sky (.sky)", "物理ベースの空（.sky）"),
        "body": _t(
            "The atmosphere and the sun and moon model, with the engine's own names: wavelengths mRamda (665/555/455 "
            "nm), Earth's radius, the sun's and moon's radius and distance, the axial tilt mObliquity (23.4) and the "
            "sun and moon textures. Both games use version 6; every file rebuilds byte for byte.",
            "大気と太陽・月のモデルです（名前はエンジン自身のもの）：波長 mRamda（665/555/455 nm）、地球の半径、"
            "太陽と月の半径と距離、地軸の傾き mObliquity（23.4）、太陽と月のテクスチャ。両ゲームともバージョン 6 で、"
            "すべてのファイルがバイト単位で再構築されます。"),
        "fields": {
            "mObliquity": _t("The axial tilt in degrees (23.4, Earth's).", "地軸の傾き（度）。地球と同じ 23.4 です。"),
        }},
    "wtf": {
        "title": _t("Online fog table (.wtf)", "DDO のフォグ表（.wtf）"),
        "body": _t(
            "Online's fog by time of day: rows of mTime (ms), mStart, mEnd, mExponentDensity and mColor. DDO.exe names "
            "none of these fields; the names follow Dark Arisen's fog rows and DDO's Fog* properties (an inference). "
            "25 files, byte-exact.",
            "DDO の時刻ごとのフォグです。各行は mTime（ミリ秒）、mStart、mEnd、mExponentDensity、mColor です。"
            "DDO.exe はこれらのフィールド名を持たないため、DDDA のフォグ行と DDO の Fog* プロパティから推定した名前"
            "です。25 ファイル、バイト単位で一致。")},
    "wte": {
        "title": _t("Online weather effects (.wte)", "DDO の天候エフェクト表（.wte）"),
        "body": _t("Which weather effect (.wep) each weather id (1..3) uses. 19 files, byte-exact.",
                   "天候 ID（1〜3）ごとに使う天候エフェクト（.wep）です。19 ファイル、バイト単位で一致。")},
    "wtl": {
        "title": _t("Online weather parameters (.wtl)", "DDO の天候パラメーター（.wtl）"),
        "body": _t(
            "Per weather id: clouds and scattering (mCloudHeight, mCloudiness, mMieScattering ... named by matching "
            "DDO's cDarkSkyParam properties), the fog table and cloud models. Fields named mUnkXX: UNKNOWN. 15 files, "
            "byte-exact.",
            "天候 ID ごとの雲と散乱（mCloudHeight、mCloudiness、mMieScattering など。DDO の cDarkSkyParam の"
            "プロパティと照合して命名）、フォグ表、雲のモデルです。mUnkXX のフィールドは不明（UNKNOWN）です。"
            "15 ファイル、バイト単位で一致。")},
    "wsi": {
        "title": _t("Online stage sky (.wsi)", "DDO のステージの空（.wsi）"),
        "body": _t("A stage's skies, scheduler, star model, texture and catalog, and star / environment-map settings. "
                   "7 files, byte-exact.",
                   "ステージの空、スケジューラー、星のモデル・テクスチャ・カタログ、星と環境マップの設定です。"
                   "7 ファイル、バイト単位で一致。")},
    "wta": {
        "title": _t("Online weather table (.wta)", "DDO の天候テーブル（.wta）"),
        "body": _t(
            "Per weather id, two sets of weather-script commands: sounds (cWSCSound, random, volume), effects "
            "(cWSCEpv) and timers. A command's sound or effect id is (class id << 32) | JAMCRC(path), checked on all "
            "54 sound commands; keep them matching the path. 1 file, byte-exact.",
            "天候 ID ごとに 2 組の天候スクリプトコマンドがあります：サウンド（cWSCSound、ランダム、音量）、"
            "エフェクト（cWSCEpv）、タイマー。コマンドのサウンド／エフェクト ID は (クラス ID << 32) | JAMCRC(パス) "
            "で、54 個のサウンドコマンドすべてで確認済みです。パスと一致させてください。1 ファイル、バイト単位で一致。")},
    "lcm": {
        "title": _t("Camera list (.lcm)", "カメラリスト（.lcm）"),
        "body": _t(
            "Cutscene and skill cameras. Dark Arisen (version 3): one row per frame with eye position, target, a "
            "rotation quaternion and fov (degrees). Online (version 5): four compressed tracks per camera (position, "
            "target, up, fov) with their own codecs; editable as raw keys, each shown with its decoded value. "
            "DDDA 99 files (142,153 frames) and DDO 52 (156,591 frames) rebuild byte for byte.",
            "カットシーンやスキルのカメラです。DDDA（バージョン 3）はフレームごとに視点位置、注視点、回転"
            "クォータニオン、画角（度）を持ちます。DDO（バージョン 5）はカメラごとに 4 本の圧縮トラック（位置、注視点、"
            "上方向、画角）を独自の方式で持ち、生のキー値として編集できます（各キーに復号後の値を表示）。"
            "DDDA 99 ファイル（142,153 フレーム）と DDO 52 ファイル（156,591 フレーム）がバイト単位で再構築されます。"),
        "fields": {
            "fovtype": _t("Which angle the fov measures: 0 vertical (FOV_V), 1 horizontal (FOV_H).",
                          "画角の基準：0 は垂直（FOV_V）、1 は水平（FOV_H）です。"),
        }},
    "srq": {
        "title": _t("Sound-effect cues (.srq)", "効果音のキュー（.srq）"),
        "body": _t(
            "Which sound each cue number plays and how: the package (Dark Arisen .spc) or bank (Online .sbkr), "
            "program, volume, pan, pitch, sends, priority and limits. The file is the engine's memory image; "
            "offsets and padding are derived again on rebuild. Volumes are decibels (gain 10^(dB/20); -96 or less "
            "is silent, the same code in both games). DDDA 1,600 and DDO 2,399 files rebuild byte for byte; DDO "
            "fields named mUnkXX are UNKNOWN.",
            "キュー番号ごとに、どの音をどう鳴らすかを定義します：パッケージ（DDDA の .spc）またはバンク（DDO の "
            ".sbkr）、プログラム、音量、パン、ピッチ、センド、優先度、発音数の制限。ファイルはエンジンのメモリイメージで、"
            "オフセットとパディングは再構築時に計算し直します。音量はデシベルです（ゲイン 10^(dB/20)。-96 以下は無音。"
            "両ゲームで同じ処理）。DDDA 1,600、DDO 2,399 ファイルがバイト単位で再構築されます。DDO の mUnkXX は不明"
            "（UNKNOWN）です。"),
        "fields": {
            "mVol": _t("Volume in decibels (0 = unchanged, -96 or less = silent).",
                       "音量（デシベル）。0 で変化なし、-96 以下で無音です。"),
            "mCommand": _t("What the cue does; 4 = pick another cue from the random table (.srd).",
                           "キューの動作です。4 はランダム表（.srd）から別のキューを選びます。"),
        }},
    "stq": {
        "title": _t("Streamed cues (.stq)", "ストリーミング再生のキュー（.stq）"),
        "body": _t("Music and voice cues played from wave files: path, loop points, volume and the stream source "
                   "table. DDDA 2,632 and DDO 1,132 files rebuild byte for byte.",
                   "音楽やボイスなど、波形ファイルから再生するキューです：パス、ループ位置、音量、ストリームの"
                   "ソース表。DDDA 2,632、DDO 1,132 ファイルがバイト単位で再構築されます。")},
    "srd": {
        "title": _t("Random cue picks (.srd)", "ランダムなキューの選択（.srd）"),
        "body": _t("Sixteen (cue, weight) pairs per entry; the game rolls 1-100 through the cumulative weights. "
                   "mAllowRepeat 1 lets the same cue play twice in a row. 256 files, byte-exact.",
                   "エントリーごとに 16 組の（キュー、重み）があり、ゲームは 1〜100 の乱数で累積の重みを辿ります。"
                   "mAllowRepeat が 1 なら同じキューが連続で鳴ることを許します。256 ファイル、バイト単位で一致。")},
    "smx": {
        "title": _t("Sub-mixer (.smx)", "サブミキサー（.smx）"),
        "body": _t("Mixer fader settings (volumes, sends, EQ and reverb presets), engine field names. DDDA 49 and "
                   "DDO 35 files, byte-exact.",
                   "ミキサーのフェーダー設定（音量、センド、EQ とリバーブのプリセット）です。フィールド名はエンジン"
                   "自身のもの。DDDA 49、DDO 35 ファイル、バイト単位で一致。")},
    "spl": {
        "title": _t("Physics-sound list (.spl)", "物理サウンドのリスト（.spl）"),
        "body": _t("Physics-sound (.spr) paths and a 64-entry index table. 29 files, byte-exact; mUnk0C / mUnk10 "
                   "UNKNOWN.", "物理サウンド（.spr）のパスと 64 項目の索引表です。29 ファイル、バイト単位で一致。"
                              "mUnk0C／mUnk10 は不明（UNKNOWN）です。")},
    "sbkr": {
        "title": _t("Online sound bank (.sbkr)", "DDO のサウンドバンク（.sbkr）"),
        "body": _t("Programs and their waves (.xsew paths): a program draws one of its waves weighted by each "
                   "wave's weight (checked on all 1,734 banks: every total is the sum of its waves). Most wave "
                   "bytes and the 8-byte records: UNKNOWN.",
                   "プログラムとその波形（.xsew のパス）です。プログラムは各波形の重みに従って 1 つを選びます"
                   "（1,734 個すべてのバンクで、合計が波形の重みの和と一致することを確認）。波形のバイトの大半と "
                   "8 バイトのレコードは不明（UNKNOWN）です。")},
    "sar": {
        "title": _t("Online area sound (.sar)", "DDO のエリアのサウンド（.sar）"),
        "body": _t("Per stage area: the music numbers (normal, day, night: cues of resource1's stream request; "
                   "battle, outro: of resource3's), zones, and a sound request per ground surface. 684 files, "
                   "byte-exact.",
                   "ステージのエリアごとの設定です：BGM 番号（通常・昼・夜は resource1 のストリームリクエストの"
                   "キュー、戦闘・アウトロは resource3 のもの）、ゾーン、地面の材質ごとのサウンドリクエスト。"
                   "684 ファイル、バイト単位で一致。")},
    "epv": {
        "title": _t("Effect provider (.epv)", "エフェクトプロバイダー（.epv）"),
        "body": _t(
            "Which effects a model or skill spawns and where: indices of elements, each naming up to eight effect "
            "lists (.efl) and one 2D effect, with the joint (mJointNo), position, direction, scale, colour and "
            "loop; the motion-sync list ties a motion number and frame window to an element (the motion -> effect "
            "link). Enum names show as comments. DDDA 599 and DDO 1,660 files rebuild byte for byte; DDO's added "
            "fields (mUnkXX) are UNKNOWN.",
            "モデルやスキルが、どのエフェクトをどこに出すかを定義します：要素のインデックスがあり、各要素はエフェクト"
            "リスト（.efl）を最大 8 個と 2D エフェクトを 1 個指定し、ジョイント（mJointNo）、位置、向き、拡大率、色、"
            "ループを持ちます。モーション同期リストは、モーション番号とフレーム範囲を要素に結び付けます（モーションと"
            "エフェクトの対応）。列挙値の名前はコメントに表示します。DDDA 599、DDO 1,660 ファイルがバイト単位で"
            "再構築されます。DDO で追加されたフィールド（mUnkXX）は不明（UNKNOWN）です。"),
        "fields": {
            "mJointNo": _t("The joint the effect follows (the model's joint id).",
                           "エフェクトが追従するジョイント（モデルのジョイント ID）。"),
        }},
    "efl": {
        "title": _t("Effect list (.efl)", "エフェクトリスト（.efl）"),
        "body": _t(
            "An effect's units: each with its joint, generator (emission), particle head (Billboard, Polyline, "
            "Model, Light ... with up to three textures and an animation), life and move. Generator and particle "
            "heads are named fields; the rest of each structure is kept as bytes and its offsets are rebuilt. "
            "DDDA 4,514 and DDO 5,754 files rebuild byte for byte (about 60-65% of the bytes are named fields).",
            "エフェクトのユニットの一覧です。各ユニットはジョイント、ジェネレーター（発生）、パーティクルの先頭部"
            "（Billboard、Polyline、Model、Light など。テクスチャ最大 3 枚とアニメーション）、寿命、移動を持ちます。"
            "ジェネレーターとパーティクル先頭部は名前付きのフィールドで、それ以外はバイト列のまま保持し、オフセットは"
            "再構築時に計算し直します。DDDA 4,514、DDO 5,754 ファイルがバイト単位で再構築されます（名前付きの"
            "フィールドはバイト数の約 60〜65%）。")},
    "e2d": {
        "title": _t("2D effect (.e2d)", "2D エフェクト（.e2d）"),
        "body": _t("Screen-space effects: render-target and back-texture paths, then units. DDDA 156 and DDO 52 files "
                   "rebuild byte for byte; most unit members are UNKNOWN.",
                   "画面上のエフェクトです：レンダーターゲットと背景テクスチャのパス、続いてユニット。DDDA 156、"
                   "DDO 52 ファイルがバイト単位で再構築されます。ユニットのメンバーの大半は不明（UNKNOWN）です。")},
    "efs": {
        "title": _t("Effect strip (.efs)", "エフェクトストリップ（.efs）"),
        "body": _t("A part table, then 32-byte vertices and 8-byte records (kept as bytes); the header totals are "
                   "checked against the parts. DDDA 25 and DDO 6 files rebuild byte for byte; not editable as YAML.",
                   "パーツの表に続いて 32 バイトの頂点と 8 バイトのレコード（バイト列のまま保持）があります。"
                   "ヘッダーの合計値はパーツと照合します。DDDA 25、DDO 6 ファイルがバイト単位で再構築されます。"
                   "YAML では編集できません。")},
    "cpe": {
        "title": _t("Online enemy parameters (.cpe)", "DDO の敵パラメーター（.cpe）"),
        "body": _t(
            "rCharParamEnemy: an enemy's body and behaviour settings under the developers' own 48 Japanese "
            "field names (each with an English gloss), plus a flying block for the 63 flying enemies. Every "
            "loader read matches DDO.exe's. 288 files, byte-exact; what each value does in game: UNKNOWN.",
            "rCharParamEnemy：敵の体と挙動の設定です。フィールド名は開発者自身の日本語名 48 個（英訳付き）で、"
            "飛行する敵 63 体には飛行ブロックがあります。読み込み処理は DDO.exe と完全に一致します。"
            "288 ファイル、バイト単位で一致。各値のゲーム内での効果は不明（UNKNOWN）です。"),
        "fields": {
            "mUnk12C": _t("Eleven multipliers; the game picks entry v-1 with v (1..11) a byte of the attacker "
                          "(the vocation is a guess). Entries 2, 3, 5 and 7 are the ones most often raised.",
                          "11 個の倍率です。攻撃側の 1 バイトの値 v（1〜11）で v-1 番目を選びます（v がジョブかどうかは"
                          "推測です）。2、3、5、7 番目が最もよく上げられています。"),
        }},
    "pep": {
        "title": _t("Online pawn view of an enemy (.pep)", "DDO のポーン用の敵パラメーター（.pep）"),
        "body": _t("rAIPawnEmParam: what pawns know about an enemy; its lists look like bit masks (shown in hex). "
                   "273 files, byte-exact; field meanings UNKNOWN.",
                   "rAIPawnEmParam：ポーンが敵について持つ情報です。リストはビットマスクのようです（16 進で表示）。"
                   "273 ファイル、バイト単位で一致。フィールドの意味は不明（UNKNOWN）です。")},
    "prs": {
        "title": _t("Online region status (.prs)", "DDO の部位ステータス（.prs）"),
        "body": _t("rParentRegionStatusParam: per-region status of a monster's body parts. 269 files, byte-exact; "
                   "field meanings UNKNOWN.",
                   "rParentRegionStatusParam：モンスターの部位ごとのステータスです。269 ファイル、バイト単位で一致。"
                   "フィールドの意味は不明（UNKNOWN）です。")},
    "osp": {
        "title": _t("Online status ailments (.osp)", "DDO の状態異常パラメーター（.osp）"),
        "body": _t("rOcdStatusParamRes: status-ailment parameters under their Japanese names (異常名称 counts up from "
                   "1). 236 files, byte-exact.",
                   "rOcdStatusParamRes：状態異常のパラメーターです（日本語のフィールド名。異常名称は 1 から順に"
                   "並びます）。236 ファイル、バイト単位で一致。")},
    "sti": {
        "title": _t("Online stage info (.sti)", "DDO のステージ情報（.sti）"),
        "body": _t("rStageInfo: a stage's settings and its scheduler, navigation mesh, occluder, start positions, "
                   "location data and zones (one class per slot in every file). The stage number comes from the "
                   "file's name, not its bytes. 479 files, byte-exact.",
                   "rStageInfo：ステージの設定と、スケジューラー、ナビメッシュ、オクルーダー、開始位置、ロケーション"
                   "データ、ゾーンの参照です（どのファイルでもスロットごとに同じクラス）。ステージ番号はデータではなく"
                   "ファイル名から決まります。479 ファイル、バイト単位で一致。")},
    "sal": {
        "title": _t("Online adjoining stages (.sal)", "DDO の隣接ステージ一覧（.sal）"),
        "body": _t("rStageAdjoinList: which stages connect to this one, and where. 457 files, byte-exact (mUnk90 is "
                   "the stage number in 453 of them).",
                   "rStageAdjoinList：このステージにつながるステージとその位置です。457 ファイル、バイト単位で一致"
                   "（うち 453 ファイルで mUnk90 がステージ番号）。")},
    "evtr": {
        "title": _t("Online event resources (.evtr)", "DDO のイベントリソース表（.evtr）"),
        "body": _t("rEventResTable: the resources an event loads, each as (type id << 32) | JAMCRC(path); 5,213 of "
                   "7,474 resolve to archive entries, the rest carry type 0x31897EE8 (not a DDO.exe class, "
                   "UNKNOWN). 362 files, byte-exact.",
                   "rEventResTable：イベントが読み込むリソースの一覧で、各項目は (型 ID << 32) | JAMCRC(パス) です。"
                   "7,474 件中 5,213 件がアーカイブ内のファイルに対応し、残りは型 0x31897EE8（DDO.exe のクラスに"
                   "ない。不明）です。362 ファイル、バイト単位で一致。")},
    "ndp": {
        "title": _t("Online named enemy parameters (.ndp)", "DDO の名前付き敵パラメーター（.ndp）"),
        "body": _t(
            "rNamedParam (param/named_param, 2,366 records): per-enemy multipliers in percent (HP, EXP, attack, "
            "defence, endurances). The local server sends only a record's id with each spawn; the client finds "
            "it through an id table it builds when the game sets up, and its name through the label "
            "namedparam_<id> in "
            "named_param.gmd. Byte-exact, and equal to the server's named_param.ndp.json field for field. "
            "riftstone ddo solo adds solo twins of every record (docs/ddo-solo.md).",
            "rNamedParam（param/named_param、2,366 レコード）：敵ごとの倍率（％）です（HP、経験値、攻撃、防御、"
            "耐性）。ローカルサーバーは出現ごとにレコードの ID だけを送り、クライアントは初期化時に作る ID 表で"
            "レコードを、named_param.gmd のラベル namedparam_<ID> で名前を探します。バイト単位で一致し、"
            "サーバーの named_param.ndp.json ともフィールド単位で一致します。riftstone ddo solo は全レコードに"
            "ソロ用の双子を加えます（docs/ddo-solo.md）。"),
        "fields": {
            "mID": _t("The id the server sends (NamedEnemyParamsId). Keep the largest id off a multiple of 4: the "
                      "client's id table has (largest + 3) & ~3 slots.",
                      "サーバーが送る ID（NamedEnemyParamsId）。最大の ID を 4 の倍数にしないでください。"
                      "クライアントの ID 表は（最大 + 3）& ~3 個の枠しかありません。"),
            "mHpRate": _t("HP in percent (100 = unchanged; 0 loads as 1).",
                          "HP の倍率（％。100 で変化なし。0 は 1 として読み込まれます）。"),
            "mExperience": _t("EXP in percent; the server computes exp * (this / 100) in whole numbers, so 150 "
                              "gives the same as 100.",
                              "経験値の倍率（％）。サーバーは exp * (この値 / 100) を整数で計算するため、150 と"
                              " 100 は同じ結果になります。"),
        }},
    "fca": {
        "title": _t("Facial animation (.fca)", "表情アニメーション（.fca）"),
        "body": _t(
            "Lip-sync and face curves: 14 tracks, each a default value and keys (frame, interpolation, value, "
            "tangents; MtFCurve's own names). Interpolation 0 default, 1 step, 2 linear, 3 Bezier, 4 Hermite; "
            "every vanilla key is linear. 10,133 Dark Arisen files (1.68 M keys) rebuild byte for byte; Online "
            "has none. Which face control each track drives is set by the .fcp pattern file (UNKNOWN here).",
            "口パクと表情のカーブです：14 本のトラックがあり、それぞれ既定値とキー（フレーム、補間、値、接線。"
            "名前は MtFCurve 自身のもの）を持ちます。補間は 0 既定、1 ステップ、2 線形、3 ベジェ、4 エルミート"
            "で、標準のキーはすべて線形です。DDDA の 10,133 ファイル（168 万キー）がバイト単位で再構築されます。"
            "DDO にはありません。各トラックがどの表情を動かすかは .fcp で決まります（ここでは不明）。")},
    "mss": {
        "title": _t("NPC conversations (.mss)", "NPC の会話（.mss）"),
        "body": _t(
            "Dark Arisen: conversations of up to 15 lines (message, motion, face, a GSF or quest condition) and "
            "6 choices with jumps, engine field names. Both loaders write a third 15-entry array over "
            "mQuestNo_msg, so quest-flag conditions are never checked (8 of 8,676 conversations set one; in game "
            "UNKNOWN). Online (\"mgst\"): groups and lines. DDDA 830 and DDO 3,187 files rebuild byte for byte.",
            "DDDA：最大 15 行（メッセージ、モーション、表情、GSF またはクエストの条件）と、ジャンプ先付きの"
            "選択肢 6 個からなる会話です（フィールド名はエンジン自身のもの）。両方の読み込み処理が 3 つ目の 15 項目"
            "配列で mQuestNo_msg を上書きするため、クエストフラグの条件は判定されません（8,676 件中 8 件が設定。"
            "ゲーム内では不明）。DDO（\"mgst\"）：グループと行。DDDA 830、DDO 3,187 ファイルがバイト単位で"
            "再構築されます。")},
    "msl": {
        "title": _t("Message serials (.msl)", "メッセージのシリアル一覧（.msl）"),
        "body": _t("A list of message numbers (mNo). 752 Dark Arisen files, byte-exact; what they index: UNKNOWN.",
                   "メッセージ番号（mNo）の一覧です。DDDA の 752 ファイル、バイト単位で一致。何を指しているかは"
                   "不明（UNKNOWN）です。")},
    "sdl": {
        "title": _t("Scheduler (.sdl)", "スケジューラー（.sdl）"),
        "body": _t(
            "Timelines: tracks (14 kinds in Dark Arisen, 16 in Online, each with its value size) of keys, a "
            "24-bit frame and a mode (0 hold, 2 trigger, 3 linear, 5 curve, 1 and 4 count up). Names and paths "
            "are plain text; the string table is rebuilt exactly as the game's writer does (reproduced on all "
            "2,105 files). DDDA 624 and DDO 1,481 files rebuild byte for byte.",
            "タイムラインです：トラック（DDDA 14 種、DDO 16 種。種類ごとに値のサイズが異なる）がキーを持ち、各キーは "
            "24 ビットのフレームとモード（0 保持、2 トリガー、3 線形、5 カーブ、1 と 4 はカウントアップ）です。名前"
            "とパスは普通のテキストで、文字列テーブルはゲームの書き出し処理と同じ規則で作り直します（2,105 ファイル"
            "すべてで再現）。DDDA 624、DDO 1,481 ファイルがバイト単位で再構築されます。")},
    "zon": {
        "title": _t("Stage zones (.zon)", "ステージのゾーン（.zon）"),
        "body": _t(
            "A stage's zones: layouts with their shapes (all 12 ShapeInfo shapes, engine names), group "
            "managers, grids and bounding boxes, and an embedded pool of XFS objects kept as exact bytes (hex). "
            "DDDA 477 and DDO 1,698 files rebuild byte for byte; mUnk fields and ContentsNum: UNKNOWN.",
            "ステージのゾーンです：形状付きのレイアウト（ShapeInfo の 12 形状すべて。名前はエンジン自身のもの）、"
            "グループマネージャー、グリッド、バウンディングボックス、そして埋め込まれた XFS オブジェクト群"
            "（正確なバイト列として 16 進で保持）。DDDA 477、DDO 1,698 ファイルがバイト単位で再構築されます。"
            "mUnk のフィールドと ContentsNum は不明（UNKNOWN）です。")},
    "arc": {
        "title": _t("Archive reference (ARCS)", "アーカイブ参照（ARCS）"),
        "body": _t("The list of another archive's resources (name hash + type): the archive dependency graph behind "
                   "`riftstone world deps`.",
                   "別のアーカイブが持つリソースの一覧（名前のハッシュ＋種類）です。`riftstone world deps` の"
                   "アーカイブ依存グラフの元になっています。")},
}

# The flat tables (flat.py): one line each, plus fields where their meaning is measured.
_FLAT = {
    "ajp": ("Enemy stat adjustments (float array)", "敵ステータスの補正値（float 配列）"),
    "ajp-ddo": ("Online's stat adjustments: no magic, version 0x100, a float array (65 files: enemy params and the "
                "player's baseStatus)", "DDO のステータス補正値：マジックなし、バージョン 0x100、float 配列"
                                       "（65 ファイル：敵のパラメーターとプレイヤーの baseStatus）"),
    "irp": ("Random-drop value ranges", "ランダムドロップの値の範囲"),
    "itemlv": ("Equipment stat gain per enhancement level", "強化段階ごとの装備ステータス上昇量"),
    "bed": ("Character creator: body edit markers", "キャラクタークリエイト：体型編集のマーカー"),
    "fed": ("Character creator: face edit markers", "キャラクタークリエイト：顔編集のマーカー"),
    "hed": ("Character creator: body-slider limits", "キャラクタークリエイト：体型スライダーの範囲"),
    "hpe": ("Character creator: per-part scale and offset", "キャラクタークリエイト：部位ごとのスケールとオフセット"),
    "edp": ("A pawn's whole appearance and equipment", "ポーンの外見と装備のすべて"),
    "cit": ("Cursed-item reward tables", "呪われたアイテムの報酬テーブル"),
    "skl": ("Vocation skill and ability unlocks", "ジョブのスキル・アビリティの習得条件"),
    "nnl": ("NPC ledger", "NPC 台帳"),
    "amr": ("Armour model table", "防具モデルの表"), "aor": ("Armour parts hidden by other parts", "他の部位で隠れる防具パーツ"),
    "atr": ("Armour table", "防具テーブル"), "qlv": ("Equipment upgrade material costs", "装備強化に必要な素材"),
    "gfd": ("GUI font metrics", "GUI フォントの字形情報"),
    "eap": ("Which enemy AI action fires under which conditions", "敵 AI の行動と、その発動条件"),
    "sap": ("Stage AI actions, gated by scenario and hour", "シナリオと時刻で制限されるステージ AI の行動"),
    "map": ("Per-spell motion and effect frames, shot control per level", "魔法ごとのモーション・エフェクトのフレームと、"
                                                                        "レベルごとの射出制御"),
    "qct": ("Quest judgment and result commands per sheet", "シートごとのクエスト判定・結果コマンド"),
    "rst": ("Per-creature status regions (grab / climb candidates)", "モンスターごとの状態領域（掴み・登りの候補）"),
    "spn": ("Stage place names: the stage -> room-name map", "ステージの地名：ステージと部屋名の対応表"),
    "jcp": ("Online: per custom skill, the 17 resources it uses (motion list, motion params, collision, 10 attack "
            "params, sounds, effects) by path hash and type", "DDO：カスタムスキルごとに、使用する 17 個のリソース"
                                                          "（モーションリスト、モーションパラメーター、当たり判定、攻撃パラメーター 10 個、"
                                                          "サウンド、エフェクト）をパスのハッシュと種類で指定"),
    "csd": ("Online: custom skills of a vocation and their level tables", "DDO：ジョブのカスタムスキルと、そのレベル表"),
    "nsd": ("Online: core skills of a vocation and their cost", "DDO：ジョブのコアスキル（ノーマルスキル）と習得コスト"),
    "jmc": ("Online: job master NPCs and tutorial quests per vocation", "DDO：ジョブごとのジョブマスター NPC と"
                                                                      "チュートリアルクエスト"),
    "eir": ("Online: which vocations may equip an item category (bit n = vocation n)", "DDO：アイテム種別を装備できる"
                                                                                      "ジョブ（ビット n がジョブ n）"),
    "wcrt": ("Online: weapon categories with their effect provider and sound request", "DDO：武器カテゴリーと、"
                                                                                    "そのエフェクト・サウンドの参照"),
    "dja": ("Online: 15 damage multipliers per vocation (meanings UNKNOWN)", "DDO：ジョブごとの 15 個のダメージ倍率"
                                                                         "（各値の意味は不明）"),
    "jlt2": ("Online: one row of five numbers per job level (meanings UNKNOWN)", "DDO：ジョブレベルごとに 5 つの数値"
                                                                               "（意味は不明）"),
    "jtq": ("Online: the vocation tutorial quest ids", "DDO：ジョブのチュートリアルクエスト ID"),
    "atk": ("Online: per-hit attack data of skills and enemies; ten files per custom skill, one per level (most "
            "field meanings UNKNOWN; 00D appears to be the element, 008 / 014 the physical / magick rate)",
            "DDO：スキルと敵の攻撃ごとの攻撃データ。カスタムスキルはレベルごとに 1 ファイル、計 10 ファイル"
            "（ほとんどのフィールドの意味は不明。00D は属性、008／014 は物理／魔法の倍率と思われます）"),
    "acp": ("Online: action lists: per row an action class (its MtDTI id) and its parameters",
            "DDO：アクションリスト。各行がアクションのクラス（MtDTI の ID）とそのパラメーター"),
    "kcm": ("Online: a vocation's input commands (most field meanings UNKNOWN)",
            "DDO：ジョブの入力コマンド（ほとんどのフィールドの意味は不明）"),
    "motparam": ("Online: per-motion parameters beside a motion list (meanings UNKNOWN)",
                 "DDO：モーションリストに付随するモーションごとのパラメーター（意味は不明）"),
    "kcp": ("Online: key configuration presets", "DDO：キーコンフィグのプリセット"),
    "chant": ("Online: magick chant parameters per spell (most field meanings UNKNOWN)",
              "DDO：魔法ごとの詠唱パラメーター（ほとんどのフィールドの意味は不明）"),
    "qmi": ("Online: a stage's quest markers (position, group, id)", "DDO：ステージのクエストマーカー（位置、グループ、ID）"),
    "fmi": ("Online: a field area's markers (position, stage, group, id)",
            "DDO：フィールドエリアのマーカー（位置、ステージ、グループ、ID）"),
    "smc": ("Online: which message group shows during which quest window",
            "DDO：どのクエスト期間にどのメッセージグループを表示するか"),
}
for _ext, (_en, _ja) in _FLAT.items():
    FORMATS.setdefault(_ext, {"title": _t(_en, _ja), "body": _t(
        _en + ". A flat table: edit the numbers, keep the structure; lists may grow or shrink, fixed groups keep "
        "their length. Rebuilds byte for byte when untouched.",
        _ja + "。フラットなテーブル形式です：数値は編集できますが、構造は変えないでください。リストは要素の増減が"
        "できますが、固定長のグループは長さを変えないでください。変更しなければ完全に同一のバイト列で再構築されます。")})

FORMATS["csd"]["fields"] = {
    "mSkillNo": _t("The skill's number (1..N base skills, 101.. and 201.. its EX variants); jobcustomNN.jcp uses the "
                   "same number.", "スキル番号です（1〜N が基本スキル、101〜 と 201〜 が EX スキル）。"
                               "jobcustomNN.jcp も同じ番号を使います。"),
    "mMsgIndex": _t("Which message of custom_skill_name_NN / custom_skill_info_NN names and describes the skill.",
                    "custom_skill_name_NN／custom_skill_info_NN の何番目のメッセージがこのスキルの名前と説明かを示します。"),
    "mBaseSkillNo": _t("For an EX skill, the base skill it varies (0 otherwise).",
                       "EX スキルの場合、元になる基本スキルの番号です（それ以外は 0）。"),
    "mJobLevel": _t("Job level needed for this skill level.", "このスキルレベルに必要なジョブレベルです。"),
    "mJobPoint": _t("Job points this skill level costs.", "このスキルレベルに必要なジョブポイントです。"),
}
FORMATS["nsd"]["fields"] = {
    "mJobPoint": _t("Job points the core skill costs (matches the server for 33 of 34 purchasable skills).",
                    "コアスキルの習得に必要なジョブポイントです（購入できる 34 個中 33 個がサーバーの値と一致）。"),
    "mJobLevel": _t("Job level needed.", "必要なジョブレベルです。"),
    "mSkillNo": _t("The core skill's number.", "コアスキルの番号です。"),
}
FORMATS["jmc"]["fields"] = {
    "mJobId": _t("The vocation, 1-11 (Fighter .. High Scepter).", "ジョブ（1〜11：ファイター〜ハイセプター）。"),
    "mStartJobLevel": _t("DDO.exe's own field name; 18 for vocations 1-10 and 1 for High Scepter. What it gates in "
                         "game: UNKNOWN.",
                         "DDO.exe 上のフィールド名です。ジョブ 1〜10 は 18、ハイセプターは 1 です。ゲーム内で何を制限して"
                         "いるかは不明（UNKNOWN）です。"),
}
FORMATS["dja"]["fields"] = {
    "mJobType": _t("The vocation, 1-11.", "ジョブ（1〜11）。"),
    "mRate": _t("Fifteen damage multipliers (1.0 = unchanged); which damage each scales: UNKNOWN.",
                "15 個のダメージ倍率です（1.0 で変化なし）。それぞれがどのダメージに掛かるかは不明（UNKNOWN）です。"),
}

for _tag, _same in (("srq-ddo", "srq"), ("stq-ddo", "stq"), ("smx-ddo", "smx"), ("mss-ddo", "mss"),   # DDO's tags
                     *((f"{k}-ddo", k) for k in ("cpe", "pep", "prs", "osp", "sti", "sal", "evtr", "ndp"))):
    FORMATS[_tag] = FORMATS[_same]

TOPICS: dict[str, dict] = {**TABS, **FORMATS}
LANGS = ("en", "ja")

_TAG = re.compile(r"^riftstone:\s*([A-Za-z0-9_\-]+)/", re.M)


def topic_for(path_or_tag: str, yaml_text: str | None = None) -> str | None:
    """The help topic for a resource path (by extension, `.yaml` suffix ignored) or a YAML file's tag."""
    if yaml_text:
        m = _TAG.search(yaml_text[:4096])
        if m and m.group(1) in TOPICS:
            return m.group(1)
        if m and m.group(1) in ("xfs", "params"):
            return "fsm" if "rAIFSM" in yaml_text[:4096] else "xfs"
    p = (path_or_tag or "").lower()
    if p.endswith(".yaml"):
        p = p[:-5]
    ext = p.rsplit(".", 1)[-1] if "." in p else p
    if ext in TOPICS:
        return ext
    from . import typemap
    tid = typemap.type_for_extension(ext) if ext else None
    if tid is not None and typemap.is_xfs(tid):
        return "xfs"
    return None


def get(topic: str, field: str | None = None) -> dict | None:
    """{"topic", "title", "body", "field"?} in both languages, or None for an unknown topic."""
    t = TOPICS.get(topic)
    if t is None:
        return None
    out = {"topic": topic, "title": t["title"], "body": t["body"]}
    if field:
        f = t.get("fields", {}).get(field)
        if f:
            out["field"] = {"name": field, **f}
    out["fields"] = sorted(t.get("fields", {}))
    return out


def text(topic: str, lang: str = "en", field: str | None = None) -> str:
    """Plain text for the CLI."""
    h = get(topic, field)
    if h is None:
        raise KeyError(topic)
    lang = lang if lang in LANGS else "en"
    lines = [h["title"][lang], "", h["body"][lang]]
    if "field" in h:
        lines += ["", f"{field}: {h['field'][lang]}"]
    elif h["fields"]:
        lines += ["", ("Fields with help: " if lang == "en" else "解説のあるフィールド：") + ", ".join(h["fields"])]
    return "\n".join(lines)
