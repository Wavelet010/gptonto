"""Read adapter for the original React DocumentManager's pagination contract."""
import json
from collections import Counter


def document_page(store,body,phases):
    try:
        page=max(1,int(body.get("page",1)))
        size=max(1,min(200,int(body.get("page_size",10))))
    except (TypeError,ValueError):
        page,size=1,10
    mapping={"QUEUED":"pending","PROCESSING":"processing","COMPLETED":"processed",
             "FAILED":"failed","PARTIAL":"failed","CANCELLED":"failed"}
    def state(row):
        phase=phases.get(row["id"],{}).get("phase")
        if row["status"]=="PROCESSING":
            return {"PARSING":"parsing","ANALYZING":"analyzing"}.get(phase,"processing")
        return mapping.get(row["status"],"failed")
    with store.connect() as db:
        db.execute("BEGIN")
        rows=[dict(r) for r in db.execute("""SELECT d.id,d.filename,d.created_at,d.status,d.error,
                length(d.source) AS content_length,d.options,
                (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id) AS chunks_count
                FROM documents d""")]
    counts=Counter(state(r) for r in rows)
    filters=body.get("status_filters") or ([body["status_filter"]] if body.get("status_filter") else [])
    if filters: rows=[r for r in rows if state(r) in filters]
    field={"id":"id","file_path":"filename","created_at":"created_at","updated_at":"created_at"}.get(body.get("sort_field"),"created_at")
    rows.sort(key=lambda r:(phases.get(r["id"],{}).get("updated_at",r[field]) if body.get("sort_field")=="updated_at" else r[field],r["id"]),reverse=body.get("sort_direction","desc")!="asc")
    count=len(rows); total=(count+size-1)//size
    docs=[]
    for r in rows[(page-1)*size:page*size]:
        info=phases.get(r["id"],{})
        docs.append({"id":r["id"],"file_path":r["filename"],"content_summary":r["filename"],
                     "content_length":r["content_length"],"status":state(r),"created_at":r["created_at"],
                     "updated_at":info.get("updated_at",r["created_at"]),"chunks_count":r["chunks_count"],
                     "error_msg":r["error"] or ("部分抽取存在校验错误" if r["status"]=="PARTIAL" else ""),
                     "metadata":{"gptonto_status":r["status"],"phase":info,"options":json.loads(r["options"])}})
    return {"documents":docs,"pagination":{"page":page,"page_size":size,"total_count":count,
             "total_pages":total,"has_next":page<total,"has_prev":page>1},"status_counts":dict(counts)}
