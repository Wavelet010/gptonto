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


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
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
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                ord INTEGER NOT NULL, data TEXT NOT NULL, result TEXT NOT NULL, UNIQUE(doc_id,ord)
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
            CREATE INDEX IF NOT EXISTS ix_entity_sources_chunk ON entity_sources(chunk_id);
            CREATE INDEX IF NOT EXISTS ix_attrs_entity ON attributes(entity_id);
            CREATE INDEX IF NOT EXISTS ix_attrs_chunk ON attributes(chunk_id);
            CREATE INDEX IF NOT EXISTS ix_rel_source ON relationships(source);
            CREATE INDEX IF NOT EXISTS ix_rel_target ON relationships(target);
            CREATE INDEX IF NOT EXISTS ix_relation_sources_chunk ON relation_sources(chunk_id);
            """)

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
        rid = digest(kind, name, content)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO resources(id,kind,name,content,data) VALUES(?,?,?,?,?)",
                       (rid,kind,name,content,dump(data)))
        return rid

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

    def new_document(self, filename, source, options, snapshot):
        did = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO documents(id,filename,source,options,snapshot,status) VALUES(?,?,?,?,?,?)",
                       (did,filename,source,dump(options),dump(snapshot),"QUEUED"))
        return did

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
            db.execute("UPDATE documents SET status=?,error=? WHERE id=?", (status,error,did))

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE documents SET status='FAILED',error='Interrupted; retry document' WHERE status IN ('QUEUED','PROCESSING')")

    def retry(self, did):
        with self.connect() as db:
            if not db.execute("UPDATE documents SET status='QUEUED',error=NULL WHERE id=? AND status NOT IN ('PROCESSING','QUEUED')",(did,)).rowcount:
                raise ValueError("Document missing or already processing")

    @staticmethod
    def _prune(db):
        db.execute("DELETE FROM relationships WHERE NOT EXISTS(SELECT 1 FROM relation_sources s WHERE s.relationship_id=relationships.id)")
        db.execute("DELETE FROM entities WHERE NOT EXISTS(SELECT 1 FROM entity_sources s WHERE s.entity_id=entities.id)")

    def delete_document(self, did):
        with self.connect() as db:
            if not db.execute("DELETE FROM documents WHERE id=? AND status NOT IN ('QUEUED','PROCESSING')",(did,)).rowcount:
                raise ValueError("Document missing or still processing")
            self._prune(db)

    def commit(self, did, records, profiles):
        """Replace one document atomically; stable identities use only configured key properties."""
        by_id = {p["id"]:p for p in profiles}
        partial = False
        with self.connect() as db:
            db.execute("DELETE FROM chunks WHERE doc_id=?", (did,))
            for order, rec in enumerate(records):
                cid = digest(did, order)
                db.execute("INSERT INTO chunks VALUES(?,?,?,?,?)",(cid,did,order,dump(rec["chunk"]),dump(rec)))
                partial |= bool(rec["errors"])
                for ex in rec["extractions"]:
                    pid = ex["profile_id"]
                    partial |= bool(ex["errors"])
                    keys = by_id[pid]["prompt"].get("identity_properties",{})
                    mapping = {}
                    for e in ex["entities"]:
                        cls = e["class_uri"]
                        key = keys.get(cls)
                        vals = {dump(a["value"]) for a in e["attributes"] if a["property_uri"] == key}
                        # No name-only merging. With no explicit unique key, use chunk-local identity.
                        eid = digest(cls,key,next(iter(vals))) if key and len(vals)==1 else digest(cid,pid,e["local_id"])
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
            self._prune(db)
            db.execute("UPDATE documents SET status=?,error=NULL WHERE id=?",("PARTIAL" if partial else "COMPLETED",did))

    def chunks(self, did):
        with self.connect() as db:
            return [{"id":r["id"], **json.loads(r["result"])} for r in db.execute(
                "SELECT id,result FROM chunks WHERE doc_id=? ORDER BY ord",(did,))]

    def export(self, did=None):
        """Export valid instances only, with assertions and source provenance."""
        filt = " WHERE c.doc_id=?" if did else ""
        args = (did,) if did else ()
        with self.connect() as db:
            entities = {}
            for r in db.execute("""SELECT e.id,e.class_uri,s.name,s.evidence,s.chunk_id,s.profile_id,c.doc_id
                FROM entity_sources s JOIN entities e ON e.id=s.entity_id JOIN chunks c ON c.id=s.chunk_id"""+filt,args):
                e = entities.setdefault(r["id"],{"id":r["id"],"class_uri":r["class_uri"],"name":r["name"],"attributes":[],"sources":[]})
                e["sources"].append({k:r[k] for k in ("name","evidence","chunk_id","profile_id","doc_id")})
            for r in db.execute("SELECT a.*,c.doc_id FROM attributes a JOIN chunks c ON c.id=a.chunk_id"+filt,args):
                if r["entity_id"] in entities:
                    entities[r["entity_id"]]["attributes"].append({**dict(r),"value":json.loads(r["value"])})
            rels = {}
            for r in db.execute("""SELECT r.*,s.chunk_id,s.profile_id,s.evidence,c.doc_id FROM relation_sources s
                JOIN relationships r ON r.id=s.relationship_id JOIN chunks c ON c.id=s.chunk_id"""+filt,args):
                rel = rels.setdefault(r["id"],{k:r[k] for k in ("id","source","predicate_uri","target")})
                rel.setdefault("sources",[]).append({k:r[k] for k in ("chunk_id","profile_id","evidence","doc_id")})
            return {"entities":list(entities.values()),"relationships":list(rels.values())}
