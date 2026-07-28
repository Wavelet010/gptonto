#!/bin/bash
# Nightly sync: write Milvus metadata back to JSON KV store
# Usage: ./sync-metadata.sh [page_size]
set -e

API="${LIGHTRAG_API_URL:-http://localhost:9621}"
PAGE_SIZE="${1:-100}"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') Starting metadata sync ==="

PAGE=1
TOTAL_SYNCED=0

while true; do
  RESP=$(curl -s "$API/documents/paginated" \
    -H "Content-Type: application/json" \
    -d "{\"page\":$PAGE,\"page_size\":$PAGE_SIZE}")

  # Extract doc IDs from current page
  DOCS=$(echo "$RESP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
docs = d.get('documents', [])
for doc in docs:
    print(doc.get('id', ''))
")

  # Check if there are more pages
  HAS_NEXT=$(echo "$RESP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('true' if d.get('pagination', {}).get('has_next') else 'false')
")

  if [ -z "$DOCS" ]; then
    echo "No documents found on page $PAGE, stopping."
    break
  fi

  for doc_id in $DOCS; do
    result=$(curl -s -X POST "$API/documents/sync-metadata/$doc_id")
    synced=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('synced',0))")
    echo "$(date '+%Y-%m-%d %H:%M:%S') sync $doc_id: $synced chunks"
    TOTAL_SYNCED=$((TOTAL_SYNCED + synced))
  done

  if [ "$HAS_NEXT" != "true" ]; then
    break
  fi
  PAGE=$((PAGE + 1))
done

echo "=== $(date '+%Y-%m-%d %H:%M:%S') Sync complete, total synced: $TOTAL_SYNCED chunks ==="
