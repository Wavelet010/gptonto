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



def test_cross_chunk_completion_and_delete(tmp_path):
    from lightrag.ontology.cross_chunk import complete_relationships
    from lightrag.ontology.storage import entity_id
    from lightrag.ontology.browse import browse_instances
    import json
    schema,raw=fixture()
    store=Store(tmp_path/"cross.sqlite3")
    o=store.resource("ontology","设备",TTL,schema)
    m=store.resource("prompt","设备","prompt",{"description":"设备","extraction":{},"identity_properties":{EX+"Device":EX+"deviceId",EX+"Gateway":EX+"gatewayId"}})
    pid=store.profile("设备",o,m)
    profiles=store.profiles(True)
    chunks=[{"content":"设备D-001。"},{"content":"网关GW-01。"},{"content":"上述设备连接至该网关。"},{"content":"无关信息。"}]
    records=[]
    for i,c in enumerate(chunks):
        if i<2:
            entity=raw["entities"][i].copy()
            entity["attributes"]=[a for a in entity["attributes"] if a["evidence"] in c["content"]]
            ex={"profile_id":pid,**validate({"entities":[entity],"relationships":[]},schema,c["content"])}
            records.append({"chunk":c,"routes":[{"id":pid}],"extractions":[ex],"errors":[]})
        else:
            records.append({"chunk":c,"routes":[],"extractions":[],"errors":[]})
    did=store.new_document("a.txt",b"x",{},profiles)
    a,b=[entity_id(did,i,profiles[0],records[i]["extractions"][0]["entities"][0]) for i in (0,1)]
    calls=[]
    async def llm(payload,system):
        data=json.loads(payload)
        calls.append(data["chunk_index"])
        return json.dumps({"relationships":[{"source":a,"target":b,"predicate_uri":EX+"connectedTo","evidence":"上述设备连接至该网关"}]})
    completed=asyncio.run(complete_relationships(did,records,profiles,llm))
    assert calls==[2]
    assert completed[2]["cross_relationships"][0]["target"]==b
    assert completed[2]["status"]=="COMPLETED"
    store.commit(did,completed,profiles)
    data=store.export(did)
    assert len(data["entities"])==2 and len(data["relationships"])==1
    assert data["relationships"][0]["source"]==a
    assert data["relationships"][0]["sources"][0]["evidence"]=="上述设备连接至该网关"
    page=browse_instances(store,did,limit=1)
    assert page["total"]==2 and len(page["entities"])==1 and not page["relationships"]
    assert browse_instances(store,did,q="D-001")["entities"][0]["id"]==a
    assert browse_instances(store,did,focus=a)["relationships"][0]["target"]==b
    store.delete_document(did)
    assert store.export()=={"entities":[],"relationships":[]}


def test_external_parser_hooks(tmp_path,monkeypatch):
    from lightrag.ontology.documents import parse_external
    import lightrag.parser.registry as registry
    import lightrag.sidecar as sidecar
    from lightrag.parser.base import ParseResult
    class Parser:
        async def parse(self,ctx):
            resolved = ctx.resolve("mineru")
            assert resolved.source_path.read_bytes()==b"example"
            assert resolved.document_name=="document.pdf"
            assert ctx.content_data["parse_engine"]=="mineru"
            await ctx.rag._persist_parsed_full_docs(ctx.doc_id,{"content":"解析结果"})
            await ctx.archive_source(str(resolved.source_path))
            return ParseResult(ctx.doc_id,ctx.file_path,"lightrag","解析结果","blocks.jsonl")
    monkeypatch.setattr(registry,"get_parser",lambda name:Parser())
    monkeypatch.setattr(registry,"engine_endpoint_configured",lambda name:True)
    assert asyncio.run(parse_external("source.pdf",b"example","a"*32,tmp_path,"mineru"))==("解析结果","blocks.jsonl")


def test_http_api_contract(tmp_path,monkeypatch):
    import httpx
    from lightrag.ontology.app import create_app
    monkeypatch.setenv("GPTONTO_DB",str(tmp_path/"http.sqlite3"))
    monkeypatch.setenv("GPTONTO_API_KEY","test-secret")
    app=create_app()
    paths=app.openapi()["paths"]
    assert "/ontology-api/documents" in paths and "/ontology-api/export" in paths
    assert "/ontology-api/instances" in paths and "/query" not in paths
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            assert (await client.get("/ontology-api/documents")).status_code==401
            headers={"Authorization":"Bearer test-secret"}
            r=await client.get("/ontology-api/documents",headers=headers)
            assert r.status_code==200 and r.json()==[]
            r=await client.get("/ontology-api/instances",headers=headers)
            assert r.status_code==200 and r.json()["total"]==0
            r=await client.get("/ontology-api/parsers",headers=headers)
            assert r.status_code==200 and {p["id"] for p in r.json()}=={"native","legacy","mineru","docling"}
            r=await client.post("/ontology-api/documents/preview",headers=headers,
                files={"file":("demo.txt","一、设备\n设备D-001。\n二、状态\n正常。".encode())})
            assert r.status_code==200 and r.json()["count"]==2
    asyncio.run(run())


def test_upstream_queue_and_cache(tmp_path,monkeypatch):
    import lightrag.llm.openai as provider
    from lightrag.ontology.llm_runtime import create_llm_runtime
    monkeypatch.setenv("LLM_MODEL","mock-model")
    monkeypatch.setenv("GPTONTO_LLM_CACHE","true")
    calls=[]
    async def mock(*args,**kwargs):
        calls.append(args)
        return '{"matches":[]}'
    monkeypatch.setattr(provider,"openai_complete_if_cache",mock)
    store=Store(tmp_path/"cache.sqlite3")
    async def run():
        llm,fresh,close,stats=create_llm_runtime(store)
        try:
            assert await llm("test","system")=='{"matches":[]}'
            assert await llm("test","system")=='{"matches":[]}'
            assert len(calls)==1
            assert await fresh("test","system")=='{"matches":[]}'
            assert len(calls)==2
        finally:
            await close()
    asyncio.run(run())



