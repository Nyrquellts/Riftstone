import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from helpers import prop
from test_items import item_list, names
from riftstone import arc, arcref, gmd, itl, params, prp, registration as R, typemap, xfs
from riftstone.errors import RiftError


def fixture():
    item_entries=[arc.Entry.from_data(*R.ITEM_LIST,item_list(6,free=(3,4)))]
    for suffix,language in [(v,k) for k,v in gmd.SUFFIXES.items()]:
        for kind in ("itemName","itemInfo"):
            item_entries.append(arc.Entry.from_data(f"id\\message\\item\\{kind}_{suffix}".encode(),R.GMD,names(language,6,free=(3,4))))
    item_entries.append(arc.Entry.from_data(b"opaque",123,b"preserve exact payload"))
    definition=xfs.ClassDef(0x7E509FE9,16,(prop("Scale","f32"),))
    raw=prp.build(xfs.Xfs(5,[definition],xfs.Obj(0,[[1.0]])))
    enemy=arc.Archive([arc.Entry.from_data(b"charparam\\em\\em0100_cmn",R.PRP,raw)])
    reference=arcref.build(arcref.for_names([e.key for e in enemy.entries]))
    source={"rom/items":arc.Archive(item_entries).build(),"rom/enemy/em0100":enemy.build(),
            "rom/stage/test":arc.Archive([arc.Entry.from_data(b"rom\\enemy\\em0100",R.ARCS,reference)]).build()}
    request={"schema":"riftstone.registration/1","archives":[{"name":name,"sha256":hashlib.sha256(raw).hexdigest()} for name,raw in source.items()],
             "items":[{"key":"potion","template":1,"name":"New potion","description":"Useful.","buy":999}]}
    return source,request


