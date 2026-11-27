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



def test_duplicate_local_ids_never_bind_relationships():
    schema,raw=fixture()
    raw["entities"].append({**raw["entities"][0],"name":"另一个设备"})
    result=validate(raw,schema,TEXT)
    assert [e["local_id"] for e in result["entities"]]==["b"]
    assert not result["relationships"] and result["errors"]


def test_retry_failure_keeps_prior_facts(tmp_path):
    schema,raw=fixture()
    store=Store(tmp_path/"retry.sqlite3")
    o=store.resource("ontology","设备",TTL,schema)
    m=store.resource("prompt","设备","prompt",{"description":"设备"})
    p=store.profile("设备",o,m)
    profiles=store.profiles(True)
    good={"chunk":{"content":TEXT},"routes":[{"id":p}],"errors":[],
          "extractions":[{"profile_id":p,**validate(raw,schema,TEXT)}]}
    bad={"chunk":{"content":TEXT},"routes":[],"errors":["model failed"],"extractions":[]}
    d=store.new_document("a.txt",TEXT.encode(),{},profiles)
    store.commit(d,[good],profiles)
    before=store.export()
    store.commit_retry(d,[good],{0:bad},profiles)
    assert store.export()==before
    assert store.chunks(d)[0]["last_retry"]["errors"]
    old,failed=store.retry_input(d)
    assert failed==[0]
    store.commit_retry(d,old,{0:good},profiles)
    assert store.chunks(d)[0]["last_retry"] is None
    assert store.documents()[0]["status"]=="COMPLETED"


def test_cross_ontology_imports_and_rdf_export(tmp_path):
    from rdflib import Graph, URIRef, RDF
    from lightrag.ontology.exporter import rdf_turtle
    dep="""@prefix owl: <http://www.w3.org/2002/07/owl#> .
    <https://example.org/space> a owl:Ontology .
    <https://example.org/space#Zone> a owl:Class ."""
    root="""@prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    <https://example.org/device> a owl:Ontology ; owl:imports <https://example.org/space> .
    <https://example.org/device#Device> a owl:Class .
    <https://example.org/device#locatedIn> a owl:ObjectProperty ;
      rdfs:domain <https://example.org/device#Device> ; rdfs:range <https://example.org/space#Zone> ."""
    with pytest.raises(ValueError,match="Missing local"):
        compile_ttl(root)
    schema=compile_ttl(root,[dep])
    rel=EX+"locatedIn"
    assert schema["relationships"][rel]["range"]==["https://example.org/space#Zone"]
    data={"entities":[{"id":"a","class_uri":EX+"Device","name":"设备","attributes":[]},
                      {"id":"b","class_uri":"https://example.org/space#Zone","name":"区域","attributes":[]}],
          "relationships":[{"source":"a","target":"b","predicate_uri":rel}]}
    graph=Graph().parse(data=rdf_turtle(data),format="turtle")
    assert (URIRef("urn:gptonto:instance:a"),URIRef(rel),URIRef("urn:gptonto:instance:b")) in graph
    assert (URIRef("urn:gptonto:instance:a"),RDF.type,URIRef(EX+"Device")) in graph


def test_heading_exact_source_and_no_measurement_boundary():
    # Character tokenizer makes the boundary test independent of network cache.
    class Tok:
        def encode(self,text): return list(text)
    text="一、设备\n型号甲乙丙丁，电量88%。\n31.5 ℃\n二、状态\n运行正常。"
    chunks=heading_chunks(Tok(),text,10)
    assert all(c["content"]==text[c["_source_span"]["start"]:c["_source_span"]["end"]] for c in chunks)
    assert all(c["tokens"]<=10 for c in chunks)
    assert all(c["heading_path"] in (["一、设备"],["二、状态"]) for c in chunks)
    assert "".join(c["content"] for c in chunks).replace("\n","")==text.replace("\n","")


def test_json_rejects_overflow():
    from lightrag.ontology.engine import parse_json
    with pytest.raises(ValueError):
        parse_json('{"value":1e999}')
