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
    page=document_page(store,{"page_size":1},{b:{"phase":"ANALYZING"}})
    assert page["pagination"]["total_count"]==2
    assert page["status_counts"]["processed"]==1 and page["status_counts"]["analyzing"]==1
    assert len(document_page(store,{"status_filters":["processed"]},{})["documents"])==1
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



def test_normalized_and_repeated_evidence_positions():
    from lightrag.ontology.evidence import locate_chunks,evidence_view
    text="设备  A。\n设备  A。"
    exact,normalized=locate_chunks(text,[{"content":"设备  A。"},{"content":"设备 A。"}])
    assert "_source_span" not in exact
    assert exact["evidence_kind"]=="parsed_text"
    assert normalized["evidence_kind"]=="normalized_chunk"
    row={"id":"c","ord":0,"doc_id":"d","filename":"a.txt","data":normalized}
    assert evidence_view(row,"设备")["matches"]==[{"chunk_start":0,"chunk_end":2}]



def test_native_docx_image_analysis_with_original_pipeline(tmp_path,monkeypatch):
    import io
    import json
    from docx import Document
    from PIL import Image
    import lightrag.llm.openai as provider
    from lightrag.ontology.documents import split_document
    from lightrag.ontology.multimodal import create_multimodal_runtime
    from lightrag.ontology.evidence import artifact_manifest
    monkeypatch.setenv("VLM_MODEL","mock-vision")
    monkeypatch.setenv("LLM_MODEL","mock-text")
    monkeypatch.setenv("VLM_MIN_IMAGE_PIXEL","1")
    monkeypatch.setenv("GPTONTO_LLM_CACHE","false")
    image=io.BytesIO()
    Image.new("RGB",(256,256),"white").save(image,format="PNG")
    image.seek(0)
    document=Document()
    document.add_paragraph("一、设备示意图")
    document.add_picture(image)
    document.add_paragraph("设备图片说明。")
    doc=io.BytesIO()
    document.save(doc)
    calls=[]
    async def mock(*args,**kwargs):
        calls.append(kwargs.get("image_inputs"))
        return json.dumps({"name":"设备图片","description":"图中包含设备示意内容","type":"diagram"},ensure_ascii=False)
    monkeypatch.setattr(provider,"openai_complete_if_cache",mock)
    store=Store(tmp_path/"mm.sqlite3")
    bundles=[]
    async def run():
        analyze,close=create_multimodal_runtime(store)
        try:
            return await split_document("image.docx",doc.getvalue(),"a"*32,
                {"parser":"native","strategy":"H","size":2000,"overlap":100,
                 "heading_depth":1,"modalities":["images"]},
                artifact_sink=bundles.append,multimodal=analyze)
        finally: await close()
    chunks=asyncio.run(run())
    assert calls and calls[0]
    assert any(c.get("evidence_kind")=="multimodal_analysis" for c in chunks)
    assert any(a["path"].endswith(".drawings.json") for a in artifact_manifest(bundles[0]))
    assert any(a["path"].endswith(".png") for a in artifact_manifest(bundles[0]))



def test_upstream_chunk_configuration_and_profile_snapshot(monkeypatch):
    from lightrag.ontology.config import resolve_options,profile_manager
    from lightrag.ontology.app import Options
    monkeypatch.setenv("CHUNK_R_SIZE","512")
    monkeypatch.setenv("CHUNK_R_OVERLAP_SIZE","32")
    monkeypatch.setenv("CHUNK_R_SEPARATORS",'["。","\\n"]')
    opts=Options(strategy="R")
    resolved=resolve_options("a.txt",opts.model_dump(),opts.model_fields_set)
    assert resolved["size"]==512 and resolved["overlap"]==32
    assert resolved["chunk_params"]["separators"]==["。","\n"]
    assert resolved["resolved_parser"]=="legacy"
    opts=Options(strategy="R",size=900,overlap=50)
    resolved=resolve_options("a.txt",opts.model_dump(),opts.model_fields_set)
    assert resolved["size"]==900 and resolved["overlap"]==50
    names=profile_manager().list_profiles()
    if names:
        opts=Options(strategy="S",edp_profile=names[0])
        resolved=resolve_options("a.txt",opts.model_dump(),opts.model_fields_set)
        assert resolved["edp_profile_data"]["profile_name"]


