"""Read-only LightRAG graph contract over the ontology assertion store.

Use stable instance IDs, preserve direction and parallel predicate edges.
Never materialize the complete export just to show a bounded neighborhood.
"""
import json
import re


def graph_labels(store,q="",limit=100):
    with store.connect() as db:
        rows=db.execute("""SELECT e.id,MIN(s.name) AS name FROM entities e
            JOIN entity_sources s ON s.entity_id=e.id
            WHERE instr(lower(s.name),lower(?))>0 OR instr(lower(e.id),lower(?))>0
               OR EXISTS(SELECT 1 FROM attributes a WHERE a.entity_id=e.id AND instr(lower(a.value),lower(?))>0)
            GROUP BY e.id ORDER BY name,e.id LIMIT ?""",(q,q,q,limit))
        return [f'{r["name"]} [{r["id"]}]' for r in rows]


def graph_data(store,label="*",max_depth=2,max_nodes=500,document_id=None):
    """Bounded undirected traversal of directed relations; output keeps orientation."""
    max_nodes=max(1,min(1000,int(max_nodes)))
    max_depth=max(1,min(8,int(max_depth)))
    with store.connect() as db:
        db.execute("BEGIN")
        scope = " AND c.doc_id=?" if document_id else ""
        scope_args = [document_id] if document_id else []
        # Temp tables avoid SQLite parameter limits, including on older builds.
        db.execute("CREATE TEMP TABLE selected(id TEXT PRIMARY KEY)")
        db.execute("CREATE TEMP TABLE frontier(id TEXT PRIMARY KEY)")
        match = re.search(r"\[([0-9a-f]{64})\]$",label)
        key = match.group(1) if match else label
        query = """SELECT e.id FROM entities e JOIN entity_sources s ON s.entity_id=e.id
            JOIN chunks c ON c.id=s.chunk_id WHERE """
        args=[]
        if label=="*":
            query+="1=1"
        elif match or re.fullmatch(r"[0-9a-f]{64}",key):
            query+="e.id=?"; args=[key]
        else:
            query+="instr(lower(s.name),lower(?))>0"; args=[key]
        seeds=[r[0] for r in db.execute(query+scope+" GROUP BY e.id ORDER BY e.id LIMIT ?",args+scope_args+[max_nodes])]
        db.executemany("INSERT INTO selected VALUES(?)",[(i,) for i in seeds])
        db.executemany("INSERT INTO frontier VALUES(?)",[(i,) for i in seeds])
        remaining=max_nodes-len(seeds)
        if label!="*":
            for _ in range(max_depth):
                if remaining<=0: break
                neighbors=[r[0] for r in db.execute("""SELECT DISTINCT n.id FROM (
                    SELECT r.target AS id,r.id AS rid FROM relationships r JOIN frontier f ON r.source=f.id
                    UNION ALL
                    SELECT r.source AS id,r.id AS rid FROM relationships r JOIN frontier f ON r.target=f.id
                    ) n JOIN relation_sources rs ON rs.relationship_id=n.rid JOIN chunks c ON c.id=rs.chunk_id
                    WHERE NOT EXISTS(SELECT 1 FROM selected s WHERE s.id=n.id)"""+scope+
                    " ORDER BY n.id LIMIT ?",scope_args+[remaining])]
                if not neighbors: break
                db.execute("DELETE FROM frontier")
                db.executemany("INSERT INTO frontier VALUES(?)",[(i,) for i in neighbors])
                db.executemany("INSERT INTO selected VALUES(?)",[(i,) for i in neighbors])
                remaining-=len(neighbors)
        nodes={}
        for r in db.execute("""SELECT e.id,e.class_uri,s.name,s.evidence,s.chunk_id,s.profile_id,c.doc_id
                FROM selected x JOIN entities e ON e.id=x.id JOIN entity_sources s ON s.entity_id=e.id
                JOIN chunks c ON c.id=s.chunk_id WHERE 1=1"""+scope+" ORDER BY e.id,s.name",scope_args):
            n=nodes.setdefault(r["id"],{"id":r["id"],"labels":[r["name"]],
                "properties":{"entity_id":r["id"],"entity_type":r["class_uri"],"name":r["name"],"attributes":[],"sources":[]}})
            n["properties"]["sources"].append({k:r[k] for k in ("name","evidence","chunk_id","profile_id","doc_id")})
        for r in db.execute("""SELECT a.*,c.doc_id FROM selected x JOIN attributes a ON a.entity_id=x.id
                JOIN chunks c ON c.id=a.chunk_id WHERE 1=1"""+scope,scope_args):
            if r["entity_id"] in nodes:
                nodes[r["entity_id"]]["properties"]["attributes"].append({**dict(r),"value":json.loads(r["value"])})
        edges={}
        for r in db.execute("""SELECT r.*,s.chunk_id,s.profile_id,s.evidence,c.doc_id FROM relationships r
                JOIN selected a ON a.id=r.source JOIN selected b ON b.id=r.target
                JOIN relation_sources s ON s.relationship_id=r.id JOIN chunks c ON c.id=s.chunk_id
                WHERE 1=1"""+scope+" ORDER BY r.id",scope_args):
            if r["source"] not in nodes or r["target"] not in nodes: continue
            edge=edges.setdefault(r["id"],{"id":r["id"],"source":r["source"],"target":r["target"],
                "type":r["predicate_uri"],"properties":{"keywords":r["predicate_uri"].rsplit("#",1)[-1].rsplit("/",1)[-1],
                "predicate_uri":r["predicate_uri"],"weight":1,"sources":[]}})
            edge["properties"]["sources"].append({k:r[k] for k in ("chunk_id","profile_id","evidence","doc_id")})
        return {"nodes":list(nodes.values()),"edges":list(edges.values())}
