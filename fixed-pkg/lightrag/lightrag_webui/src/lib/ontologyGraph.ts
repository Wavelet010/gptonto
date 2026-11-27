export const ontologyGraphMode = import.meta.env.MODE === 'gptonto'
let accessKey = ''
let documentId = ''
export function configureOntologyGraph(key: string, doc: string) {
  accessKey = key
  documentId = doc
}
export async function ontologyGraphRequest<T>(path: string, params: Record<string, string | number> = {}): Promise<T> {
  const url = new URL('../ontology-api/' + path, window.location.href)
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, String(value))
  if (documentId && path === 'graph') url.searchParams.set('document_id', documentId)
  const response = await fetch(url, {headers: accessKey ? {Authorization: 'Bearer ' + accessKey} : {}})
  if (!response.ok) throw new Error('GPTOnto API: ' + response.status)
  return response.json()
}
