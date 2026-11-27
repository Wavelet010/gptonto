"""SQLite storage. Validated assertions are distinct from raw model output."""
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(*values):
    return hashlib.sha256(dump(values).encode()).hexdigest()


def entity_id(doc_id, order, profile, entity):
    """Shared identifier contract for local extraction and cross-chunk references."""
    cls = entity["class_uri"]
    key = profile["prompt"].get("identity_properties",{}).get(cls)
    vals = {dump(a["value"]) for a in entity["attributes"]
            if a["property_uri"] == key and str(a["value"]) in a["evidence"]}
    return digest(cls,key,next(iter(vals))) if key and len(vals)==1 else digest(digest(doc_id,order),profile["id"],entity["local_id"])


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS llm_cache (
                id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS resources (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
                content TEXT NOT NULL, data TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS profiles (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, ontology_id TEXT NOT NULL REFERENCES resources(id),
                prompt_id TEXT NOT NULL REFERENCES resources(id), enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, filename TEXT NOT NULL, source BLOB NOT NULL,
                options TEXT NOT NULL, snapshot TEXT NOT NULL, status TEXT NOT NULL,
                error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS document_artifacts (
                doc_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                bundle BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS prepared_chunks (
                doc_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                ord INTEGER NOT NULL, data TEXT NOT NULL, result TEXT NOT NULL, UNIQUE(doc_id,ord)
            );
            CREATE TABLE IF NOT EXISTS retry_diagnostics (
                doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                ord INTEGER NOT NULL, result TEXT NOT NULL, PRIMARY KEY(doc_id,ord)
            );
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY, class_uri TEXT NOT NULL, name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entity_sources (
                entity_id TEXT REFERENCES entities(id) ON DELETE CASCADE,
                chunk_id TEXT REFERENCES chunks(id) ON DELETE CASCADE,
                profile_id TEXT NOT NULL, name TEXT NOT NULL, evidence TEXT NOT NULL,
                PRIMARY KEY(entity_id,chunk_id,profile_id,name,evidence)
            );
            CREATE TABLE IF NOT EXISTS attributes (
                id TEXT PRIMARY KEY, entity_id TEXT REFERENCES entities(id) ON DELETE CASCADE,
                property_uri TEXT NOT NULL, value TEXT NOT NULL, datatype TEXT NOT NULL,
                chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
                profile_id TEXT NOT NULL, evidence TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS relationships (
                id TEXT PRIMARY KEY, source TEXT REFERENCES entities(id) ON DELETE CASCADE,
                predicate_uri TEXT NOT NULL, target TEXT REFERENCES entities(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS relation_sources (
                relationship_id TEXT REFERENCES relationships(id) ON DELETE CASCADE,
                chunk_id TEXT REFERENCES chunks(id) ON DELETE CASCADE,
                profile_id TEXT NOT NULL, evidence TEXT NOT NULL,
                PRIMARY KEY(relationship_id,chunk_id,profile_id,evidence)
            );
            CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id);
            CREATE INDEX IF NOT EXISTS ix_entities_class ON entities(class_uri);
            CREATE INDEX IF NOT EXISTS ix_entity_sources_name ON entity_sources(name);
            CREATE INDEX IF NOT EXISTS ix_entity_sources_chunk ON entity_sources(chunk_id);
            CREATE INDEX IF NOT EXISTS ix_attrs_entity ON attributes(entity_id);
            CREATE INDEX IF NOT EXISTS ix_attrs_chunk ON attributes(chunk_id);
            CREATE INDEX IF NOT EXISTS ix_rel_source ON relationships(source);
            CREATE INDEX IF NOT EXISTS ix_rel_target ON relationships(target);
            CREATE INDEX IF NOT EXISTS ix_relation_sources_chunk ON relation_sources(chunk_id);
            """)
            if "updated_at" not in {r[1] for r in db.execute("PRAGMA table_info(documents)")}:
                db.execute("ALTER TABLE documents ADD COLUMN updated_at TEXT")
                db.execute("UPDATE documents SET updated_at=created_at")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def resource(self, kind, name, content, data):
        rid = digest(kind, name, content, data.get("dependency_ids", [])) if kind == "ontology" and data.get("dependency_ids") else digest(kind, name, content)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO resources(id,kind,name,content,data) VALUES(?,?,?,?,?)",
                       (rid,kind,name,content,dump(data)))
        return rid

    def ontology_dependencies(self, ids):
        """Load selected local TTL closure; never download arbitrary imports."""
        pending, seen, texts = list(ids), set(), []
        with self.connect() as db:
            db.execute("BEGIN")
            while pending:
                rid = pending.pop()
                if rid in seen:
                    continue
                row = db.execute("SELECT content,data FROM resources WHERE id=? AND kind='ontology'",(rid,)).fetchone()
                if row is None:
                    raise ValueError("Unknown ontology dependency: " + rid)
                seen.add(rid)
                texts.append(row["content"])
                pending.extend(json.loads(row["data"]).get("dependency_ids",[]))
            if sum(map(len,texts)) > 8*1024*1024:
                raise ValueError("Ontology dependency closure exceeds size limit")
        return texts

    def resources(self, kind):
        with self.connect() as db:
            return [{**dict(r), "data": json.loads(r["data"])} for r in db.execute(
                "SELECT id,name,kind,data,enabled,created_at FROM resources WHERE kind=? ORDER BY created_at DESC", (kind,))]

    def profile(self, name, ontology_id, prompt_id):
        pid = digest(name, ontology_id, prompt_id)
        with self.connect() as db:
            for rid, kind in ((ontology_id,"ontology"),(prompt_id,"prompt")):
                if not db.execute("SELECT 1 FROM resources WHERE id=? AND kind=?",(rid,kind)).fetchone():
                    raise ValueError("Unknown " + kind)
            from .schema import applies
            schema = json.loads(db.execute("SELECT data FROM resources WHERE id=?", (ontology_id,)).fetchone()[0])
            prompt = json.loads(db.execute("SELECT data FROM resources WHERE id=?", (prompt_id,)).fetchone()[0])
            for cls, uri in prompt.get("identity_properties", {}).items():
                prop = schema["properties"].get(uri)
                if not prop or cls not in schema["classes"] or not applies(schema, cls, prop["domain"]):
                    raise ValueError("Identity property is not allowed on class: " + cls)
            db.execute("INSERT OR IGNORE INTO profiles(id,name,ontology_id,prompt_id) VALUES(?,?,?,?)",
                       (pid,name,ontology_id,prompt_id))
        return pid

    def profiles(self, enabled_only=False):
        with self.connect() as db:
            sql = """SELECT p.*,o.data AS schema,m.data AS prompt FROM profiles p
                JOIN resources o ON o.id=p.ontology_id JOIN resources m ON m.id=p.prompt_id"""
            if enabled_only:
                sql += " WHERE p.enabled=1 AND o.enabled=1 AND m.enabled=1"
            return [{**dict(r), "schema": json.loads(r["schema"]), "prompt": json.loads(r["prompt"])}
                    for r in db.execute(sql+" ORDER BY p.name,p.id")]

    def set_enabled(self, table, id, enabled):
        if table not in {"profiles","resources"}:
            raise ValueError("Invalid table")
        with self.connect() as db:
            if not db.execute(f"UPDATE {table} SET enabled=? WHERE id=?", (int(enabled),id)).rowcount:
                raise KeyError(id)

    def new_document(self, filename, source, options, snapshot, did=None, prepared=None, artifacts=None):
        did = did or uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO documents(id,filename,source,options,snapshot,status) VALUES(?,?,?,?,?,?)",
                       (did,filename,source,dump(options),dump(snapshot),"QUEUED"))
            if prepared is not None:
                db.execute("INSERT INTO prepared_chunks VALUES(?,?)",(did,dump(prepared)))
            if artifacts is not None:
                db.execute("INSERT INTO document_artifacts VALUES(?,?)",(did,artifacts))
        return did

    def save_artifacts(self,did,bundle):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO document_artifacts VALUES(?,?)",(did,bundle))

    def artifacts(self,did):
        with self.connect() as db:
            row=db.execute("SELECT bundle FROM document_artifacts WHERE doc_id=?",(did,)).fetchone()
            return row[0] if row else None

    def evidence_chunk(self,cid):
        with self.connect() as db:
            row=db.execute("SELECT c.id,c.ord,c.data,c.doc_id,d.filename FROM chunks c JOIN documents d ON d.id=c.doc_id WHERE c.id=?",(cid,)).fetchone()
            if not row: raise KeyError(cid)
            return {**dict(row),"data":json.loads(row["data"])}

    def save_prepared_chunks(self,did,chunks):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO prepared_chunks VALUES(?,?)",(did,dump(chunks)))

    def document_exists(self,did):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM documents WHERE id=?",(did,)).fetchone() is not None

    def prepared_chunks(self,did):
        with self.connect() as db:
            row=db.execute("SELECT data FROM prepared_chunks WHERE doc_id=?",(did,)).fetchone()
            return json.loads(row[0]) if row else None

    def document(self, did):
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
            if row is None: raise KeyError(did)
            return {**dict(row), "options":json.loads(row["options"]), "snapshot":json.loads(row["snapshot"])}

    def documents(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,filename,status,error,created_at FROM documents ORDER BY created_at DESC,id")]

    def status(self, did, status, error=None):
        with self.connect() as db:
            db.execute("UPDATE documents SET status=?,error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status,error,did))

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE documents SET status='FAILED',error='Interrupted; retry document',updated_at=CURRENT_TIMESTAMP WHERE status IN ('QUEUED','PROCESSING')")

    def retry(self, did):
        with self.connect() as db:
            if not db.execute("UPDATE documents SET status='QUEUED',error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status NOT IN ('PROCESSING','QUEUED')",(did,)).rowcount:
                raise ValueError("Document missing or already processing")

    @staticmethod
    def _prune(db):
        db.execute("DELETE FROM relationships WHERE NOT EXISTS(SELECT 1 FROM relation_sources s WHERE s.relationship_id=relationships.id)")
        db.execute("DELETE FROM entities WHERE NOT EXISTS(SELECT 1 FROM entity_sources s WHERE s.entity_id=entities.id)")

    def delete_document(self, did):
        self.delete_documents([did])

    def delete_documents(self, ids=None):
        """All-or-nothing deletion; source bytes, sidecars and evidence cascade."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if ids is None:
                ids=[r[0] for r in db.execute("SELECT id FROM documents")]
            ids=list(dict.fromkeys(ids))
            for did in ids:
                row=db.execute("SELECT status FROM documents WHERE id=?",(did,)).fetchone()
                if not row or row[0] in ("QUEUED","PROCESSING"):
                    raise ValueError("Document missing or still processing")
            db.executemany("DELETE FROM documents WHERE id=?",[(did,) for did in ids])
            self._prune(db)
            return len(ids)

    def clear_cache(self):
        with self.connect() as db:
            db.execute("DELETE FROM llm_cache")

    @staticmethod
    def has_errors(record):
        return bool(record.get("errors") or record.get("cross_errors") or record.get("assertion_errors")) or any(e.get("errors") for e in record.get("extractions", []))

    def retry_input(self, did):
        """Existing chunks are the canonical retry input, not a reparse with drifting boundaries."""
        with self.connect() as db:
            rows = list(db.execute("SELECT ord,result FROM chunks WHERE doc_id=? ORDER BY ord",(did,)))
            diagnostics = {r["ord"] for r in db.execute("SELECT ord FROM retry_diagnostics WHERE doc_id=?",(did,))}
            records = [json.loads(r["result"]) for r in rows]
            failed = [i for i,r in enumerate(records) if self.has_errors(r) or i in diagnostics]
            return records, failed

    def prepare_retry(self, previous, updates):
        """Select valid new chunks, retaining old facts for failed replacements."""
        merged = list(previous)
        diagnostics = {}
        for i, record in updates.items():
            if self.has_errors(record):
                diagnostics[i] = record
            else:
                merged[i] = record
        return merged, diagnostics

    def commit_retry(self, did, previous, updates, profiles):
        """Replace successful retries only; preserve previous assertions on failed retries."""
        merged, diagnostics = self.prepare_retry(previous,updates)
        self.commit(did, merged, profiles, diagnostics=diagnostics)

    def commit(self, did, records, profiles, diagnostics=None):
        """Replace one document atomically; stable identities use only configured key properties."""
        by_id = {p["id"]:p for p in profiles}
        diagnostics = diagnostics or {}
        partial = bool(diagnostics)
        with self.connect() as db:
            db.execute("DELETE FROM retry_diagnostics WHERE doc_id=?", (did,))
            db.executemany("INSERT INTO retry_diagnostics VALUES(?,?,?)",
                           [(did,i,dump(r)) for i,r in diagnostics.items()])
            db.execute("DELETE FROM prepared_chunks WHERE doc_id=?",(did,))
            db.execute("DELETE FROM chunks WHERE doc_id=?", (did,))
            for order, rec in enumerate(records):
                cid = digest(did, order)
                db.execute("INSERT INTO chunks VALUES(?,?,?,?,?)",(cid,did,order,dump(rec["chunk"]),dump(rec)))
                partial |= bool(rec["errors"] or rec.get("assertion_errors"))
                for ex in rec["extractions"]:
                    pid = ex["profile_id"]
                    partial |= bool(ex["errors"])
                    mapping = {}
                    for e in ex["entities"]:
                        cls = e["class_uri"]
                        eid = entity_id(did,order,by_id[pid],e)
                        mapping[e["local_id"]] = eid
                        db.execute("INSERT OR IGNORE INTO entities VALUES(?,?,?)",(eid,cls,e["name"]))
                        db.execute("INSERT OR IGNORE INTO entity_sources VALUES(?,?,?,?,?)",(eid,cid,pid,e["name"],e["evidence"]))
                        for a in e["attributes"]:
                            aid = digest(eid,cid,pid,a)
                            db.execute("INSERT OR IGNORE INTO attributes VALUES(?,?,?,?,?,?,?,?)",
                                       (aid,eid,a["property_uri"],dump(a["value"]),a["datatype"],cid,pid,a["evidence"]))
                    for r in ex["relationships"]:
                        source,target = mapping[r["source"]],mapping[r["target"]]
                        rid = digest(source,r["predicate_uri"],target)
                        db.execute("INSERT OR IGNORE INTO relationships VALUES(?,?,?,?)",(rid,source,r["predicate_uri"],target))
                        db.execute("INSERT OR IGNORE INTO relation_sources VALUES(?,?,?,?)",(rid,cid,pid,r["evidence"]))
            # All local entities must exist before inserting document-level edges.
            # Resolve only endpoints contributed by this very document.
            known = {r[0] for r in db.execute("""SELECT DISTINCT s.entity_id
                FROM entity_sources s JOIN chunks c ON c.id=s.chunk_id WHERE c.doc_id=?""",(did,))}
            from .schema import applies
            for order, rec in enumerate(records):
                cid = digest(did,order)
                partial |= bool(rec.get("cross_errors"))
                for r in rec.get("cross_relationships",[]):
                    source,target,pid = r["source"],r["target"],r["profile_id"]
                    schema = by_id[pid]["schema"]
                    pred = schema["relationships"].get(r["predicate_uri"])
                    if source not in known or target not in known or not pred:
                        raise ValueError("Invalid cross-chunk endpoint or predicate")
                    types = {x["id"]:x["class_uri"] for x in db.execute(
                        "SELECT id,class_uri FROM entities WHERE id IN (?,?)",(source,target))}
                    if not applies(schema,types[source],pred["domain"]) or not applies(schema,types[target],pred["range"]):
                        raise ValueError("Cross-chunk relation violates schema")
                    if not r["evidence"].strip() or r["evidence"] not in rec["chunk"]["content"]:
                        raise ValueError("Cross-chunk relation lacks evidence")
                    rid = digest(source,r["predicate_uri"],target)
                    db.execute("INSERT OR IGNORE INTO relationships VALUES(?,?,?,?)",(rid,source,r["predicate_uri"],target))
                    db.execute("INSERT OR IGNORE INTO relation_sources VALUES(?,?,?,?)",(rid,cid,pid,r["evidence"]))
            self._prune(db)
            all_failed = bool(records) and all(
                r.get("errors") or (r.get("extractions") and all(
                    e.get("errors") and not e.get("entities") for e in r["extractions"]))
                for r in records)
            state = "FAILED" if all_failed else "PARTIAL" if partial else "COMPLETED"
            db.execute("UPDATE documents SET status=?,error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                       (state,"All chunks failed; see extraction details" if all_failed else None,did))

    def chunks(self, did):
        with self.connect() as db:
            db.execute("BEGIN")
            diagnostics = {r["ord"]:json.loads(r["result"]) for r in db.execute(
                "SELECT ord,result FROM retry_diagnostics WHERE doc_id=?", (did,))}
            return [{"id":r["id"], **json.loads(r["result"]),
                     "last_retry":diagnostics.get(r["ord"])} for r in db.execute(
                "SELECT id,ord,result FROM chunks WHERE doc_id=? ORDER BY ord",(did,))]

    def chunk_page(self,did,offset=0,limit=20):
        with self.connect() as db:
            db.execute("BEGIN")
            total=db.execute("SELECT COUNT(*) FROM chunks WHERE doc_id=?",(did,)).fetchone()[0]
            rows=db.execute("""SELECT c.id,c.ord,c.result,d.result AS diagnostic
                FROM chunks c LEFT JOIN retry_diagnostics d ON d.doc_id=c.doc_id AND d.ord=c.ord
                WHERE c.doc_id=? ORDER BY c.ord LIMIT ? OFFSET ?""",(did,limit,offset))
            return {"total":total,"offset":offset,"limit":limit,"chunks":[
                {"id":r["id"],**json.loads(r["result"]),"last_retry":json.loads(r["diagnostic"]) if r["diagnostic"] else None}
                for r in rows]}

    def export(self, did=None):
        """Export valid instances only, with assertions and source provenance."""
        filt = " WHERE c.doc_id=?" if did else ""
        args = (did,) if did else ()
        with self.connect() as db:
            db.execute("BEGIN")
            from .evidence import evidence_view
            evidence_rows={}
            def provenance(row):
                cid=row["chunk_id"]
                if cid not in evidence_rows:
                    q=db.execute("SELECT c.id,c.ord,c.data,c.doc_id,d.filename FROM chunks c JOIN documents d ON d.id=c.doc_id WHERE c.id=?",(cid,)).fetchone()
                    evidence_rows[cid]={**dict(q),"data":json.loads(q["data"])}
                view=evidence_view(evidence_rows[cid],row["evidence"])
                return {k:view[k] for k in ("filename","chunk_order_index","matches","heading","evidence_kind","sidecar","parse_engine")}
            entities = {}
            for r in db.execute("""SELECT e.id,e.class_uri,s.name,s.evidence,s.chunk_id,s.profile_id,c.doc_id
                FROM entity_sources s JOIN entities e ON e.id=s.entity_id JOIN chunks c ON c.id=s.chunk_id"""+filt,args):
                e = entities.setdefault(r["id"],{"id":r["id"],"class_uri":r["class_uri"],"name":r["name"],"attributes":[],"sources":[]})
                e["sources"].append({k:r[k] for k in ("name","evidence","chunk_id","profile_id","doc_id")}|{"provenance":provenance(r)})
            for r in db.execute("SELECT a.*,c.doc_id FROM attributes a JOIN chunks c ON c.id=a.chunk_id"+filt,args):
                if r["entity_id"] in entities:
                    entities[r["entity_id"]]["attributes"].append({**dict(r),"value":json.loads(r["value"]),"provenance":provenance(r)})
            rels = {}
            for r in db.execute("""SELECT r.*,s.chunk_id,s.profile_id,s.evidence,c.doc_id FROM relation_sources s
                JOIN relationships r ON r.id=s.relationship_id JOIN chunks c ON c.id=s.chunk_id"""+filt,args):
                rel = rels.setdefault(r["id"],{k:r[k] for k in ("id","source","predicate_uri","target")})
                rel.setdefault("sources",[]).append({k:r[k] for k in ("chunk_id","profile_id","evidence","doc_id")}|{"provenance":provenance(r)})
            return {"entities":list(entities.values()),"relationships":list(rels.values())}