def test_document_wide_functional_conflict_keeps_diagnostics(tmp_path):
    import copy
    from lightrag.ontology.assertions import validate_document_assertions
    schema,raw=fixture()
    schema["properties"][EX+"batteryLevel"]["functional"]=True
    profile={"id":"p","schema":schema,"prompt":{"identity_properties":{EX+"Device":EX+"deviceId"}}}
    a={"chunk":{"content":TEXT},"routes":[],"errors":[],"extractions":[{"profile_id":"p",**validate(raw,schema,TEXT)}]}
    b=copy.deepcopy(a)
    b["chunk"]["content"]=TEXT.replace("88","99")
    attr=b["extractions"][0]["entities"][0]["attributes"][1]
    attr.update(value=99,evidence="电量99%")
    checked=validate_document_assertions("d",[a,b],[profile])
    assert all(c["assertion_errors"] for c in checked)
    assert {r["assertion"]["value"] for c in checked for r in c["rejected_assertions"]}=={88,99}
    assert all(not any(x["property_uri"]==EX+"batteryLevel" for x in c["extractions"][0]["entities"][0]["attributes"]) for c in checked)
    store=Store(tmp_path/"conflicts.sqlite3")
    did=store.new_document("test.txt",TEXT.encode(),{},[profile],did="d")
    store.commit(did,checked,[profile])
    assert store.document(did)["status"]=="PARTIAL"
    assert store.retry_input(did)[1]==[0,1]
    assert store.chunk_page(did,1,1)["total"]==2
    assert len(store.chunk_page(did,1,1)["chunks"])==1


def test_shared_model_concurrency(tmp_path,monkeypatch):
    from lightrag.ontology.llm_runtime import create_model_provider,create_llm_runtime
    import lightrag.llm.openai as module
    monkeypatch.setenv("LLM_MODEL","mock")
    monkeypatch.setenv("VLM_MODEL","mock-vlm")
    monkeypatch.setenv("GPTONTO_LLM_CONCURRENCY","1")
    monkeypatch.setenv("GPTONTO_LLM_CACHE","false")
    active=peak=0
    async def fake(*args,**kwargs):
        nonlocal active,peak
        active+=1
        peak=max(peak,active)
        try:
            await asyncio.sleep(.01)
            return "{}"
        finally: active-=1
    monkeypatch.setattr(module,"openai_complete_if_cache",fake)
    async def run():
        provider=create_model_provider()
        llm,_,close,_=create_llm_runtime(Store(tmp_path/"queue.sqlite3"),provider)
        try:
            await asyncio.gather(llm("text","sys"),provider("metadata"),provider("image",image_inputs=[{}]))
        finally: await close()
    asyncio.run(run())
    assert peak==1



def test_original_sidecar_block_positions(tmp_path):
    import json
    from lightrag.ontology.evidence import attach_source_blocks,evidence_view
    from lightrag.sidecar import backfill_chunk_sidecars
    block="设备D-001"
    path=tmp_path/"doc.blocks.jsonl"
    path.write_text("\n".join(json.dumps(x,ensure_ascii=False) for x in [
        {"type":"meta"},{"type":"content","blockid":"b1","content":block,
         "positions":[{"page":2,"bbox":[1,2,3,4]}]}]),encoding="utf-8")
    chunks=[{"content":block,"_source_span":{"start":0,"end":len(block)}}]
    backfill_chunk_sidecars(chunks,str(path))
    assert chunks[0]["sidecar"]["refs"]==[{"type":"block","id":"b1"}]
    attach_source_blocks(chunks,str(path),tmp_path)
    row={"id":"c","doc_id":"d","filename":"doc.pdf","ord":0,"data":chunks[0]}
    view=evidence_view(row,"D-001")
    assert view["source_blocks"][0]["positions"]==[{"page":2,"bbox":[1,2,3,4]}]
    assert view["source_blocks"][0]["artifact"]=="doc.blocks.jsonl"



@pytest.mark.parametrize("strategy",["F","R","P","S","H"])
def test_native_docx_chunker_preserves_original_evidence(strategy):
    import io
    from docx import Document
    from lightrag.ontology.documents import split_document
    from lightrag.ontology.evidence import artifact_manifest
    doc=Document()
    doc.add_heading("一、设备",level=1)
    doc.add_paragraph("设备D-001。")
    doc.add_heading("二、状态",level=1)
    doc.add_paragraph("电量88%。")
    content=io.BytesIO()
    doc.save(content)
    artifacts=[]
    chunks=asyncio.run(split_document("test.docx",content.getvalue(),"a"*32,
        {"parser":"native","strategy":strategy,"size":128,"overlap":0,"heading_depth":1},
        artifact_sink=artifacts.append))
    assert chunks and all(c["tokens"]<=128 for c in chunks)
    assert "设备D-001" in "".join(c["content"] for c in chunks)
    if strategy in {"F","R","P","H"}:
        assert any(c.get("source_blocks") for c in chunks)
    assert any(a["path"].endswith(".blocks.jsonl") for a in artifact_manifest(artifacts[0]))