class RegistrationTest(unittest.TestCase):
    def test_automatic_batch_ids_and_all_languages(self):
        source,request=fixture(); original=copy.deepcopy(source)
        request["items"].append({"key":"other","template":2,"name":"Other","translations":{"jpn":{"name":"新しい"}}})
        result=R.build(source,request)
        self.assertEqual([x["id"] for x in result.report["items"]],[3,4])
        built=arc.Archive.parse(result.archives["rom/items"])
        records=itl.parse(built.find(*R.ITEM_LIST).data()).records
        old=itl.parse(arc.Archive.parse(source["rom/items"]).find(*R.ITEM_LIST).data()).records
        self.assertEqual(itl.ItemList.prices(records[3]),(999,56))
        self.assertEqual(records[3][0x7c:0x7e],old[1][0x7c:0x7e])
        self.assertEqual(struct.unpack_from("<I",records[3],0x3c)[0]&~itl.ID_MASK,struct.unpack_from("<I",old[1],0x3c)[0]&~itl.ID_MASK)
        for suffix in gmd.SUFFIXES.values():
            doc=gmd.parse(built.find(f"id\\message\\item\\itemName_{suffix}".encode(),R.GMD).data())
            self.assertEqual(doc.messages[3].text,"New potion")
            self.assertEqual(doc.messages[4].text,"新しい" if suffix=="jpn" else "Other")
        self.assertEqual(built.find(b"opaque",123).data(),b"preserve exact payload")
        self.assertEqual(source,original)
        self.assertFalse(result.report["table_growth"])

    def test_duplicate_slot_and_capacity_are_transactional(self):
        source,request=fixture(); before=copy.deepcopy(source)
        request["items"][0]["id"]=3
        request["items"].append({"key":"second","template":1,"name":"Second","id":3})
        with self.assertRaises(RiftError): R.build(source,request)
        self.assertEqual(source,before)
        request["items"][1]["id"]=1901
        with self.assertRaises(RiftError): R.build(source,request)

    def test_all_copies_of_shared_text_updated_and_conflicts_rejected(self):
        source,request=fixture(); items=arc.Archive.parse(source["rom/items"])
        message=items.find(b"id\\message\\item\\itemName_eng",R.GMD)
        source["rom/mirror"]=arc.Archive([message]).build()
        request["archives"].append({"name":"rom/mirror","sha256":arc.sha256(source["rom/mirror"])})
        result=R.build(source,request)
        mirror=arc.Archive.parse(result.archives["rom/mirror"])
        self.assertEqual(gmd.parse(mirror.entries[0].data()).messages[3].text,"New potion")
        changed=gmd.parse(message.data()); changed.messages[0].text="conflict"
        source["rom/mirror"]=arc.Archive([arc.Entry.from_data(message.name,R.GMD,gmd.build(changed))]).build()
        request["archives"][-1]["sha256"]=arc.sha256(source["rom/mirror"])
        with self.assertRaisesRegex(RiftError,"conflicting"): R.build(source,request)

    def test_missing_locale_and_pin_mismatch_fail_before_outputs(self):
        source,request=fixture(); request["archives"][0]["sha256"]="0"*64
        with self.assertRaisesRegex(RiftError,"SHA-256"): R.build(source,request)
        request["archives"][0]["sha256"]=arc.sha256(source["rom/items"])
        parsed=arc.Archive.parse(source["rom/items"]); parsed.entries=[e for e in parsed.entries if e.name!=b"id\\message\\item\\itemName_ita"]
        source["rom/items"]=parsed.build(); request["archives"][0]["sha256"]=arc.sha256(source["rom/items"])
        with self.assertRaisesRegex(RiftError,"missing required"): R.build(source,request)

    def test_real_prp_and_arcs_codecs_rebuild_new_variant_references(self):
        source,request=fixture(); request["items"]=[]
        request["param_copies"]=[{"from_archive":"rom/enemy/em0100","from_resource":"charparam/em/em0100_cmn.prp",
                                  "to_archive":"rom/enemy/em0100","to_resource":"charparam/em/custom_variant.prp"}]
        result=R.build(source,request)
        enemy=arc.Archive.parse(result.archives["rom/enemy/em0100"])
        reference=arc.Archive.parse(result.archives["rom/stage/test"]).entries[0].data()
        self.assertEqual(len(enemy.entries),2)
        self.assertTrue(arcref.matches(arcref.parse(reference),[e.key for e in enemy.entries]))
        self.assertFalse(result.report["param_resources"][0]["enemy_archetype_registered"])
        self.assertEqual(result.report["rebuilt_references"],[{"archive":"rom/stage/test","target":"rom/enemy/em0100"}])

    def test_unsupported_archetype_and_unknown_keys_refuse(self):
        source,request=fixture(); request["enemy_archetypes"]=[{"id":9999}]
        with self.assertRaisesRegex(RiftError,"UNAVAILABLE"): R.build(source,request)
        request.pop("enemy_archetypes"); request["items"][0]["weigth"]=2
        with self.assertRaises(RiftError): R.build(source,request)
        with self.assertRaises(RiftError): R.parse_request(b'{"items":[],"items":[]}')
        with self.assertRaises(RiftError): R.parse_request(b'{"value":NaN}')

    def test_custom_prp_fields_use_the_existing_checked_codec(self):
        source,request=fixture(); request["items"]=[]
        raw=arc.Archive.parse(source["rom/enemy/em0100"]).entries[0].data()
        changed=prp.parse(raw); changed.root.fields[0]=[2.0]
        request["param_copies"]=[{"from_archive":"rom/enemy/em0100","from_resource":"charparam/em/em0100_cmn.prp",
            "to_archive":"rom/enemy/em0100","to_resource":"charparam/em/custom.prp","yaml":prp.to_yaml(changed)}]
        result=R.build(source,request)
        modified=arc.Archive.parse(result.archives["rom/enemy/em0100"]).find(b"charparam\\em\\custom",R.PRP)
        self.assertEqual(prp.parse(modified.data()).root.fields[0],[2.0])

    def test_reserved_placeholder_and_oversized_input_refused(self):
        source,request=fixture(); request["items"][0]["name"]="Unknown Item"
        with self.assertRaisesRegex(RiftError,"placeholder"): R.build(source,request)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"request.json"
            with path.open("wb") as file: file.truncate(16*1024*1024+1)
            with self.assertRaisesRegex(RiftError,"exceeds"): R.run(path,Path(temp))
        with self.assertRaises(RiftError): R.run(Path("does-not-exist.json"),Path("."))

    def test_output_is_new_standalone_archive_set_and_source_unchanged(self):
        source,request=fixture()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); inputs=root/"input"; inputs.mkdir()
            for name,raw in source.items():
                path=inputs/(name+".arc"); path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(raw)
            plan=root/"plan.json"; plan.write_text(json.dumps(request))
            report=R.run(plan,inputs,root/"package")
            self.assertEqual(report["status"],"BUILT")
            for name,raw in source.items():
                self.assertEqual((inputs/(name+".arc")).read_bytes(),raw)
                self.assertTrue((root/"package/nativePC"/(name+".arc")).is_file())
            with self.assertRaises(RiftError): R.run(plan,inputs,root/"package")
            with self.assertRaises(RiftError): R.run(plan,inputs,inputs/"bad")
            game=root/"game"; game.mkdir(); (game/"DDDA.exe").write_bytes(b"synthetic")
            with self.assertRaises(RiftError): R.run(plan,inputs,game/"bad")

    def test_malformed_public_values_fail_closed(self):
        source,request=fixture()
        for value in (True, -1, 3.5, "3"):
            bad=copy.deepcopy(request); bad["items"][0]["id"]=value
            with self.assertRaises(RiftError): R.build(source,bad)
        bad=copy.deepcopy(request); bad["archives"][0]["name"]="../escape"
        with self.assertRaises(RiftError): R.build(source,bad)


if __name__=="__main__": unittest.main()
