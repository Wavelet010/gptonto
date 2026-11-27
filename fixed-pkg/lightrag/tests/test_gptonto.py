"""Regression tests for extraction-only GPTOnto; no live LLM required."""
import asyncio
from pathlib import Path
import pytest
from lightrag.ontology.schema import compile_ttl, validate
from lightrag.ontology.engine import extract_chunks
from lightrag.ontology.storage import Store
from lightrag.ontology.heading import heading_chunks

EX = "https://example.org/device#"
TTL = (Path(__file__).resolve().parents[1]/"lightrag/ontology/example_device.ttl").read_text()
TEXT = "设备D-001连接网关GW-01，并上报。电量88%。"

def fixture():
    schema=compile_ttl(TTL)
    raw={"entities":[
        {"local_id":"a","name":"设备","class_uri":EX+"Device","evidence":"设备D-001","attributes":[
            {"property_uri":EX+"deviceId","value":"D-001","evidence":"D-001"},
            {"property_uri":EX+"batteryLevel","value":88,"evidence":"电量88%"}]},
        {"local_id":"b","name":"网关","class_uri":EX+"Gateway","evidence":"网关GW-01","attributes":[
            {"property_uri":EX+"gatewayId","value":"GW-01","evidence":"GW-01"}]}],
        "relationships":[{"source":"a","target":"b","predicate_uri":EX+x,"evidence":"连接网关GW-01，并上报"} for x in ("connectedTo","reportsTo")]}
    return schema,raw

def test_validate_direction_types_and_evidence():
    schema,raw=fixture()
    raw["entities"][0]["attributes"] += [
        {"property_uri":EX+"batteryLevel","value":"88","evidence":"电量88%"},
        {"property_uri":EX+"invented","value":1,"evidence":"88"},
        {"property_uri":EX+"model","value":"ABC","evidence":"absent"}]
    raw["relationships"].append({"source":"b","target":"a","predicate_uri":EX+"connectedTo","evidence":"连接"})
    result=validate(raw,schema,TEXT)
    assert len(result["entities"])==2
    assert len(result["entities"][0]["attributes"])==2
    assert len(result["relationships"])==2
    assert len(result["errors"])==4

def test_heading_boundaries():
    from lightrag.utils import TiktokenTokenizer
    tok=TiktokenTokenizer()
    text="前言\n一、设备\n设备D-001。\n二、网关\n网关GW-01。\n三、状态\n正常。"
    chunks=heading_chunks(tok,text,500)
    assert len(chunks)==4
    assert chunks[1]["heading_path"]==["一、设备"]
    assert "二、网关" not in chunks[1]["content"]
    nums=heading_chunks(tok,"1。设备\n正文\n2.网关\n正文\n3．状态\n正文",500)
    assert len(nums)==3

def test_router_multi_label_and_no_match():
    schema,raw=fixture()
    import json
    async def llm(payload,system):
        data=json.loads(payload)
        if "profiles" in data:
            return json.dumps({"matches":[{"id":"p","reason":"设备"},{"id":"q","reason":"连接"}] if data["text"]==TEXT else []})
        return json.dumps(raw)
    profiles=[{"id":x,"name":x,"ontology_id":"o","prompt_id":"m","schema":schema,"prompt":{"description":"设备","extraction":{}}} for x in ("p","q")]
    rec=asyncio.run(extract_chunks([{"content":TEXT},{"content":"无关"}],profiles,llm))
    assert len(rec[0]["extractions"])==2 and not rec[0]["errors"]
    assert len(rec[1]["routes"])==0 and not rec[1]["errors"]

def test_storage_provenance_and_delete(tmp_path):
    schema,raw=fixture()
    store=Store(tmp_path/"kg.sqlite3")
    o=store.resource("ontology","设备",TTL,schema)
    prompt={"description":"设备","identity_properties":{EX+"Device":EX+"deviceId",EX+"Gateway":EX+"gatewayId"}}
    m=store.resource("prompt","设备","prompt",prompt)
    p=store.profile("设备",o,m)
    profiles=store.profiles(True)
    valid=validate(raw,schema,TEXT)
    rec={"chunk":{"content":TEXT},"routes":[{"id":p}],"errors":[],"extractions":[{"profile_id":p,**valid}]}
    d1=store.new_document("a.txt",TEXT.encode(),{},profiles)
    d2=store.new_document("b.txt",TEXT.encode(),{},profiles)
    for d in (d1,d2):
        store.commit(d,[rec],profiles)
    export=store.export()
    assert len(export["entities"])==2 and len(export["relationships"])==2
    assert all(len(r["sources"])==2 for r in export["relationships"])
    store.commit(d1,[rec],profiles)
    assert all(len(r["sources"])==2 for r in store.export()["relationships"])
    store.delete_document(d1)
    assert len(store.export()["entities"])==2
    store.delete_document(d2)
    assert store.export()=={"entities":[],"relationships":[]}
