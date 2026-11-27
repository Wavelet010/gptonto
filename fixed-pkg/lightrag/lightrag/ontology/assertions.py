"""Check document-wide assertions after stable identity merging.

Do not choose a winner among conflicting values. Preserve every rejected
assertion and evidence in diagnostics; only non-conflicting facts enter ABox.
"""
import copy
from collections import defaultdict
from .storage import entity_id,dump


def validate_document_assertions(did,records,profiles):
    result=copy.deepcopy(records)
    by_id={p["id"]:p for p in profiles}
    functional_attrs={u for p in profiles for u,v in p["schema"]["properties"].items() if v.get("functional")}
    functional_rels={u for p in profiles for u,v in p["schema"]["relationships"].items() if v.get("functional")}
    values=defaultdict(set)
    targets=defaultdict(set)
    mappings={}
    for i,record in enumerate(result):
        for j,ex in enumerate(record["extractions"]):
            profile=by_id[ex["profile_id"]]
            mapping={e["local_id"]:entity_id(did,i,profile,e) for e in ex["entities"]}
            mappings[i,j]=mapping
            for e in ex["entities"]:
                for a in e["attributes"]:
                    if a["property_uri"] in functional_attrs:
                        values[mapping[e["local_id"]],a["property_uri"]].add(dump(a["value"]))
            for r in ex["relationships"]:
                if r["predicate_uri"] in functional_rels:
                    targets[mapping[r["source"]],r["predicate_uri"]].add(mapping[r["target"]])
        for r in record.get("cross_relationships",[]):
            if r["predicate_uri"] in functional_rels:
                targets[r["source"],r["predicate_uri"]].add(r["target"])
    bad_attrs={k for k,v in values.items() if len(v)>1}
    bad_rels={k for k,v in targets.items() if len(v)>1}
    for i,record in enumerate(result):
        rejected=[]
        def reject(kind,pid,key,assertion):
            rejected.append({"kind":kind,"profile_id":pid,"entity_id":key[0],"predicate_uri":key[1],"assertion":assertion})
        for j,ex in enumerate(record["extractions"]):
            mapping=mappings[i,j]
            pid=ex["profile_id"]
            for e in ex["entities"]:
                keep=[]
                for a in e["attributes"]:
                    key=(mapping[e["local_id"]],a["property_uri"])
                    if key in bad_attrs: reject("functional_attribute",pid,key,a)
                    else: keep.append(a)
                e["attributes"]=keep
            keep=[]
            for r in ex["relationships"]:
                key=(mapping[r["source"]],r["predicate_uri"])
                if key in bad_rels: reject("functional_relationship",pid,key,r)
                else: keep.append(r)
            ex["relationships"]=keep
        keep=[]
        for r in record.get("cross_relationships",[]):
            key=(r["source"],r["predicate_uri"])
            if key in bad_rels: reject("functional_relationship",r["profile_id"],key,r)
            else: keep.append(r)
        if "cross_relationships" in record: record["cross_relationships"]=keep
        if rejected:
            record["rejected_assertions"]=record.get("rejected_assertions",[])+rejected
            record["assertion_errors"]=["Conflicting document-wide functional assertions; no automatic winner"]
            record["status"]="PARTIAL"
    return result
