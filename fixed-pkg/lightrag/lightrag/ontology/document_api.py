"""Read adapter for original React DocumentManager with SQL-side pagination."""
import json


def document_page(store,body,phases):
    try:
        page=max(1,int(body.get("page",1)))
        size=max(1,min(200,int(body.get("page_size",10))))
    except (TypeError,ValueError):
        page,size=1,10
    filters=body.get("status_filters") or ([body["status_filter"]] if body.get("status_filter") else [])
    if not isinstance(filters,list) or any(x not in {"pending","parsing","analyzing","processing","processed","failed","preprocessed"} for x in filters):
        raise ValueError("Invalid status filters")
    field={"id":"id","file_path":"filename","created_at":"created_at","updated_at":"updated_at"}.get(body.get("sort_field"),"created_at")
    direction="ASC" if body.get("sort_direction")=="asc" else "DESC"
    with store.connect() as db:
        db.execute("BEGIN")
        db.execute("CREATE TEMP TABLE live_phase(id TEXT PRIMARY KEY, phase TEXT, updated_at TEXT)")
        db.executemany("INSERT INTO live_phase VALUES(?,?,?)",[(k,v.get("phase"),v.get("updated_at")) for k,v in phases.items()])
        source="""SELECT d.id,d.filename,d.created_at,COALESCE(p.updated_at,d.updated_at,d.created_at) AS updated_at,
               d.status,d.error,d.options,length(d.source) AS content_length,
               CASE WHEN d.status='QUEUED' THEN 'pending'
                    WHEN d.status='COMPLETED' THEN 'processed'
                    WHEN d.status='PROCESSING' AND p.phase='PARSING' THEN 'parsing'
                    WHEN d.status='PROCESSING' AND p.phase='ANALYZING' THEN 'analyzing'
                    WHEN d.status='PROCESSING' THEN 'processing' ELSE 'failed' END AS ui_status
               FROM documents d LEFT JOIN live_phase p ON p.id=d.id"""
        cte="WITH docs AS ("+source+") "
        counts={r[0]:r[1] for r in db.execute(cte+"SELECT ui_status,COUNT(*) FROM docs GROUP BY ui_status")}
        where=" WHERE ui_status IN ("+",".join("?" for _ in filters)+")" if filters else ""
        count=db.execute(cte+"SELECT COUNT(*) FROM docs"+where,filters).fetchone()[0]
        total=(count+size-1)//size
        rows=db.execute(cte+"SELECT docs.*,(SELECT COUNT(*) FROM chunks c WHERE c.doc_id=docs.id) AS chunks_count FROM docs"+where+
                        f" ORDER BY {field} {direction},id {direction} LIMIT ? OFFSET ?",filters+[size,(page-1)*size])
        docs=[]
        for r in rows:
            info=phases.get(r["id"],{})
            docs.append({"id":r["id"],"file_path":r["filename"],"content_summary":r["filename"],
                         "content_length":r["content_length"],"status":r["ui_status"],"created_at":r["created_at"],
                         "updated_at":r["updated_at"],"chunks_count":r["chunks_count"],
                         "error_msg":r["error"] or ("Partial extraction; review validation errors" if r["status"]=="PARTIAL" else ""),
                         "metadata":{"gptonto_status":r["status"],"phase":info,"options":json.loads(r["options"])}})
        return {"documents":docs,"pagination":{"page":page,"page_size":size,"total_count":count,
                 "total_pages":total,"has_next":page<total,"has_prev":page>1},"status_counts":counts}
