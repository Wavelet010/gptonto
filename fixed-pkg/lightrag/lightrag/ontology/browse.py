"""Bounded instance browsing. Export remains a separate, full-document operation."""
import json


def browse_instances(store, document_id=None, q="", class_uri=None, offset=0, limit=50, focus=None):
    with store.connect() as db:
        db.execute("BEGIN")
        source_where = " WHERE c.doc_id=?" if document_id else ""
        source_args = [document_id] if document_id else []
        # Query current source names, not stale entity display names.
        where, args = [], list(source_args)
        if q:
            where.append("(instr(lower(s.name),lower(?))>0 OR instr(lower(e.id),lower(?))>0 OR EXISTS(SELECT 1 FROM attributes a WHERE a.entity_id=e.id AND a.chunk_id=s.chunk_id AND instr(lower(a.value),lower(?))>0))")
            args.extend([q,q,q])
        if class_uri:
            where.append("e.class_uri=?")
            args.append(class_uri)
        if focus:
            # Restrict adjacency to the selected document when scoped.
            rel_doc = " AND c2.doc_id=?" if document_id else ""
            where.append("""(e.id=? OR EXISTS(SELECT 1 FROM relationships r
                JOIN relation_sources rs ON rs.relationship_id=r.id JOIN chunks c2 ON c2.id=rs.chunk_id
                WHERE ((r.source=? AND r.target=e.id) OR (r.target=? AND r.source=e.id))"""+rel_doc+"))")
            args.extend([focus,focus,focus])
            if document_id: args.append(document_id)
        base = """ FROM entity_sources s JOIN entities e ON e.id=s.entity_id
            JOIN chunks c ON c.id=s.chunk_id"""+source_where
        if where:
            base += (" AND " if source_where else " WHERE ")+" AND ".join(where)
        total = db.execute("SELECT COUNT(DISTINCT e.id)"+base,args).fetchone()[0]
        rows = list(db.execute("SELECT e.id,e.class_uri,MIN(s.name) AS name"+base+
            " GROUP BY e.id,e.class_uri ORDER BY "+("CASE WHEN e.id=? THEN 0 ELSE 1 END," if focus else "")+
            " name COLLATE NOCASE,e.id LIMIT ? OFFSET ?",args+([focus] if focus else [])+[limit,offset]))
        entities = {r["id"]:{**dict(r),"attributes":[],"sources":[]} for r in rows}
        classes = [r[0] for r in db.execute("SELECT DISTINCT e.class_uri FROM entity_sources s JOIN entities e ON e.id=s.entity_id JOIN chunks c ON c.id=s.chunk_id"+source_where+" ORDER BY e.class_uri",source_args)]
        if not entities:
            return {"entities":[],"relationships":[],"total":total,"offset":offset,"limit":limit,"classes":classes}
        marks = ",".join("?" for _ in entities)
        scope = " AND c.doc_id=?" if document_id else ""
        args = list(entities)+source_args
        for r in db.execute("SELECT s.*,c.doc_id FROM entity_sources s JOIN chunks c ON c.id=s.chunk_id WHERE s.entity_id IN ("+marks+")"+scope,args):
            entities[r["entity_id"]]["sources"].append(dict(r))
        for r in db.execute("SELECT a.*,c.doc_id FROM attributes a JOIN chunks c ON c.id=a.chunk_id WHERE a.entity_id IN ("+marks+")"+scope,args):
            entities[r["entity_id"]]["attributes"].append({**dict(r),"value":json.loads(r["value"])})
        rels = {}
        for r in db.execute("""SELECT r.*,s.chunk_id,s.profile_id,s.evidence,c.doc_id FROM relationships r
            JOIN relation_sources s ON s.relationship_id=r.id JOIN chunks c ON c.id=s.chunk_id
            WHERE r.source IN ("""+marks+") AND r.target IN ("+marks+")"+scope,list(entities)*2+source_args):
            rel = rels.setdefault(r["id"],{k:r[k] for k in ("id","source","predicate_uri","target")})
            rel.setdefault("sources",[]).append({k:r[k] for k in ("chunk_id","profile_id","evidence","doc_id")})
        return {"entities":list(entities.values()),"relationships":list(rels.values()),"total":total,
                "offset":offset,"limit":limit,"classes":classes}
