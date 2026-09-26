# Stage -> room-name map (measured)

Every stage's named sub-areas, from `rStagePlaceName` (`.spn`, 23 files, byte-exact via
`flat.py`) whose `mPlaceNameId` indexes `id/DDN/message/common/map_placelist`. This is the
definitive stage<->place mapping -- no guessing. Regenerate with `tools/gen_stage_map.py`.

| Stage | Named areas (map_placelist) |
|---|---|
| `st100` | Verda Woodlands, Manamia Trail, Moonsbit Pass, Deos Hills, Estan Plains, Wilted Forest, Northface Forest, Cutlass Cape, Barta Crags, Windbluff Tower, The Ruins of Aernst Castle, The Ruins of Heavenspeak Fort, Rest Camp, The Mountain Waycastle, Pastona Cavern, Greatwall Encampment, Ophis' Domain, Miasmic Haunt, Prayer Falls, Moonshower Cliffs, The Abbey, Devilfire Grove, Miner's Hut, Healing Spring, Storage Shed, Hillfigure Knoll, Windworn Valley, Smugglers' Pass, Collapsed Meeting Room, Mountain Cottage, Warrior's Departure, Abandoned Storehouse, Lake Hardship, Cape Pactforge, Miner's Toolshed, Bloodwater Beach, Tomb of the Unknown Traveler, Beast Cave, Seabreeze Trail, Unusual Beach, Cobal Coast, Cursewood, Stones of Courage, Conquest Road, Vestad Hills, Eradication Site, Conqueror's Sanctuary, Nightcall Crevasse, Caverns of Delusion, Man Swallowing Falls, Old Garrison, Abandoned Campsite, Nameless Falls, Travelers' Camp, Bandits' Den, Travelers' Rest, Knowledge Chair, Portcrystal |
| `st200` | Chief Adaro's House, Your House, Pablos' Inn, Aestella's, Benita's House, Heraldo's Grocery, Iola's House, Inez's Alehouse, Fisherman's House, Village Chapel, Starfall Bay, Village Pier, Knowledge Chair, Barn, Notice Board |
| `st210` | Enlistment Corps Base, Command Headquarters, Training Grounds, Notice Board |
| `st220` | Urban Quarter, Craftsman's Quarter, Craftsman's House, Venery, Slums, Noble Quarter, Gran Soren Union Inn, Caxton's Armory, Camellia's Apothecary, Devyn's Barber Shop, Arsmith's Alehouse, Pawn Guild, Smithy, Field, ???, Madeleine's Shop, The Black Cat, Gran Soren Cathedral, Fournival Manor, Knight's Manor, Fountain Square, Castle Gate, Residence, Merchant's House, Aqueduct, Passage Gate, Portcrystal, Abandoned House, Knowledge Chair, Notice Board |
| `st230` | Urban Quarter, Craftsman's Quarter, Noble Quarter, Caxton's Armory, Camellia's Apothecary, Arsmith's Alehouse, Pawn Guild, Craftsman's House, Smithy, Field, Gran Soren Cathedral, Fournival Manor, Knight's Manor, Passage Gate, Portcrystal, Refuge Area, Notice Board, Knowledge Chair |
| `st240` | Audience Chamber, Visitor's Chamber, Chamberlain's Office, Gathering Hall, Duke's Solar, Duchess's Bedchamber, Treasury, Storehouse, Guard Station, Dungeon, Duchess's Gardens, Observation Room |
| `st300` | Witch's House, Guardian's Grave |
| `st310` | Before the Greatwall Gate |
| `st320` | Gathering Hall, Confessional Chamber |
| `st330` | Commander's Antechamber, Station Room |
| `st370` | Tower Summit |
| `st380` | Holding Room, Grand Hall, Path to Dragon's Domain, Temple Antechamber |
| `st400` | Portcrystal, Resting Bench, Request Board, Monument of Remembrance |
| `st405` | Resting Bench, Request Board, Healing Spring |
| `st406` | Resting Bench, Request Board |
| `st600` | Flameservant's Throne, Ceremonial Cage |
| `st601` | Chamber of Anxiety, Chamber of Absence, Chamber of Hesitation, Chamber of Apprehension, Chamber of Remorse, Chamber of Tragedy, Chamber of Lament, Chamber of Fate, Chamber of Distress, Chamber of Estrangement, Chamber of Woe, Chamber of Sorrow, Chamber of Resolution, Chamber of Inspiration, Chamber of Hope |
| `st700` | Brightwater Cove, Emperor's Pillar, Station Room |
| `st701` | Water's Bottom, Offering Chamber |
| `st702` | Abandoned Mine |
| `st704` | Leaper's Ledge |
| `st705` | Receiving Room, Underground Corridor |
| `st706` | Proving Grounds |