def test_preview_reuse_is_exact_and_one_use(tmp_path):
    from lightrag.ontology.preview import PreviewCache
    cache=PreviewCache(max_entries=2,max_bytes=10000)
    opts={"parser":"legacy","strategy":"H","size":2000}
    chunks=[{"content":"原文","chunk_order_index":0}]
    token=cache.put("a"*32,"doc.txt",b"source",opts,chunks)
    assert cache.take(token,"doc.txt",b"other",opts) is None
    assert cache.take(token,"doc.txt",b"source",{**opts,"size":500}) is None
    assert cache.take(token,"doc.txt",b"source",{**opts,"cross_chunk":True})==("a"*32,chunks)
    assert cache.take(token,"doc.txt",b"source",opts) is None
    store=Store(tmp_path/"preview.sqlite3")
    did=store.new_document("doc.txt",b"source",opts,[],did="a"*32,prepared=chunks)
    assert store.prepared_chunks(did)==chunks
    store.commit(did,[{"chunk":chunks[0],"routes":[],"extractions":[],"errors":[]}],[])
    assert store.prepared_chunks(did) is None


def test_graph_adapter_parallel_directed_edges_and_scope(tmp_path):
    from lightrag.ontology.graph_adapter import graph_data,graph_labels
    schema,raw=fixture()
    store=Store(tmp_path/"graph.sqlite3")
    o=store.resource("ontology","设备",TTL,schema)
    m=store.resource("prompt","设备","prompt",{"description":"设备"})
    pid=store.profile("设备",o,m)
    profiles=store.profiles(True)
    # Same pair of entities, two distinct ontology predicates.
    valid=validate(raw,schema,TEXT)
    rec={"chunk":{"content":TEXT},"routes":[{"id":pid}],"errors":[],"extractions":[{"profile_id":pid,**valid}]}
    did=store.new_document("one.txt",b"one",{},profiles)
    did2=store.new_document("two.txt",b"two",{},profiles)
    store.commit(did,[rec],profiles)
    store.commit(did2,[rec],profiles)
    data=graph_data(store,document_id=did)
    assert len(data["nodes"])==2 and len(data["edges"])==2
    assert {e["type"] for e in data["edges"]}=={EX+"connectedTo",EX+"reportsTo"}
    assert len({(e["source"],e["target"]) for e in data["edges"]})==1
    assert len(graph_labels(store,"设备",document_id=did))==1
    label=graph_labels(store,"设备",document_id=did)[0]
    assert len(graph_data(store,label,1,10,did)["edges"])==2
    assert len(graph_data(store,"*",1,1,did)["nodes"])==1



def test_original_evidence_and_artifact_roundtrip(tmp_path):
    from lightrag.ontology.evidence import locate_chunks,pack_artifacts,artifact_manifest,read_artifact,evidence_view
    text="前文😀设备D-001，电量88%。后文"
    chunks=locate_chunks(text,[{"content":"设备D-001，电量88%。"}])
    (tmp_path/"parsed").mkdir()
    (tmp_path/"parsed"/"document.blocks.jsonl").write_text("test",encoding="utf-8")
    bundle=pack_artifacts(tmp_path,text)
    assert read_artifact(bundle,"parsed-text.txt").decode()==text
    assert any(a["path"]=="parsed/document.blocks.jsonl" for a in artifact_manifest(bundle))
    with pytest.raises(KeyError): read_artifact(bundle,"../secret")
    store=Store(tmp_path/"evidence.sqlite3")
    did=store.new_document("original.txt",text.encode(),{},[],artifacts=bundle)
    store.commit(did,[{"chunk":chunks[0],"routes":[],"extractions":[],"errors":[]}],[])
    cid=store.chunks(did)[0]["id"]
    view=evidence_view(store.evidence_chunk(cid),"D-001")
    assert view["matches"][0]["parsed_start"]==text.index("D-001")
    assert view["filename"]=="original.txt"
    assert store.artifacts(did)==bundle
    store.delete_document(did)
    assert store.artifacts(did) is None
    with pytest.raises(KeyError): store.evidence_chunk(cid)


def test_document_ui_contract_and_atomic_delete(tmp_path):
    from lightrag.ontology.document_api import document_page
    store=Store(tmp_path/"document-ui.sqlite3")
    a=store.new_document("a.txt",b"a",{},[])
    b=store.new_document("b.txt",b"b",{},[])
    store.status(a,"COMPLETED")
    store.status(b,"PROCESSING")
    page=document_page(store,{"page_size":1},{"b":{"phase":"ANALYZING"}})
    assert page["pagination"]["total_count"]==2
    assert page["status_counts"]["processed"]==1
    with pytest.raises(ValueError): store.delete_documents([a,b])
    assert len(store.documents())==2
    store.status(b,"CANCELLED")
    assert store.delete_documents([a,b])==2


def test_multimodal_evidence_not_original():
    from lightrag.ontology.evidence import evidence_view
    row={"id":"c","ord":0,"doc_id":"d","filename":"image.pdf",
         "data":{"content":"模型描述","evidence_kind":"multimodal_analysis","sidecar":{"type":"drawing","id":"x"}}}
    view=evidence_view(row,"模型")
    assert view["evidence_kind"]=="multimodal_analysis"
    assert view["matches"]==[{"chunk_start":0,"chunk_end":2}]
    assert view["sidecar"]["id"]=="x"
