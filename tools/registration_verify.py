"""Verify offline registration on original DDDA archives, saving evidence only."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from riftstone import arc, gmd, itl, registration as R
from riftstone.game import find_game
from riftstone.index import home


def verify():
    game=find_game("ddda")
    key=hashlib.sha1(str(game.root.resolve()).lower().encode()).hexdigest()[:12]
    db=sqlite3.connect((home()/f"index-{key}.sqlite").as_uri()+"?mode=ro",uri=True)
    required=[R.ITEM_LIST]+[(f"id\\message\\item\\{kind}_{suffix}".encode(),R.GMD)
                           for kind in ("itemName","itemInfo") for suffix in gmd.SUFFIXES.values()]
    holders=set()
    for name,tid in required:
        holders.update(row[0] for row in db.execute("SELECT DISTINCT arc FROM res WHERE name=? AND type=?",(name,tid)))
    if not holders: raise RuntimeError("no indexed item resources")
    sources={name:game.vanilla_arc(name).read_bytes() for name in sorted(holders)}
    original={name:arc.sha256(raw) for name,raw in sources.items()}
    tables=[entry for raw in sources.values() for entry in arc.Archive.parse(raw).entries if entry.key==R.ITEM_LIST]
    names=[entry for raw in sources.values() for entry in arc.Archive.parse(raw).entries
           if entry.key==(b"id\\message\\item\\itemName_eng",R.GMD)]
    doc=gmd.parse(names[0].data())
    template=next(i for i,message in enumerate(doc.messages) if message.text=="Greenwarish")
    request={"schema":"riftstone.registration/1","archives":[{"name":name,"sha256":digest} for name,digest in original.items()],
             "items":[{"key":"verification","template":template,"name":"Registration verification","description":"Offline byte validation only."}]}
    result=R.build(sources,request)
    expected=set(required)
    checked=0
    for name,raw in sources.items():
        before=arc.Archive.parse(raw); after=arc.Archive.parse(result.archives[name])
        assert [e.key for e in before.entries]==[e.key for e in after.entries]
        for left,right in zip(before.entries,after.entries):
            if left.key not in expected:
                assert left==right, "unrelated resource compression/metadata changed"
                checked+=1
        assert arc.sha256(game.vanilla_arc(name).read_bytes())==original[name], "source was modified"
    return {"schema":"riftstone.registration-verification/1","status":"PASS",
            "item_count":len(itl.parse(tables[0].data()).records),"all_indexed_item_holders":sorted(holders),
            "unrelated_entries_exact":checked,"sources_unchanged":True,"game_started":False,
            "result":result.report,"claim":"actual vanilla item/archive byte registration verified; no gameplay or new-class registration claim"}


if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--out",required=True,type=Path)
    args=parser.parse_args(); result=verify(); args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open("x",encoding="utf-8") as stream: json.dump(result,stream,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!="result"}))
