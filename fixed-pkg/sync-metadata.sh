#!/bin/bash
# Nightly sync: write Milvus metadata back to JSON KV store
API="http://localhost:9621"
DOCS=$(curl -s "$API/documents/paginated" -H "Content-Type: application/json" \
  -d '{"page":1,"page_size":100}' | python3 -c "
import sys,json
d=json.load(sys.stdin)
for doc in d.get('documents',[]):
    print(doc['id'])
")
for doc_id in $DOCS; do
  result=$(curl -s -X POST "$API/documents/sync-metadata/$doc_id")
  synced=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('synced',0))")
  echo "$(date '+%Y-%m-%d %H:%M') sync $doc_id: $synced chunks"
done
